"""In-session memory chunk retrieval."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import get_settings
from app.models.episode import Episode
from app.services.context_manager import (
    assemble_enhanced_context,
    build_completion_messages,
    latest_memory_episode,
)
from app.services.database import SessionLocal
from app.services.memory_rerank import RerankResult
from app.services.rls import set_tenant_context
from tests.conftest import user_a


@pytest.fixture
def session_settings(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("IN_SESSION_MEMORY_FINAL_K", "1")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_multi_chunk_in_session_excludes_latest(user_a, session_settings):
    user, _ = user_a
    session_id = f"multi-{uuid.uuid4().hex[:8]}"
    dim = get_settings().gemini_embedding_dimensions
    vec = [0.5] * dim

    with SessionLocal() as db:
        set_tenant_context(db, user.id)
        old = Episode(
            user_id=user.id,
            session_id=session_id,
            episode_kind="memory",
            role="system",
            content="Older chunk about project alpha",
            summary="Older chunk about project alpha",
            embedding=vec,
        )
        latest = Episode(
            user_id=user.id,
            session_id=session_id,
            episode_kind="memory",
            role="system",
            content="Latest compressed summary for current session",
            summary="Latest compressed summary",
            embedding=vec,
        )
        db.add(old)
        db.flush()
        db.add(latest)
        db.commit()
        set_tenant_context(db, user.id)

        latest_row = latest_memory_episode(db, user.id, session_id)
        assert latest_row is not None
        assert "Latest compressed summary" in (latest_row.content or "")

        with (
            patch(
                "app.services.context_manager._embed_query_with_retry",
                new_callable=AsyncMock,
                return_value=vec,
            ),
            patch(
                "app.services.context_manager.rerank_memories",
                new_callable=AsyncMock,
                return_value=RerankResult([], False, False, 0.0),
            ),
        ):
            ctx = await assemble_enhanced_context(
                db, user.id, session_id, "tell me about project alpha"
            )

        assert len(ctx.in_session_memories) <= 1
        for m in ctx.in_session_memories:
            assert m.episode_id != latest.id

        messages = build_completion_messages(db, user.id, session_id, enhanced=ctx)
        compressed = [
            m for m in messages if "Compressed context" in m.get("content", "")
        ]
        assert len(compressed) == 1
        assert "Latest compressed summary" in compressed[0]["content"]
        in_sess_blocks = [
            m for m in messages if "<in_session_memory>" in m.get("content", "")
        ]
        if ctx.in_session_memories:
            assert len(in_sess_blocks) == 1
            assert "project alpha" in in_sess_blocks[0]["content"].lower()
