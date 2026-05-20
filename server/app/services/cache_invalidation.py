"""Invalidate Postgres retrieval/prompt caches after semantic changes."""

from __future__ import annotations

import logging

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models.prompt_assembly_cache import PromptAssemblyCache
from app.models.retrieval_bundle_cache import RetrievalBundleCache
from app.services.rls import set_bypass_rls

logger = logging.getLogger(__name__)


def invalidate_user_caches(
    db: Session,
    user_id: int,
    session_id: str | None = None,
) -> None:
    """Drop bundle/prompt cache rows for user (and optional session)."""
    set_bypass_rls(db, enabled=True)
    try:
        prefix = f"retrieval:{user_id}:"
        if session_id:
            prefix = f"retrieval:{user_id}:{session_id}:"
        db.execute(
            delete(RetrievalBundleCache).where(
                RetrievalBundleCache.cache_key.startswith(prefix)
            )
        )
        prompt_prefix = f"prompt:{user_id}:"
        if session_id:
            prompt_prefix = f"prompt:{user_id}:{session_id}:"
        db.execute(
            delete(PromptAssemblyCache).where(
                PromptAssemblyCache.cache_key.startswith(prompt_prefix)
            )
        )
        db.flush()
    finally:
        set_bypass_rls(db, enabled=False)
    logger.debug(
        "invalidated caches user_id=%s session_id=%s",
        user_id,
        session_id,
    )
