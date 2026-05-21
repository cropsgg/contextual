"""Optional Postgres cache for build_completion_messages output."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.episode import Episode
from app.models.prompt_assembly_cache import PromptAssemblyCache
from app.models.user_fact import UserFact
from app.services.retrieval_status import EnhancedContext
from app.services.rls import set_bypass_rls
from app.services.token_counter import count_chat_messages_tokens

logger = logging.getLogger(__name__)


def _prompt_cache_key(
    db: Session,
    user_id: int,
    session_id: str,
    enhanced: EnhancedContext | None,
    current_query: str = "",
) -> str:
    from app.services.context_manager import (
        latest_memory_episode,
        load_active_message_episodes,
    )

    active_count = len(load_active_message_episodes(db, user_id, session_id))
    mem = latest_memory_episode(db, user_id, session_id)
    mem_id = mem.id if mem else 0
    facts_version = db.scalar(
        select(func.max(UserFact.updated_at)).where(
            UserFact.user_id == user_id,
            UserFact.status == "active",
        )
    )
    fv = facts_version.isoformat() if facts_version else "none"
    enhanced_part = ""
    if enhanced is not None:
        enhanced_part = hashlib.sha256(
            str(len(enhanced.injected_facts)).encode()
        ).hexdigest()[:8]
    query_part = ""
    if settings.selective_context_enabled and current_query.strip():
        query_part = hashlib.sha256(current_query.strip().encode()).hexdigest()[:12]
    return (
        f"prompt:{user_id}:{session_id}:{active_count}:{mem_id}:{fv}:"
        f"{enhanced_part}:{query_part}"
    )


def get_or_build_completion_messages(
    db: Session,
    user_id: int,
    session_id: str,
    enhanced: EnhancedContext | None = None,
    *,
    current_query: str = "",
) -> list[dict[str, str]]:
    from app.services.context_manager import _build_completion_messages_uncached

    if not settings.prompt_assembly_cache_enabled:
        return _build_completion_messages_uncached(
            db,
            user_id,
            session_id,
            enhanced=enhanced,
            current_query=current_query,
        )

    key = _prompt_cache_key(
        db, user_id, session_id, enhanced, current_query=current_query
    )
    set_bypass_rls(db, enabled=True)
    try:
        row = db.scalar(
            select(PromptAssemblyCache).where(
                PromptAssemblyCache.cache_key == key,
                PromptAssemblyCache.expires_at > datetime.now(timezone.utc),
            )
        )
        if row is not None and row.messages_json:
            return list(row.messages_json)

        messages = _build_completion_messages_uncached(
            db,
            user_id,
            session_id,
            enhanced=enhanced,
            current_query=current_query,
        )
        token_count = count_chat_messages_tokens(messages)
        expires = datetime.now(timezone.utc) + timedelta(
            seconds=settings.prompt_assembly_cache_ttl_seconds
        )
        stmt = insert(PromptAssemblyCache).values(
            cache_key=key,
            messages_json=messages,
            token_count=token_count,
            expires_at=expires,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["cache_key"],
            set_={
                "messages_json": stmt.excluded.messages_json,
                "token_count": stmt.excluded.token_count,
                "expires_at": stmt.excluded.expires_at,
            },
        )
        try:
            db.execute(stmt)
            db.flush()
        except Exception:
            logger.debug("prompt assembly cache store failed", exc_info=True)
        return messages
    finally:
        set_bypass_rls(db, enabled=False)
