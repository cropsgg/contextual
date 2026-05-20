"""Retrieval bundle cache serialize round-trip."""

from __future__ import annotations

import uuid

import pytest

from app.models.episode import Episode
from app.services.database import SessionLocal
from app.services.retrieval_bundle_cache import (
    build_retrieval_cache_key,
    compute_version_token,
    deserialize_assembled_context,
    get_cached_assembled_context,
    serialize_assembled_context,
    store_assembled_context_cache,
)
from app.services.retrieval_status import AssembledContext, RetrievalMode, RetrievalOutcome
from app.services.rls import set_bypass_rls, set_tenant_context
from tests.conftest import user_a


def _minimal_context() -> AssembledContext:
    return AssembledContext(
        injected_facts=[],
        cross_session_memories=[],
        in_session_memories=[],
        retrieval=RetrievalOutcome(
            mode=RetrievalMode.FULL,
            cross_session_memory_available=True,
            embed_succeeded=True,
            keyword_fallback_used=False,
            reranked=False,
            rerank_fallback=False,
        ),
    )


def test_serialize_roundtrip():
    ctx = _minimal_context()
    data = serialize_assembled_context(ctx)
    restored = deserialize_assembled_context(data)
    assert restored.retrieval.mode == ctx.retrieval.mode


@pytest.mark.asyncio
async def test_bundle_cache_invalidation_on_message(user_a):
    user, _ = user_a
    session_id = f"bundle-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as db:
        set_tenant_context(db, user.id)
        db.add(
            Episode(
                user_id=user.id,
                session_id=session_id,
                episode_kind="message",
                role="user",
                content="hello",
            )
        )
        db.commit()
        set_tenant_context(db, user.id)
        token = compute_version_token(db, user.id, session_id)
        key = build_retrieval_cache_key(user.id, session_id, "query", token)
        store_assembled_context_cache(db, key, _minimal_context())
        db.commit()
        assert get_cached_assembled_context(db, key) is not None

        db.add(
            Episode(
                user_id=user.id,
                session_id=session_id,
                episode_kind="message",
                role="assistant",
                content="hi",
            )
        )
        from app.services.cache_invalidation import invalidate_user_caches

        invalidate_user_caches(db, user.id, session_id)
        db.commit()
        set_bypass_rls(db)
        assert get_cached_assembled_context(db, key) is None
