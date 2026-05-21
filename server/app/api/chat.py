"""Streaming chat with persistence, context reduction, and memory (Phase 2+3)."""

import asyncio
import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, update

from app.core.config import settings
from app.core.deps import get_current_user
from app.core.limiter import limiter
from app.core.rate_limit import chat_rate_limit
from app.models.episode import Episode
from app.models.user import User
from app.schemas import ChatRequest
from app.services.cache_invalidation import invalidate_user_caches
from app.services.chat_sessions import (
    bump_session_message_count,
    get_chat_session,
    set_session_title,
)
from app.services.compression_errors import CompressionError
from app.services.context_manager import (
    build_attribution_metadata,
    build_completion_messages,
    build_enhanced_context,
    compression_lock,
    count_active_prompt_tokens,
    count_active_transcript_tokens,
    reduce_until_under,
    run_post_turn_compression,
    track_background_task,
)
from app.services.database import open_tenant_session
from app.services.deepseek import ChatCompletionUsage, stream_chat_completion
from app.services.token_quota import (
    assert_chat_allowed,
    assert_preflight_estimated_tokens,
    fill_usage_from_estimate,
    get_user_quota_status,
    maybe_rollover_user,
    record_llm_usage,
)
from app.services.fact_extraction_v2 import (
    fact_extraction_lock,
    maybe_schedule_extractions,
)
from app.services.turn_embedding import embed_new_turns
from app.services.session_titles import generate_session_title

logger = logging.getLogger(__name__)

router = APIRouter()


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _embed_episode_ids(user_id: int, episode_ids: list[int]) -> None:
    from app.services.turn_embedding import embed_episodes_by_ids

    db = open_tenant_session(user_id)
    try:
        await embed_episodes_by_ids(db, user_id, episode_ids)
    except Exception:
        logger.exception("Background turn embedding failed")
    finally:
        db.close()


async def _maybe_extract_facts(
    user_id: int,
    session_id: str,
    user_message_count: int,
    session_message_count: int,
) -> None:
    lock = fact_extraction_lock(user_id)
    async with lock:
        try:
            db = open_tenant_session(user_id)
            try:
                await maybe_schedule_extractions(
                    db,
                    user_id,
                    session_id,
                    user_message_count=user_message_count,
                    session_message_count=session_message_count,
                )
            finally:
                db.close()
        except Exception:
            logger.exception("Background fact extraction failed")


async def _maybe_generate_session_title(
    user_id: int,
    session_id: str,
    first_user_message: str,
) -> None:
    try:
        db = open_tenant_session(user_id)
        try:
            row = get_chat_session(db, user_id, session_id)
            if row is None or row.title_generated_at is not None:
                return
            if row.title and row.title != "New conversation":
                return
            title = await generate_session_title(
                first_user_message,
                db=db,
                user_id=user_id,
                session_id=session_id,
            )
            set_session_title(db, user_id, session_id, title, generated=True)
            db.commit()
        finally:
            db.close()
    except Exception:
        logger.exception("Background session title generation failed")


@router.post("")
@limiter.limit(chat_rate_limit())
async def chat(
    request: Request,
    body: ChatRequest,
    current_user: Annotated[User, Depends(get_current_user)],
) -> StreamingResponse:
    if not settings.deepseek_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="DEEPSEEK_API_KEY is not configured",
        )

    session_id = body.session_id.strip()
    user_id = current_user.id
    user_message = body.message

    quota_db = open_tenant_session(user_id)
    try:
        tier_resolution = assert_chat_allowed(quota_db, current_user)
        chat_model = tier_resolution.model
        chat_tier = tier_resolution.tier
        quota_db.commit()
    finally:
        quota_db.close()

    user_episode_id: int | None = None
    db = open_tenant_session(user_id)
    try:
        user_message_count = db.execute(
            update(User)
            .where(User.id == user_id)
            .values(
                user_message_count=func.coalesce(User.user_message_count, 0) + 1
            )
            .returning(User.user_message_count)
        ).scalar_one_or_none()
        if user_message_count is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )
        db.add(
            Episode(
                user_id=user_id,
                session_id=session_id,
                episode_kind="message",
                role="user",
                content=user_message,
            )
        )
        session_message_count = bump_session_message_count(
            db,
            user_id,
            session_id,
            preview_from=user_message,
        )
        invalidate_user_caches(db, user_id, session_id)
        db.commit()
        user_episode_id = db.scalar(
            select(Episode.id)
            .where(
                Episode.user_id == user_id,
                Episode.session_id == session_id,
                Episode.episode_kind == "message",
                Episode.role == "user",
            )
            .order_by(Episode.created_at.desc(), Episode.id.desc())
            .limit(1)
        )
    finally:
        db.close()

    if user_episode_id is not None:
        track_background_task(
            asyncio.create_task(
                _embed_episode_ids(user_id, [int(user_episode_id)])
            )
        )

    if session_message_count == 1:
        track_background_task(
            asyncio.create_task(
                _maybe_generate_session_title(user_id, session_id, user_message)
            )
        )

    compressed_this_turn = False
    enhanced = None
    lock = compression_lock(user_id, session_id)
    async with lock:
        db = open_tenant_session(user_id)
        try:
            enhanced = await build_enhanced_context(
                db, user_id, session_id, user_message
            )
            transcript_tokens = count_active_transcript_tokens(
                db, user_id, session_id
            )
            packed_tokens = count_active_prompt_tokens(
                db,
                user_id,
                session_id,
                enhanced=enhanced,
                current_query=user_message,
            )
            logger.info(
                "chat_context user_id=%s session_id=%s retrieval_mode=%s "
                "facts_injected=%s cross_session_memories=%s in_session_memories=%s "
                "transcript_tokens=%s packed_tokens=%s compression_triggered=false",
                user_id,
                session_id,
                enhanced.retrieval.mode.value,
                len(enhanced.injected_facts),
                len(enhanced.cross_session_memories),
                len(enhanced.in_session_memories),
                transcript_tokens,
                packed_tokens,
            )
            if transcript_tokens > settings.context_threshold_tokens:
                compressed_this_turn = True
                logger.info(
                    "chat_context user_id=%s session_id=%s compression_triggered=true "
                    "transcript_tokens=%s threshold=%s",
                    user_id,
                    session_id,
                    transcript_tokens,
                    settings.context_threshold_tokens,
                )
                try:
                    await reduce_until_under(
                        db, user_id, session_id, enhanced=enhanced
                    )
                except CompressionError as exc:
                    m = exc.metrics
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail={
                            "error": "context_compression_failed",
                            "message": str(exc),
                            "failure_reason": exc.reason.value,
                            "compression_attempted": m.compression_attempted,
                            "compression_succeeded": m.compression_succeeded,
                            "active_token_count": m.active_token_count,
                            "context_threshold": m.context_threshold,
                            "compression_rounds": m.rounds,
                            "action": (
                                "Configure GEMINI_API_KEY and DEEPSEEK_API_KEY, "
                                "then retry. If memory_paused is true in context status, "
                                "compression is blocked until the issue is resolved."
                            ),
                        },
                    ) from exc
                db.expire_all()
                enhanced = await build_enhanced_context(
                    db, user_id, session_id, user_message
                )
                if (
                    count_active_transcript_tokens(db, user_id, session_id)
                    > settings.context_threshold_tokens
                ):
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail={
                            "error": "context_still_over_threshold",
                            "message": (
                                "Context remains above threshold after compression"
                            ),
                            "failure_reason": "still_over_threshold",
                            "compression_attempted": True,
                            "compression_succeeded": False,
                            "action": "Retry later or start a new session.",
                        },
                    )
            messages = build_completion_messages(
                db,
                user_id,
                session_id,
                enhanced=enhanced,
                current_query=user_message,
            )
            estimated_tokens = count_active_prompt_tokens(
                db,
                user_id,
                session_id,
                enhanced=enhanced,
                current_query=user_message,
            )
            assert_preflight_estimated_tokens(db, current_user, estimated_tokens)
            db.commit()
        finally:
            db.close()

    enhanced_for_persist = enhanced
    retrieval_headers: dict[str, str] = {}
    if enhanced is not None:
        retrieval_headers = {
            "X-Retrieval-Mode": enhanced.retrieval.mode.value,
            "X-Cross-Session-Memory": enhanced.retrieval.cross_session_header_value(),
        }

    async def event_stream():
        if compressed_this_turn:
            yield _sse("compression_started", {"session_id": session_id})

        usage = ChatCompletionUsage()
        assistant_parts: list[str] = []
        try:
            async for piece in stream_chat_completion(
                messages,
                model=chat_model,
                usage_out=usage,
            ):
                if await request.is_disconnected():
                    return
                assistant_parts.append(piece)
                yield _sse("token", {"text": piece})
        except RuntimeError as e:
            logger.warning("Chat stream aborted: %s", e)
            yield _sse("error", {"message": str(e), "code": "stream_aborted"})
            return
        except Exception as exc:
            logger.exception("Unexpected stream failure")
            yield _sse(
                "error",
                {"message": "Stream failed", "code": "stream_failed"},
            )
            return

        if await request.is_disconnected():
            return

        assistant_text = "".join(assistant_parts).strip()
        if not assistant_text:
            yield _sse("error", {"message": "Empty response", "code": "empty_response"})
            return

        try:
            persist = open_tenant_session(user_id)
            try:
                metadata = None
                if enhanced_for_persist is not None:
                    metadata = build_attribution_metadata(
                        enhanced_for_persist,
                        user_id=user_id,
                        session_id=session_id,
                    )
                ep = Episode(
                    user_id=user_id,
                    session_id=session_id,
                    episode_kind="message",
                    role="assistant",
                    content=assistant_text,
                    metadata_json=metadata,
                )
                persist.add(ep)
                invalidate_user_caches(persist, user_id, session_id)
                persist.flush()
                assistant_id = ep.id
                persist.commit()
            finally:
                persist.close()
        except Exception as exc:
            logger.exception("Failed to persist assistant episode")
            yield _sse(
                "error",
                {
                    "message": "Failed to save assistant message",
                    "code": "persist_failed",
                },
            )
            return

        if chat_tier != "unlimited":
            if usage.total_tokens <= 0:
                filled = fill_usage_from_estimate(
                    usage,
                    prompt_messages=messages,
                    completion_text=assistant_text,
                )
                if filled:
                    logger.warning(
                        "DeepSeek omitted usage metadata; recorded tiktoken estimate "
                        "(user_id=%s tier=%s model=%s total=%s)",
                        user_id,
                        chat_tier,
                        chat_model,
                        usage.total_tokens,
                    )
                else:
                    logger.error(
                        "Chat finished with zero billable tokens after estimate "
                        "(user_id=%s tier=%s model=%s)",
                        user_id,
                        chat_tier,
                        chat_model,
                    )
            if usage.total_tokens > 0:
                record_db = open_tenant_session(user_id)
                try:
                    record_llm_usage(
                        record_db,
                        user_id,
                        model=chat_model,
                        prompt_tokens=usage.prompt_tokens,
                        completion_tokens=usage.completion_tokens,
                        session_id=session_id,
                        tier_hint=chat_tier,
                    )
                    record_db.commit()
                finally:
                    record_db.close()

        quota_payload = None
        status_db = open_tenant_session(user_id)
        try:
            status_user = status_db.get(User, user_id)
            if status_user is not None:
                maybe_rollover_user(status_db, status_user)
                quota_payload = get_user_quota_status(status_user).model_dump()
        finally:
            status_db.close()

        yield _sse(
            "done",
            {
                "assistant_message_id": assistant_id,
                "session_id": session_id,
                "model": chat_model,
                "tier": chat_tier,
                "quota": quota_payload,
            },
        )

        track_background_task(
            asyncio.create_task(run_post_turn_compression(user_id, session_id))
        )
        track_background_task(
            asyncio.create_task(embed_new_turns(user_id, session_id))
        )
        track_background_task(
            asyncio.create_task(
                _maybe_extract_facts(
                    user_id,
                    session_id,
                    user_message_count,
                    session_message_count,
                )
            )
        )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            **retrieval_headers,
        },
    )
