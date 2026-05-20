"""Expert mode: preview assembled prompt without persisting or streaming (Phase 4)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import get_current_user, get_tenant_db
from app.core.expert import require_expert_preview
from app.core.limiter import limiter
from app.core.rate_limit import chat_rate_limit
from app.models.episode import Episode
from app.models.user import User
from app.schemas import (
    ChatPreviewEnhancedOut,
    ChatPreviewFactItem,
    ChatPreviewMemoryItem,
    ChatPreviewMessage,
    ChatPreviewOut,
    ChatRequest,
)
from app.services.compression_errors import CompressionError
from app.services.context_manager import (
    build_completion_messages,
    build_enhanced_context,
    compression_lock,
    count_active_prompt_tokens,
    reduce_until_under,
)
from app.services.token_counter import count_chat_messages_tokens

router = APIRouter()


def _episode_snapshot(db: Session, user_id: int, session_id: str) -> tuple[int, int]:
    """(memory_episode_count, offloaded_message_count) for session."""
    memory_count = db.scalar(
        select(func.count())
        .select_from(Episode)
        .where(
            Episode.user_id == user_id,
            Episode.session_id == session_id,
            Episode.episode_kind == "memory",
        )
    )
    offloaded_count = db.scalar(
        select(func.count())
        .select_from(Episode)
        .where(
            Episode.user_id == user_id,
            Episode.session_id == session_id,
            Episode.is_offloaded.is_(True),
        )
    )
    return int(memory_count or 0), int(offloaded_count or 0)


@router.post("/preview", response_model=ChatPreviewOut)
@limiter.limit(chat_rate_limit())
async def preview_chat(
    request: Request,
    body: ChatRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_tenant_db)],
) -> ChatPreviewOut:
    require_expert_preview(current_user)
    session_id = body.session_id.strip()
    user_id = current_user.id
    user_message = body.message

    memory_before, offloaded_before = _episode_snapshot(db, user_id, session_id)
    would_compress = False
    projected_offload = 0

    try:
        enhanced = await build_enhanced_context(db, user_id, session_id, user_message)

        lock = compression_lock(user_id, session_id)
        async with lock:
            if (
                count_active_prompt_tokens(db, user_id, session_id, enhanced=enhanced)
                > settings.context_threshold_tokens
            ):
                would_compress = True
                try:
                    metrics = await reduce_until_under(
                        db,
                        user_id,
                        session_id,
                        enhanced=enhanced,
                        dry_run=True,
                    )
                    projected_offload = metrics.projected_offload_count
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
                        },
                    ) from exc
                db.rollback()
                db.expire_all()
                enhanced = await build_enhanced_context(
                    db, user_id, session_id, user_message
                )
            messages = build_completion_messages(
                db, user_id, session_id, enhanced=enhanced
            )

        messages.append({"role": "user", "content": user_message})
        token_count = count_chat_messages_tokens(messages)

        enhanced_out = ChatPreviewEnhancedOut(
            facts=[
                ChatPreviewFactItem(fact_key=f.fact_key, fact_value=f.fact_value)
                for f in enhanced.injected_facts
            ],
            memories=[
                ChatPreviewMemoryItem(
                    episode_id=m.episode_id,
                    session_id=m.session_id,
                    snippet=m.snippet[:500],
                )
                for m in enhanced.memories
            ],
        )

        return ChatPreviewOut(
            messages=[
                ChatPreviewMessage(role=m["role"], content=m["content"])
                for m in messages
            ],
            token_count=token_count,
            model=settings.deepseek_model,
            enhanced=enhanced_out,
            would_compress=would_compress,
            projected_offload_count=projected_offload,
        )
    finally:
        db.rollback()
