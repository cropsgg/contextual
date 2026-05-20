"""Context reduction: summarize, embed, offload older turns (Phase 2+3)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.episode import Episode
from app.models.user_fact import UserFact
from app.services.compression_errors import (
    CompressionError,
    CompressionFailureReason,
    CompressionMetrics,
)
from app.services.database import open_tenant_session
from app.services.rls import set_tenant_context
from app.models.user import User
from app.services.deepseek import ChatCompletionUsage, summarize_conversation
from app.services.token_quota import (
    deepseek_quota_blocked,
    fill_usage_from_estimate,
    maybe_rollover_user,
    record_llm_usage,
)
from app.services.gemini_embeddings import embed_text
from app.services.fact_injection import select_facts_for_injection
from app.services.memory_keyword_search import search_memory_episodes_keyword
from app.services.memory_rerank import rerank_memories
from app.services.memory_retrieval import (
    ScoredMemory,
    embed_query,
    search_in_session_memory_chunks,
    search_in_session_memory_chunks_keyword,
    search_memory_episodes,
)
from app.services.retrieval_status import (
    AssembledContext,
    EnhancedContext,
    RetrievalMode,
    RetrievalOutcome,
)
from app.services.token_counter import count_chat_messages_tokens

logger = logging.getLogger(__name__)

_compression_locks: dict[str, asyncio.Lock] = {}
_background_compression_sessions: set[str] = set()
# Last compression outcome per session (for context_status / memory_paused UX).
_last_compression_metrics: dict[str, CompressionMetrics] = {}
_last_retrieval_outcome: dict[str, RetrievalOutcome] = {}
# Prevent asyncio tasks from being GC'd mid-flight (CPython requires a strong ref).
_background_tasks: set[asyncio.Task] = set()


def track_background_task(task: asyncio.Task) -> None:
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _session_key(user_id: int, session_id: str) -> str:
    return f"{user_id}:{session_id}"


def compression_lock(user_id: int, session_id: str) -> asyncio.Lock:
    key = _session_key(user_id, session_id)
    if key not in _compression_locks:
        _compression_locks[key] = asyncio.Lock()
    return _compression_locks[key]


def is_compression_inflight(user_id: int, session_id: str) -> bool:
    """True while sync holds the session lock or background compression is running."""
    key = _session_key(user_id, session_id)
    if key in _background_compression_sessions:
        return True
    lock = _compression_locks.get(key)
    return lock is not None and lock.locked()


def _mark_background_compression(user_id: int, session_id: str, active: bool) -> None:
    key = _session_key(user_id, session_id)
    if active:
        _background_compression_sessions.add(key)
    else:
        _background_compression_sessions.discard(key)


def get_last_compression_metrics(user_id: int, session_id: str) -> CompressionMetrics | None:
    return _last_compression_metrics.get(_session_key(user_id, session_id))


def get_last_retrieval_outcome(user_id: int, session_id: str) -> RetrievalOutcome | None:
    return _last_retrieval_outcome.get(_session_key(user_id, session_id))


def _record_retrieval_outcome(
    user_id: int, session_id: str, outcome: RetrievalOutcome
) -> None:
    _last_retrieval_outcome[_session_key(user_id, session_id)] = outcome


def _record_compression_metrics(user_id: int, session_id: str, metrics: CompressionMetrics) -> None:
    _last_compression_metrics[_session_key(user_id, session_id)] = metrics
    logger.info("context_compression outcome", extra=metrics.log_fields())


def _log_compression_failure(
    user_id: int,
    session_id: str,
    reason: CompressionFailureReason,
    *,
    rounds: int,
    active_tokens: int,
    threshold: int,
    exc: BaseException | None = None,
) -> None:
    metrics = CompressionMetrics(
        compression_attempted=True,
        compression_succeeded=False,
        failure_reason=reason.value,
        rounds=rounds,
        active_token_count=active_tokens,
        context_threshold=threshold,
    )
    _record_compression_metrics(user_id, session_id, metrics)
    if exc is not None:
        logger.error(
            "context_compression failed: %s (session=%s user=%s)",
            reason.value,
            session_id,
            user_id,
            exc_info=exc,
            extra=metrics.log_fields(),
        )
    else:
        logger.error(
            "context_compression failed: %s (session=%s user=%s)",
            reason.value,
            session_id,
            user_id,
            extra=metrics.log_fields(),
        )


def load_active_message_episodes(
    db: Session,
    user_id: int,
    session_id: str,
) -> list[Episode]:
    return list(
        db.scalars(
            select(Episode)
            .where(
                Episode.user_id == user_id,
                Episode.session_id == session_id,
                or_(Episode.episode_kind == "message", Episode.episode_kind.is_(None)),
                Episode.is_offloaded.is_(False),
            )
            .order_by(Episode.created_at.asc())
        ).all()
    )


def refresh_enhanced_context(
    db: Session,
    user_id: int,
    enhanced: AssembledContext | None,
) -> AssembledContext | None:
    """Keep assembled retrieval results after expire_all within one request."""
    if enhanced is None:
        return None
    return enhanced


def load_user_facts(db: Session, user_id: int) -> list[UserFact]:
    return list(
        db.scalars(
            select(UserFact)
            .where(UserFact.user_id == user_id)
            .order_by(UserFact.updated_at.desc())
        ).all()
    )


async def _embed_query_with_retry(text: str) -> list[float]:
    last_exc: Exception | None = None
    attempts = max(1, settings.retrieval_embed_max_retries)
    for attempt in range(attempts):
        try:
            return await embed_query(text)
        except Exception as exc:
            last_exc = exc
            if attempt + 1 >= attempts:
                break
            delay = settings.retrieval_embed_retry_base_seconds * (2**attempt)
            await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc


async def assemble_enhanced_context(
    db: Session,
    user_id: int,
    session_id: str,
    current_query: str,
) -> AssembledContext:
    import time

    from app.services.retrieval_bundle_cache import (
        build_retrieval_cache_key,
        compute_version_token,
        get_cached_assembled_context,
        store_assembled_context_cache,
    )

    version_token = compute_version_token(db, user_id, session_id)
    cache_key = build_retrieval_cache_key(
        user_id, session_id, current_query, version_token
    )
    cached_ctx = get_cached_assembled_context(db, cache_key)
    if cached_ctx is not None:
        _record_retrieval_outcome(user_id, session_id, cached_ctx.retrieval)
        return cached_ctx

    cross_session: list[ScoredMemory] = []
    in_session: list[ScoredMemory] = []
    query_vector: list[float] | None = None
    embed_latency: float | None = None
    search_latency: float | None = None
    rerank_latency: float | None = None
    mode = RetrievalMode.FULL
    failure_reason: str | None = None
    keyword_used = False
    embed_ok = False
    reranked = False
    rerank_fallback = False

    latest_mem = latest_memory_episode(db, user_id, session_id)
    exclude_ids: set[int] = set()
    if latest_mem:
        exclude_ids.add(latest_mem.id)

    if not settings.gemini_api_key:
        logger.warning("GEMINI_API_KEY missing; cross-session memory unavailable")
        mode = RetrievalMode.UNAVAILABLE_NO_KEY
        failure_reason = "missing_gemini_api_key"
    else:
        t0 = time.perf_counter()
        try:
            query_vector = await _embed_query_with_retry(current_query)
            embed_ok = True
            embed_latency = (time.perf_counter() - t0) * 1000
        except Exception as exc:
            embed_latency = (time.perf_counter() - t0) * 1000
            logger.warning("Query embed failed; trying keyword fallback", exc_info=exc)
            failure_reason = "embed_failed"
            kw = search_memory_episodes_keyword(
                db,
                user_id,
                current_query,
                exclude_session_id=session_id,
            )
            if kw:
                cross_session = kw[: settings.retrieval_final_k]
                mode = RetrievalMode.DEGRADED_KEYWORD
                keyword_used = True
            else:
                mode = RetrievalMode.UNAVAILABLE_EMBED
                failure_reason = "embed_failed_no_keyword_hits"

        if query_vector is not None:
            try:
                t1 = time.perf_counter()
                candidates = search_memory_episodes(
                    db,
                    user_id,
                    query_vector,
                    exclude_session_id=session_id,
                )
                search_latency = (time.perf_counter() - t1) * 1000
                rerank_usage = ChatCompletionUsage()
                rerank_result = await rerank_memories(
                    current_query,
                    candidates,
                    usage_out=rerank_usage,
                )
                user_row = db.get(User, user_id)
                if user_row is not None:
                    maybe_rollover_user(db, user_row)
                    if not deepseek_quota_blocked(user_row):
                        if rerank_usage.total_tokens <= 0 and rerank_result.reranked:
                            fill_usage_from_estimate(
                                rerank_usage,
                                prompt_messages=[
                                    {"role": "user", "content": current_query[:2000]}
                                ],
                            )
                        if rerank_usage.total_tokens > 0:
                            record_llm_usage(
                                db,
                                user_id,
                                model=settings.deepseek_rerank_model,
                                prompt_tokens=rerank_usage.prompt_tokens,
                                completion_tokens=rerank_usage.completion_tokens,
                                session_id=session_id,
                            )
                cross_session = rerank_result.memories
                reranked = rerank_result.reranked
                rerank_fallback = rerank_result.rerank_fallback
                rerank_latency = rerank_result.latency_ms
                mode = RetrievalMode.FULL
                failure_reason = None
            except Exception as exc:
                logger.warning(
                    "Vector memory search failed; keyword fallback",
                    exc_info=exc,
                )
                failure_reason = "vector_search_failed"
                kw = search_memory_episodes_keyword(
                    db,
                    user_id,
                    current_query,
                    exclude_session_id=session_id,
                )
                if kw:
                    cross_session = kw[: settings.retrieval_final_k]
                    mode = RetrievalMode.DEGRADED_KEYWORD
                    keyword_used = True
                else:
                    mode = RetrievalMode.UNAVAILABLE_SEARCH
                    failure_reason = "vector_search_failed_no_keyword_hits"

    if settings.in_session_memory_final_k > 0 and current_query.strip():
        latest_content = latest_mem.content if latest_mem else None
        if query_vector is not None:
            in_session = search_in_session_memory_chunks(
                db,
                user_id,
                session_id,
                query_vector,
                exclude_episode_ids=exclude_ids,
                latest_summary_content=latest_content,
            )
        else:
            in_session = search_in_session_memory_chunks_keyword(
                db,
                user_id,
                session_id,
                current_query,
                exclude_episode_ids=exclude_ids,
                latest_summary_content=latest_content,
            )
            if in_session:
                keyword_used = True
                if mode in (
                    RetrievalMode.UNAVAILABLE_EMBED,
                    RetrievalMode.UNAVAILABLE_SEARCH,
                    RetrievalMode.UNAVAILABLE_NO_KEY,
                ):
                    mode = RetrievalMode.DEGRADED_KEYWORD
                    failure_reason = failure_reason or "in_session_keyword_fallback"

    injected_facts, suppressed = await select_facts_for_injection(
        db,
        user_id,
        current_query,
        query_vector=query_vector if embed_ok else None,
    )

    cross_available = mode in (RetrievalMode.FULL, RetrievalMode.DEGRADED_KEYWORD)

    outcome = RetrievalOutcome(
        mode=mode,
        cross_session_memory_available=cross_available,
        embed_succeeded=embed_ok,
        keyword_fallback_used=keyword_used,
        reranked=reranked,
        rerank_fallback=rerank_fallback,
        embed_latency_ms=embed_latency,
        search_latency_ms=search_latency,
        rerank_latency_ms=rerank_latency,
        failure_reason=failure_reason,
        suppressed_fact_count=suppressed,
    )
    _record_retrieval_outcome(user_id, session_id, outcome)

    assembled = AssembledContext(
        injected_facts=injected_facts,
        cross_session_memories=cross_session,
        in_session_memories=in_session,
        retrieval=outcome,
    )
    try:
        store_assembled_context_cache(db, cache_key, assembled)
    except Exception:
        logger.debug("retrieval bundle cache store failed", exc_info=True)
    return assembled


async def build_enhanced_context(
    db: Session,
    user_id: int,
    session_id: str,
    current_query: str,
) -> AssembledContext:
    """Assemble facts, memories, and retrieval metadata for one chat turn."""
    return await assemble_enhanced_context(db, user_id, session_id, current_query)


def format_enhanced_system_blocks(ctx: AssembledContext) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    retrieval = ctx.retrieval

    if ctx.injected_facts:
        lines = [
            "Known facts about the user (may be incomplete; prefer recent conversation on conflict):",
        ]
        for f in ctx.injected_facts:
            lines.append(f"- {f.fact_key}: {f.fact_value}")
        blocks.append(
            {
                "role": "system",
                "content": f"<user_profile>\n{chr(10).join(lines)}\n</user_profile>",
            }
        )
    else:
        blocks.append(
            {
                "role": "system",
                "content": (
                    "<user_profile>\n"
                    "No stored user facts selected for this reply.\n"
                    "</user_profile>"
                ),
            }
        )

    cross_lines: list[str] = []
    mode = retrieval.mode
    if mode == RetrievalMode.DEGRADED_KEYWORD and ctx.cross_session_memories:
        cross_lines.append(
            "Cross-session memory (keyword fallback — semantic search unavailable):"
        )
        for m in ctx.cross_session_memories:
            sid = m.session_id
            label = f"{sid[:8]}…" if len(sid) >= 8 else sid
            cross_lines.append(f"- [{label}] {m.snippet[:800]}")
    elif mode == RetrievalMode.FULL and ctx.cross_session_memories:
        cross_lines.append(
            "Relevant historical context from past sessions (not the current live thread):"
        )
        for m in ctx.cross_session_memories:
            sid = m.session_id
            label = f"{sid[:8]}…" if len(sid) >= 8 else sid
            cross_lines.append(f"- [{label}] {m.snippet[:800]}")
    elif mode == RetrievalMode.FULL and not ctx.cross_session_memories:
        cross_lines.append("No matching past sessions found.")
    elif mode in (
        RetrievalMode.UNAVAILABLE_NO_KEY,
        RetrievalMode.UNAVAILABLE_EMBED,
        RetrievalMode.UNAVAILABLE_SEARCH,
    ):
        reason = retrieval.failure_reason or mode.value
        cross_lines.append(
            f"Cross-session memory unavailable for this reply ({reason}). "
            "Same-session summary and active messages still apply."
        )
    elif mode == RetrievalMode.DEGRADED_KEYWORD and not ctx.cross_session_memories:
        cross_lines.append(
            "Cross-session memory unavailable (keyword fallback found no matches)."
        )

    blocks.append(
        {
            "role": "system",
            "content": (
                f"<relevant_past_context>\n{chr(10).join(cross_lines)}\n</relevant_past_context>"
            ),
        }
    )

    if ctx.in_session_memories:
        in_lines = [
            "Earlier summaries from this conversation (not the latest compressed block):",
        ]
        for m in ctx.in_session_memories:
            in_lines.append(f"- {m.snippet[:800]}")
        blocks.append(
            {
                "role": "system",
                "content": f"<in_session_memory>\n{chr(10).join(in_lines)}\n</in_session_memory>",
            }
        )

    return blocks


def build_attribution_metadata(ctx: AssembledContext) -> dict:
    memories_out: list[dict] = []
    for m in ctx.cross_session_memories:
        memories_out.append(
            {
                "episode_id": m.episode_id,
                "session_id": m.session_id,
                "snippet": m.snippet[:500],
                "score": round(m.similarity, 4),
                "scope": "cross_session",
            }
        )
    for m in ctx.in_session_memories:
        memories_out.append(
            {
                "episode_id": m.episode_id,
                "session_id": m.session_id,
                "snippet": m.snippet[:500],
                "score": round(m.similarity, 4),
                "scope": "in_session",
            }
        )
    return {
        "attribution": {
            "facts": [
                {
                    "fact_key": f.fact_key,
                    "fact_value": f.fact_value,
                    "selection_reason": f.selection_reason,
                    "pinned": f.pinned,
                }
                for f in ctx.injected_facts
            ],
            "memories": memories_out,
            "retrieval": ctx.retrieval.to_attribution_dict(),
        }
    }


def latest_memory_episode(
    db: Session,
    user_id: int,
    session_id: str,
) -> Episode | None:
    return db.scalar(
        select(Episode)
        .where(
            Episode.user_id == user_id,
            Episode.session_id == session_id,
            Episode.episode_kind == "memory",
        )
        .order_by(Episode.created_at.desc(), Episode.id.desc())
        .limit(1)
    )


def build_completion_messages(
    db: Session,
    user_id: int,
    session_id: str,
    enhanced: EnhancedContext | None = None,
) -> list[dict[str, str]]:
    from app.services.prompt_assembly_cache import get_or_build_completion_messages

    return get_or_build_completion_messages(
        db, user_id, session_id, enhanced=enhanced
    )


def _build_completion_messages_uncached(
    db: Session,
    user_id: int,
    session_id: str,
    enhanced: EnhancedContext | None = None,
) -> list[dict[str, str]]:
    memory = latest_memory_episode(db, user_id, session_id)
    active = load_active_message_episodes(db, user_id, session_id)
    messages: list[dict[str, str]] = [
        {"role": "system", "content": settings.chat_system_prompt},
    ]
    if enhanced is not None:
        messages.extend(format_enhanced_system_blocks(enhanced))
    if memory:
        messages.append(
            {
                "role": "system",
                "content": f"Compressed context:\n{memory.content}",
            }
        )
    for ep in active:
        role = ep.role if ep.role in ("user", "assistant", "system") else "user"
        messages.append({"role": role, "content": ep.content})
    return messages


def count_active_prompt_tokens(
    db: Session,
    user_id: int,
    session_id: str,
    enhanced: EnhancedContext | None = None,
) -> int:
    return count_chat_messages_tokens(
        build_completion_messages(db, user_id, session_id, enhanced=enhanced)
    )


async def _embed_with_retry(summary_text: str) -> list[float]:
    """Embed summary with exponential backoff on transient API failures."""
    last_exc: Exception | None = None
    attempts = max(1, settings.compression_embed_max_retries)
    for attempt in range(attempts):
        try:
            return await embed_text(summary_text)
        except Exception as exc:
            last_exc = exc
            if attempt + 1 >= attempts:
                break
            delay = settings.compression_embed_retry_base_seconds * (2**attempt)
            logger.warning(
                "embed_text failed (attempt %s/%s); retrying in %.2fs",
                attempt + 1,
                attempts,
                delay,
            )
            await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc


async def _maybe_run_offload_fact_extraction(
    db: Session,
    user_id: int,
    session_id: str,
) -> None:
    if not settings.deepseek_api_key:
        return
    mem = latest_memory_episode(db, user_id, session_id)
    if not mem or not mem.metadata_json:
        return
    if not mem.metadata_json.get("offloaded_episode_ids"):
        return
    try:
        from app.services.fact_extraction_v2 import run_offload_extraction

        await run_offload_extraction(db, user_id, session_id, mem.id)
    except Exception:
        logger.exception("offload fact extraction failed")


async def reduce_once(
    db: Session,
    user_id: int,
    session_id: str,
    *,
    dry_run: bool = False,
) -> bool:
    """One compression cycle: oldest half of offload-eligible messages -> summary + embed."""
    if not settings.gemini_api_key:
        raise CompressionError(
            "GEMINI_API_KEY is required for context compression",
            reason=CompressionFailureReason.MISSING_GEMINI_KEY,
            metrics=CompressionMetrics(
                compression_attempted=True,
                compression_succeeded=False,
                failure_reason=CompressionFailureReason.MISSING_GEMINI_KEY.value,
            ),
        )

    active = load_active_message_episodes(db, user_id, session_id)
    k = max(1, settings.min_recent_messages_to_keep)
    if len(active) <= k:
        return False

    eligible = active[:-k]
    offload_count = len(eligible) // 2
    if offload_count < 1:
        return False

    to_offload = eligible[:offload_count]
    memory = latest_memory_episode(db, user_id, session_id)
    prev_summary = memory.content if memory else None

    to_summarize: list[dict[str, str]] = []
    for ep in to_offload:
        role = ep.role if ep.role in ("user", "assistant", "system") else "user"
        to_summarize.append({"role": role, "content": ep.content})

    user_row = db.get(User, user_id)
    if user_row is not None:
        maybe_rollover_user(db, user_row)
        if deepseek_quota_blocked(user_row):
            raise CompressionError(
                "Daily token quota exhausted; cannot summarize for compression",
                reason=CompressionFailureReason.QUOTA_EXHAUSTED,
                metrics=CompressionMetrics(
                    compression_attempted=True,
                    compression_succeeded=False,
                    failure_reason=CompressionFailureReason.QUOTA_EXHAUSTED.value,
                ),
            )

    summarize_usage = ChatCompletionUsage()
    try:
        summary_text = await summarize_conversation(
            to_summarize, prev_summary, usage_out=summarize_usage
        )
    except Exception as exc:
        db.rollback()
        raise CompressionError(
            "Conversation summarization failed during context compression",
            reason=CompressionFailureReason.SUMMARIZE_FAILED,
            metrics=CompressionMetrics(
                compression_attempted=True,
                compression_succeeded=False,
                failure_reason=CompressionFailureReason.SUMMARIZE_FAILED.value,
            ),
        ) from exc

    if summarize_usage.total_tokens <= 0:
        fill_usage_from_estimate(
            summarize_usage,
            prompt_messages=to_summarize,
            completion_text=summary_text,
        )
    if summarize_usage.total_tokens > 0:
        record_llm_usage(
            db,
            user_id,
            model=settings.deepseek_summarize_model,
            prompt_tokens=summarize_usage.prompt_tokens,
            completion_tokens=summarize_usage.completion_tokens,
            session_id=session_id,
        )

    try:
        vector = await _embed_with_retry(summary_text)
    except Exception as exc:
        db.rollback()
        raise CompressionError(
            "Embedding failed after summarization; no messages were offloaded",
            reason=CompressionFailureReason.EMBED_FAILED,
            metrics=CompressionMetrics(
                compression_attempted=True,
                compression_succeeded=False,
                failure_reason=CompressionFailureReason.EMBED_FAILED.value,
            ),
        ) from exc

    now = datetime.now(timezone.utc)
    mem = Episode(
        user_id=user_id,
        session_id=session_id,
        episode_kind="memory",
        role="system",
        content=summary_text,
        summary=summary_text,
        is_offloaded=False,
        embedding=vector,
        metadata_json={
            "offloaded_episode_ids": [ep.id for ep in to_offload],
            "prior_memory_id": memory.id if memory else None,
        },
    )
    try:
        db.add(mem)
        for ep in to_offload:
            ep.is_offloaded = True
            ep.offloaded_at = now
        if dry_run:
            db.flush()
            set_tenant_context(db, user_id)
            return True
        db.commit()
        set_tenant_context(db, user_id)
        from app.services.cache_invalidation import invalidate_user_caches

        invalidate_user_caches(db, user_id, session_id)
        db.commit()
        return True
    except Exception:
        db.rollback()
        set_tenant_context(db, user_id)
        raise


async def reduce_until_under(
    db: Session,
    user_id: int,
    session_id: str,
    threshold: int | None = None,
    max_rounds: int = 6,
    enhanced: EnhancedContext | None = None,
    *,
    dry_run: bool = False,
) -> CompressionMetrics:
    """Run reduce_once until under threshold. Raises CompressionError on hard failure."""
    limit = threshold if threshold is not None else settings.context_threshold_tokens
    tokens_before = count_active_prompt_tokens(db, user_id, session_id, enhanced=enhanced)

    if tokens_before <= limit:
        metrics = CompressionMetrics(
            compression_attempted=False,
            compression_succeeded=True,
            failure_reason=None,
            rounds=0,
            active_token_count=tokens_before,
            context_threshold=limit,
        )
        if not dry_run:
            _record_compression_metrics(user_id, session_id, metrics)
        return metrics

    if not settings.gemini_api_key:
        _log_compression_failure(
            user_id,
            session_id,
            CompressionFailureReason.MISSING_GEMINI_KEY,
            rounds=0,
            active_tokens=tokens_before,
            threshold=limit,
        )
        raise CompressionError(
            "Context exceeds threshold but GEMINI_API_KEY is not configured",
            reason=CompressionFailureReason.MISSING_GEMINI_KEY,
            metrics=CompressionMetrics(
                compression_attempted=True,
                compression_succeeded=False,
                failure_reason=CompressionFailureReason.MISSING_GEMINI_KEY.value,
                rounds=0,
                active_token_count=tokens_before,
                context_threshold=limit,
            ),
        )

    rounds = 0
    projected_offload = 0
    for _ in range(max_rounds):
        active_tokens = count_active_prompt_tokens(
            db, user_id, session_id, enhanced=enhanced
        )
        if active_tokens <= limit:
            metrics = CompressionMetrics(
                compression_attempted=True,
                compression_succeeded=True,
                failure_reason=None,
                rounds=rounds,
                active_token_count=active_tokens,
                context_threshold=limit,
                projected_offload_count=projected_offload,
            )
            if not dry_run:
                _record_compression_metrics(user_id, session_id, metrics)
            if dry_run:
                db.rollback()
                set_tenant_context(db, user_id)
            return metrics
        try:
            active_before = load_active_message_episodes(db, user_id, session_id)
            k = max(1, settings.min_recent_messages_to_keep)
            eligible = active_before[:-k] if len(active_before) > k else []
            offload_n = len(eligible) // 2 if eligible else 0
            did = await reduce_once(db, user_id, session_id, dry_run=dry_run)
            if did and offload_n > 0:
                projected_offload += offload_n
        except CompressionError:
            db.rollback()
            set_tenant_context(db, user_id)
            raise
        except Exception as exc:
            db.rollback()
            set_tenant_context(db, user_id)
            _log_compression_failure(
                user_id,
                session_id,
                CompressionFailureReason.SUMMARIZE_FAILED,
                rounds=rounds,
                active_tokens=active_tokens,
                threshold=limit,
                exc=exc,
            )
            raise CompressionError(
                "Context compression failed unexpectedly",
                reason=CompressionFailureReason.SUMMARIZE_FAILED,
                metrics=CompressionMetrics(
                    compression_attempted=True,
                    compression_succeeded=False,
                    failure_reason=CompressionFailureReason.SUMMARIZE_FAILED.value,
                    rounds=rounds,
                    active_token_count=active_tokens,
                    context_threshold=limit,
                ),
            ) from exc
        db.expire_all()
        set_tenant_context(db, user_id)
        enhanced = refresh_enhanced_context(db, user_id, enhanced)
        if not did:
            break
        rounds += 1

    active_tokens = count_active_prompt_tokens(db, user_id, session_id, enhanced=enhanced)
    if active_tokens <= limit:
        metrics = CompressionMetrics(
            compression_attempted=True,
            compression_succeeded=True,
            failure_reason=None,
            rounds=rounds,
            active_token_count=active_tokens,
            context_threshold=limit,
            projected_offload_count=projected_offload,
        )
        if not dry_run:
            _record_compression_metrics(user_id, session_id, metrics)
            if rounds > 0:
                await _maybe_run_offload_fact_extraction(db, user_id, session_id)
        elif dry_run:
            db.rollback()
            set_tenant_context(db, user_id)
        return metrics

    reason = (
        CompressionFailureReason.NO_PROGRESS
        if rounds == 0
        else CompressionFailureReason.STILL_OVER_THRESHOLD
    )
    if dry_run:
        db.rollback()
        set_tenant_context(db, user_id)
    _log_compression_failure(
        user_id,
        session_id,
        reason,
        rounds=rounds,
        active_tokens=active_tokens,
        threshold=limit,
    )
    raise CompressionError(
        "Context still exceeds token threshold after compression",
        reason=reason,
        metrics=CompressionMetrics(
            compression_attempted=True,
            compression_succeeded=False,
            failure_reason=reason.value,
            rounds=rounds,
            active_token_count=active_tokens,
            context_threshold=limit,
            projected_offload_count=projected_offload,
        ),
    )


def context_status(db: Session, user_id: int, session_id: str) -> dict:
    from app.core.config import settings as app_settings
    from app.models.user import User
    from app.services.embedding_cache_metrics import get_embedding_cache_metrics
    from app.services.retrieval_bundle_cache import get_last_retrieval_bundle_cache_hit

    user = db.get(User, user_id)
    memory = latest_memory_episode(db, user_id, session_id)
    offloaded_n = db.scalar(
        select(func.count())
        .select_from(Episode)
        .where(
            Episode.user_id == user_id,
            Episode.session_id == session_id,
            Episode.episode_kind == "message",
            Episode.is_offloaded.is_(True),
        )
    )
    memory_n = db.scalar(
        select(func.count())
        .select_from(Episode)
        .where(
            Episode.user_id == user_id,
            Episode.session_id == session_id,
            Episode.episode_kind == "memory",
        )
    )
    last_compressed = db.scalar(
        select(func.max(Episode.created_at))
        .where(
            Episode.user_id == user_id,
            Episode.session_id == session_id,
            Episode.episode_kind == "memory",
        )
    )
    tokens = count_active_prompt_tokens(db, user_id, session_id)
    last_metrics = get_last_compression_metrics(user_id, session_id)
    memory_paused = bool(
        last_metrics
        and last_metrics.compression_attempted
        and not last_metrics.compression_succeeded
    )
    offloaded_count = int(offloaded_n or 0)
    offloaded_label: str | None = None
    if offloaded_count > 0 and last_compressed is not None:
        when = (
            f"{last_compressed.strftime('%b')} {last_compressed.day}, "
            f"{last_compressed.year}"
        )
        noun = "message" if offloaded_count == 1 else "messages"
        offloaded_label = f"{offloaded_count} {noun} summarized on {when}"

    embed_metrics = get_embedding_cache_metrics()
    fact_err = user.fact_extraction_last_error if user else None
    if app_settings.environment == "production" and fact_err:
        fact_err = "sync paused"

    return {
        "active_token_count": tokens,
        "context_threshold": settings.context_threshold_tokens,
        "offloaded_message_count": offloaded_count,
        "memory_chunk_count": int(memory_n or 0),
        "last_summary": memory.content if memory else None,
        "last_compressed_at": last_compressed.isoformat() if last_compressed else None,
        "latest_memory_episode_id": memory.id if memory else None,
        "offloaded_summary_label": offloaded_label,
        "compression_in_progress": is_compression_inflight(user_id, session_id),
        "compression_attempted": (
            last_metrics.compression_attempted if last_metrics else False
        ),
        "compression_succeeded": (
            last_metrics.compression_succeeded if last_metrics else True
        ),
        "failure_reason": last_metrics.failure_reason if last_metrics else None,
        "memory_paused": memory_paused,
        "last_fact_extraction_at": (
            user.last_fact_extraction_at.isoformat()
            if user and user.last_fact_extraction_at
            else None
        ),
        "fact_extraction_last_error": fact_err,
        "fact_extraction_consecutive_failures": int(
            user.fact_extraction_consecutive_failures or 0
        )
        if user
        else 0,
        "embedding_cache_hit_rate": embed_metrics.hit_rate,
        "retrieval_bundle_cache_hit": get_last_retrieval_bundle_cache_hit(),
        **_retrieval_status_fields(user_id, session_id),
    }


def _retrieval_status_fields(user_id: int, session_id: str) -> dict:
    last_retrieval = get_last_retrieval_outcome(user_id, session_id)
    if last_retrieval is None:
        return {
            "retrieval_mode": None,
            "cross_session_memory_available": None,
            "retrieval_degraded": False,
            "retrieval_failure_reason": None,
        }
    return {
        "retrieval_mode": last_retrieval.mode.value,
        "cross_session_memory_available": last_retrieval.cross_session_memory_available,
        "retrieval_degraded": last_retrieval.retrieval_degraded,
        "retrieval_failure_reason": last_retrieval.failure_reason,
    }


async def run_post_turn_compression(user_id: int, session_id: str) -> None:
    """Background compression after assistant persist (see compression_policy).

    Skips when the session is already under threshold — including when sync
    compression succeeded earlier in the same chat request and the assistant
    reply did not push tokens over the limit again.
    """
    _mark_background_compression(user_id, session_id, True)
    lock = compression_lock(user_id, session_id)
    try:
        async with lock:
            db = open_tenant_session(user_id)
            try:
                user_row = db.get(User, user_id)
                if user_row is not None:
                    maybe_rollover_user(db, user_row)
                    if deepseek_quota_blocked(user_row):
                        logger.info(
                            "post_turn_compression skipped (quota blocked user_id=%s)",
                            user_id,
                        )
                        return
                if (
                    count_active_prompt_tokens(db, user_id, session_id)
                    <= settings.context_threshold_tokens
                ):
                    logger.debug(
                        "post_turn_compression skipped (under threshold session=%s)",
                        session_id,
                    )
                    return
                await reduce_until_under(db, user_id, session_id)
            finally:
                db.close()
    except CompressionError:
        # Metrics recorded; next user turn will hard-fail sync path if still over.
        pass
    except Exception:
        logger.exception("Background compression failed")
    finally:
        _mark_background_compression(user_id, session_id, False)


async def maybe_compress_after_turn(user_id: int, session_id: str) -> None:
    """Alias for post-turn background compression (backwards compatible)."""
    await run_post_turn_compression(user_id, session_id)


def list_offloaded_messages(
    db: Session,
    user_id: int,
    session_id: str,
    *,
    page: int = 1,
    limit: int = 20,
) -> tuple[list[Episode], int]:
    """RLS-scoped offloaded chat turns for the authenticated user's session."""
    page = max(1, page)
    limit = max(1, min(limit, 100))
    base = (
        select(Episode)
        .where(
            Episode.user_id == user_id,
            Episode.session_id == session_id,
            Episode.episode_kind == "message",
            Episode.is_offloaded.is_(True),
        )
        .order_by(Episode.offloaded_at.desc().nullslast(), Episode.created_at.desc())
    )
    total = int(
        db.scalar(
            select(func.count())
            .select_from(Episode)
            .where(
                Episode.user_id == user_id,
                Episode.session_id == session_id,
                Episode.episode_kind == "message",
                Episode.is_offloaded.is_(True),
            )
        )
        or 0
    )
    rows = db.scalars(base.offset((page - 1) * limit).limit(limit)).all()
    return list(rows), int(total)


def session_compression_summary(
    db: Session,
    user_id: int,
    session_id: str,
) -> dict | None:
    """Latest in-session compressed summary (read-only, same user only via RLS)."""
    memory = latest_memory_episode(db, user_id, session_id)
    if not memory:
        return None
    offloaded_n = db.scalar(
        select(func.count())
        .select_from(Episode)
        .where(
            Episode.user_id == user_id,
            Episode.session_id == session_id,
            Episode.episode_kind == "message",
            Episode.is_offloaded.is_(True),
        )
    )
    return {
        "memory_episode_id": memory.id,
        "summary": memory.content,
        "created_at": memory.created_at.isoformat(),
        "offloaded_message_count": int(offloaded_n or 0),
    }
