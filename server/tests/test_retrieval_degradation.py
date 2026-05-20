"""Retrieval degradation and attribution modes."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.models.episode import Episode
from app.models.user_fact import UserFact
from app.services.context_manager import (
    assemble_enhanced_context,
    build_attribution_metadata,
    format_enhanced_system_blocks,
    latest_memory_episode,
)
from app.services.memory_keyword_search import search_memory_episodes_keyword
from app.services.memory_retrieval import search_in_session_memory_chunks_keyword
from app.services.database import SessionLocal
from app.services.memory_rerank import RerankResult
from app.services.retrieval_status import RetrievalMode
from app.services.rls import set_tenant_context
from tests.conftest import user_a


@pytest.fixture
def retrieval_settings(monkeypatch):
    import app.core.config as app_config
    import app.services.context_manager as context_manager_module

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    get_settings.cache_clear()
    settings = get_settings()
    app_config.settings = settings
    context_manager_module.settings = settings
    yield settings
    get_settings.cache_clear()


def _rerank_empty():
    return RerankResult(
        memories=[],
        reranked=False,
        rerank_fallback=False,
        latency_ms=0.0,
    )


@pytest.mark.asyncio
async def test_missing_gemini_key_unavailable(user_a, monkeypatch):
    import app.core.config as app_config
    import app.services.context_manager as context_manager_module

    user, _ = user_a
    monkeypatch.setenv("GEMINI_API_KEY", "")
    get_settings.cache_clear()
    settings = get_settings()
    app_config.settings = settings
    context_manager_module.settings = settings

    with SessionLocal() as db:
        set_tenant_context(db, user.id)
        ctx = await assemble_enhanced_context(
            db, user.id, f"s-{uuid.uuid4().hex[:8]}", "hello"
        )

    assert ctx.retrieval.mode == RetrievalMode.UNAVAILABLE_NO_KEY
    blocks = format_enhanced_system_blocks(ctx)
    past = next(b for b in blocks if "<relevant_past_context>" in b["content"])
    assert "unavailable" in past["content"].lower()


@pytest.mark.asyncio
async def test_embed_fail_keyword_fallback(user_a, retrieval_settings):
    user, _ = user_a
    session_id = f"kw-{uuid.uuid4().hex[:8]}"

    with SessionLocal() as db:
        set_tenant_context(db, user.id)
        db.add(
            Episode(
                user_id=user.id,
                session_id="other-session",
                episode_kind="memory",
                role="system",
                content="User prefers dark mode in the interface",
                summary="User prefers dark mode",
            )
        )
        db.commit()
        set_tenant_context(db, user.id)

        with (
            patch(
                "app.services.context_manager._embed_query_with_retry",
                new_callable=AsyncMock,
                side_effect=RuntimeError("embed down"),
            ),
            patch(
                "app.services.context_manager.rerank_memories",
                new_callable=AsyncMock,
                return_value=_rerank_empty(),
            ),
        ):
            ctx = await assemble_enhanced_context(
                db, user.id, session_id, "dark mode preference"
            )

        assert ctx.retrieval.mode == RetrievalMode.DEGRADED_KEYWORD
        assert ctx.retrieval.keyword_fallback_used is True
        meta = build_attribution_metadata(ctx)
        assert meta["attribution"]["retrieval"]["mode"] == "degraded_keyword"


@pytest.mark.asyncio
async def test_embed_fail_still_retrieves_in_session_keyword(user_a, retrieval_settings):
    """In-session memory uses keyword fallback when query embedding fails."""
    user, _ = user_a
    session_id = f"in-kw-{uuid.uuid4().hex[:8]}"

    with SessionLocal() as db:
        set_tenant_context(db, user.id)
        db.add(
            Episode(
                user_id=user.id,
                session_id=session_id,
                episode_kind="memory",
                role="system",
                content="User prefers beta mode in the interface for this project",
                summary="User prefers beta mode",
            )
        )
        db.flush()
        db.add(
            Episode(
                user_id=user.id,
                session_id=session_id,
                episode_kind="memory",
                role="system",
                content="Latest compressed summary for current session",
                summary="Latest compressed summary",
            )
        )
        db.commit()
        set_tenant_context(db, user.id)

        memory_count = db.scalar(
            select(func.count())
            .select_from(Episode)
            .where(
                Episode.user_id == user.id,
                Episode.session_id == session_id,
                Episode.episode_kind == "memory",
            )
        )
        assert memory_count == 2

        latest = latest_memory_episode(db, user.id, session_id)
        assert latest is not None
        kw_hits = search_memory_episodes_keyword(
            db, user.id, "beta mode", only_session_id=session_id
        )
        assert len(kw_hits) >= 1
        in_kw = search_in_session_memory_chunks_keyword(
            db,
            user.id,
            session_id,
            "beta mode preference",
            exclude_episode_ids={latest.id},
            latest_summary_content=latest.content,
        )
        assert len(in_kw) >= 1

        with (
            patch(
                "app.services.context_manager._embed_query_with_retry",
                new_callable=AsyncMock,
                side_effect=RuntimeError("embed down"),
            ),
            patch(
                "app.services.context_manager.rerank_memories",
                new_callable=AsyncMock,
                return_value=_rerank_empty(),
            ),
        ):
            ctx = await assemble_enhanced_context(
                db, user.id, session_id, "beta mode preference"
            )

        assert len(ctx.in_session_memories) >= 1
        assert ctx.in_session_memories[0].scope == "in_session"
        assert ctx.retrieval.mode == RetrievalMode.DEGRADED_KEYWORD
        assert ctx.retrieval.keyword_fallback_used is True


@pytest.mark.asyncio
async def test_attribution_reflects_mode(user_a, retrieval_settings):
    user, _ = user_a
    session_id = f"attr-{uuid.uuid4().hex[:8]}"

    with SessionLocal() as db:
        set_tenant_context(db, user.id)
        db.add(
            UserFact(
                user_id=user.id,
                fact_key="name",
                fact_value="Alex",
                confidence=1.0,
            )
        )
        db.commit()
        set_tenant_context(db, user.id)

        fake_vec = [0.1] * get_settings().gemini_embedding_dimensions
        with (
            patch(
                "app.services.context_manager._embed_query_with_retry",
                new_callable=AsyncMock,
                return_value=fake_vec,
            ),
            patch(
                "app.services.context_manager.rerank_memories",
                new_callable=AsyncMock,
                return_value=_rerank_empty(),
            ),
        ):
            ctx = await assemble_enhanced_context(db, user.id, session_id, "hi")

        meta = build_attribution_metadata(ctx)
        assert len(meta["attribution"]["facts"]) == 1
        assert meta["attribution"]["facts"][0]["fact_key"] == "name"
        assert "retrieval" in meta["attribution"]
