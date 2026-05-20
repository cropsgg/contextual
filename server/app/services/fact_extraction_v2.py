"""Fact extraction v2: hybrid scopes, job queue, apply + dedup + cache invalidation."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.episode import Episode
from app.models.fact_extraction_run import FactExtractionRun
from app.models.user import User
from app.services.cache_invalidation import invalidate_user_caches
from app.services.rls import set_tenant_context
from app.services.deepseek import ChatCompletionUsage
from app.services.memory_gate import extract_gated_fact_changes
from app.services.token_quota import (
    deepseek_quota_blocked,
    fill_usage_from_estimate,
    maybe_rollover_user,
    record_llm_usage,
)
from app.services.fact_dedup import run_fact_dedup
from app.services.fact_extraction_apply import apply_fact_changes
from app.services.fact_extraction_sources import (
    build_extraction_payload,
    payload_to_llm_text,
)

logger = logging.getLogger(__name__)

_fact_locks: dict[int, asyncio.Lock] = {}
_RUNNING_STALE_SECONDS = 15 * 60


def fact_extraction_lock(user_id: int) -> asyncio.Lock:
    if user_id not in _fact_locks:
        _fact_locks[user_id] = asyncio.Lock()
    return _fact_locks[user_id]


def enqueue_extraction_run(
    db: Session,
    user_id: int,
    *,
    session_id: str | None,
    scope: str,
    payload: dict[str, Any] | None = None,
    memory_episode_id: int | None = None,
) -> FactExtractionRun:
    snap = payload or {}
    if memory_episode_id is not None:
        snap = {**snap, "memory_episode_id": memory_episode_id}
    run = FactExtractionRun(
        user_id=user_id,
        session_id=session_id,
        scope=scope,
        status="pending",
        attempts=0,
        max_attempts=settings.fact_extraction_max_attempts,
        next_retry_at=datetime.now(timezone.utc),
        payload_json=snap,
    )
    db.add(run)
    db.flush()
    return run


def _mark_user_extraction_success(db: Session, user_id: int) -> None:
    user = db.get(User, user_id)
    if user:
        user.last_fact_extraction_at = datetime.now(timezone.utc)
        user.fact_extraction_last_error = None
        user.fact_extraction_consecutive_failures = 0


def _mark_user_extraction_failure(db: Session, user_id: int, error: str) -> None:
    user = db.get(User, user_id)
    if user:
        user.fact_extraction_last_error = error[:2000]
        user.fact_extraction_consecutive_failures = int(
            user.fact_extraction_consecutive_failures or 0
        ) + 1


def _reclaim_stale_running_runs(db: Session, user_id: int) -> int:
    """Reset runs stuck in `running` long enough to be considered abandoned."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=_RUNNING_STALE_SECONDS)
    result = db.execute(
        update(FactExtractionRun)
        .where(
            FactExtractionRun.user_id == user_id,
            FactExtractionRun.status == "running",
            FactExtractionRun.updated_at < cutoff,
        )
        .values(
            status="failed",
            last_error="stale_running_reclaimed",
            next_retry_at=datetime.now(timezone.utc),
        )
    )
    return int(result.rowcount or 0)


async def _execute_run(db: Session, run: FactExtractionRun) -> dict[str, Any]:
    memory_episode = None
    if run.payload_json and run.payload_json.get("memory_episode_id"):
        memory_episode = db.get(Episode, int(run.payload_json["memory_episode_id"]))

    payload = build_extraction_payload(
        db,
        run.user_id,
        session_id=run.session_id,
        scope=run.scope,
        memory_episode=memory_episode,
    )
    text = payload_to_llm_text(payload)
    if not text.strip():
        return {"upserted": 0, "deleted": 0, "noop": 0, "deprecated_by_cap": 0}

    user = db.get(User, run.user_id)
    if user is not None:
        maybe_rollover_user(db, user)
        if deepseek_quota_blocked(user):
            return {
                "upserted": 0,
                "deleted": 0,
                "noop": 0,
                "deprecated_by_cap": 0,
                "skipped": "quota_exhausted",
            }

    usage = ChatCompletionUsage()
    gate_result = await extract_gated_fact_changes(
        text, scope=run.scope, usage_out=usage
    )
    if usage.total_tokens <= 0 and gate_result.skipped != "no_memory_signals":
        fill_usage_from_estimate(
            usage,
            prompt_messages=[{"role": "user", "content": text[:8000]}],
        )
    if usage.total_tokens > 0:
        record_llm_usage(
            db,
            run.user_id,
            model=settings.deepseek_memory_model,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            session_id=run.session_id,
        )
    if gate_result.skipped:
        return {
            "upserted": 0,
            "deleted": 0,
            "noop": 0,
            "deprecated_by_cap": 0,
            "skipped": gate_result.skipped,
            "ignored": 0,
            "gate_decisions": [],
            "candidates_proposed": 0,
        }
    result = apply_fact_changes(
        db,
        run.user_id,
        gate_result.changes,
        session_id=run.session_id,
        run_id=run.id,
    )
    await run_fact_dedup(db, run.user_id)
    invalidate_user_caches(db, run.user_id, run.session_id)
    return {
        "upserted": result.upserted,
        "deleted": result.deleted,
        "noop": result.noop,
        "deprecated_by_cap": result.deprecated_by_cap,
        "ignored": gate_result.ignored,
        "gate_decisions": gate_result.gate_decisions,
        "candidates_proposed": gate_result.candidates_proposed,
    }


async def process_extraction_run(db: Session, run_id: int, user_id: int) -> bool:
    set_tenant_context(db, user_id)
    run = db.get(FactExtractionRun, run_id)
    if run is None or run.status in ("succeeded", "dead"):
        return False

    user = db.get(User, user_id)
    if user and int(user.fact_extraction_consecutive_failures or 0) >= (
        settings.fact_extraction_max_consecutive_failures
    ):
        run.status = "dead"
        run.last_error = "max_consecutive_failures"
        db.commit()
        set_tenant_context(db, user_id)
        return False

    run.status = "running"
    run.attempts = int(run.attempts or 0) + 1
    db.commit()
    set_tenant_context(db, user_id)

    try:
        run = db.get(FactExtractionRun, run_id)
        if run is None:
            return False
        result = await _execute_run(db, run)
        run.status = "succeeded"
        run.result_json = result
        run.last_error = None
        _mark_user_extraction_success(db, user_id)
        db.commit()
        return True
    except Exception as exc:
        db.rollback()
        db.expire_all()
        set_tenant_context(db, user_id)
        failed = db.get(FactExtractionRun, run_id)
        if failed is None:
            return False
        err = str(exc)[:2000]
        failed.last_error = err
        _mark_user_extraction_failure(db, failed.user_id, err)
        if failed.attempts >= failed.max_attempts:
            failed.status = "dead"
        else:
            failed.status = "failed"
            backoff = settings.fact_extraction_retry_base_seconds * (
                2 ** (failed.attempts - 1)
            )
            failed.next_retry_at = datetime.now(timezone.utc) + timedelta(
                seconds=backoff
            )
        db.commit()
        logger.exception("fact extraction run %s failed", run_id)
        return False


async def process_pending_extraction_runs(db: Session, user_id: int) -> int:
    _reclaim_stale_running_runs(db, user_id)
    db.commit()
    set_tenant_context(db, user_id)
    db.expire_all()
    now = datetime.now(timezone.utc)
    run_ids = list(
        db.scalars(
            select(FactExtractionRun.id)
            .where(
                FactExtractionRun.user_id == user_id,
                FactExtractionRun.status.in_(("pending", "failed")),
                FactExtractionRun.next_retry_at <= now,
            )
            .order_by(FactExtractionRun.id.asc())
            .limit(5)
        ).all()
    )
    processed = 0
    for run_id in run_ids:
        if await process_extraction_run(db, run_id, user_id):
            processed += 1
    return processed


async def run_session_extraction(
    db: Session,
    user_id: int,
    session_id: str,
) -> int | None:
    run = enqueue_extraction_run(
        db, user_id, session_id=session_id, scope="session"
    )
    run_id = run.id
    db.commit()
    ok = await process_extraction_run(db, run_id, user_id)
    return run_id if ok else None


async def run_global_extraction(db: Session, user_id: int) -> int | None:
    run = enqueue_extraction_run(db, user_id, session_id=None, scope="global")
    run_id = run.id
    db.commit()
    ok = await process_extraction_run(db, run_id, user_id)
    return run_id if ok else None


def _offload_already_succeeded(
    db: Session,
    user_id: int,
    memory_episode_id: int,
) -> bool:
    runs = db.scalars(
        select(FactExtractionRun).where(
            FactExtractionRun.user_id == user_id,
            FactExtractionRun.scope == "offload",
            FactExtractionRun.status == "succeeded",
        )
    ).all()
    for row in runs:
        payload = row.payload_json or {}
        if int(payload.get("memory_episode_id") or 0) == memory_episode_id:
            return True
    return False


async def run_offload_extraction(
    db: Session,
    user_id: int,
    session_id: str,
    memory_episode_id: int,
) -> int | None:
    if _offload_already_succeeded(db, user_id, memory_episode_id):
        return None
    run = enqueue_extraction_run(
        db,
        user_id,
        session_id=session_id,
        scope="offload",
        memory_episode_id=memory_episode_id,
    )
    run_id = run.id
    db.commit()
    ok = await process_extraction_run(db, run_id, user_id)
    return run_id if ok else None


async def maybe_schedule_extractions(
    db: Session,
    user_id: int,
    session_id: str,
    *,
    user_message_count: int,
    session_message_count: int,
) -> None:
    """Enqueue and process session/global extraction based on counters."""
    session_n = settings.fact_extraction_session_every_n
    global_n = settings.fact_extraction_global_every_n

    if session_n >= 1 and session_message_count % session_n == 0:
        await run_session_extraction(db, user_id, session_id)

    if global_n >= 1 and user_message_count % global_n == 0:
        await run_global_extraction(db, user_id)

    await process_pending_extraction_runs(db, user_id)
