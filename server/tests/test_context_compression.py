"""Phase 2 compression integration tests (mocked summarize/embed; no live API keys)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

import app.core.config as app_config
import app.services.context_manager as context_manager_module
from app.core.config import get_settings
from app.models.episode import Episode
from app.models.user_fact import UserFact
from app.services.compression_errors import CompressionError, CompressionFailureReason
from app.services.context_manager import (
    build_completion_messages,
    build_enhanced_context,
    count_active_prompt_tokens,
    count_active_transcript_tokens,
    load_active_message_episodes,
    reduce_until_under,
)
from app.services.memory_rerank import RerankResult
from app.services.database import SessionLocal
from app.services.rls import set_tenant_context

# Repeatable filler: ~200 tokens per message (cl100k approximation).
_LONG_LINE = "word " * 180


def _seed_messages(
    db,
    user_id: int,
    session_id: str,
    pairs: int,
) -> list[Episode]:
    """Insert user/assistant pairs with long content."""
    episodes: list[Episode] = []
    for i in range(pairs):
        for role in ("user", "assistant"):
            ep = Episode(
                user_id=user_id,
                session_id=session_id,
                episode_kind="message",
                role=role,
                content=f"{role} turn {i}: {_LONG_LINE}",
                is_offloaded=False,
            )
            db.add(ep)
            episodes.append(ep)
    db.commit()
    # RLS tenant GUC is transaction-local; restore after commit.
    set_tenant_context(db, user_id)
    return episodes


@pytest.fixture()
def compression_settings(monkeypatch):
    """Low threshold and fast embed retries for deterministic tests."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")
    monkeypatch.setenv("CONTEXT_THRESHOLD_TOKENS", "800")
    monkeypatch.setenv("MIN_RECENT_MESSAGES_TO_KEEP", "2")
    monkeypatch.setenv("COMPRESSION_EMBED_MAX_RETRIES", "1")
    monkeypatch.setenv("COMPRESSION_EMBED_RETRY_BASE_SECONDS", "0")
    get_settings.cache_clear()
    settings = get_settings()
    app_config.settings = settings
    context_manager_module.settings = settings
    yield settings
    get_settings.cache_clear()


@pytest.fixture()
def mock_compression_apis(compression_settings):
    fake_vector = [0.1] * compression_settings.gemini_embedding_dimensions
    with (
        patch(
            "app.services.context_manager.summarize_conversation",
            new_callable=AsyncMock,
            return_value="Compressed summary of offloaded turns.",
        ) as summarize,
        patch(
            "app.services.context_manager.embed_text",
            new_callable=AsyncMock,
            return_value=fake_vector,
        ) as embed,
        patch(
            "app.services.context_manager._embed_query_with_retry",
            new_callable=AsyncMock,
            return_value=fake_vector,
        ),
        patch(
            "app.services.context_manager.rerank_memories",
            new_callable=AsyncMock,
            return_value=RerankResult([], False, False, 0.0),
        ),
    ):
        yield summarize, embed


@pytest.mark.asyncio
async def test_compression_offloads_messages_and_creates_memory(
    user_a,
    compression_settings,
    mock_compression_apis,
):
    user, _token = user_a
    session_id = f"compress-{uuid.uuid4().hex[:8]}"
    summarize, embed = mock_compression_apis

    with SessionLocal() as db:
        set_tenant_context(db, user.id)
        _seed_messages(db, user.id, session_id, pairs=6)
        tokens_before = count_active_transcript_tokens(db, user.id, session_id)
        assert tokens_before > compression_settings.context_threshold_tokens

        metrics = await reduce_until_under(db, user.id, session_id)

        assert metrics.compression_attempted is True
        assert metrics.compression_succeeded is True
        assert metrics.failure_reason is None
        assert metrics.rounds >= 1

        set_tenant_context(db, user.id)
        offloaded = db.scalars(
            select(Episode).where(
                Episode.user_id == user.id,
                Episode.session_id == session_id,
                Episode.episode_kind == "message",
                Episode.is_offloaded.is_(True),
            )
        ).all()
        assert len(offloaded) >= 1

        memory_rows = db.scalars(
            select(Episode).where(
                Episode.user_id == user.id,
                Episode.session_id == session_id,
                Episode.episode_kind == "memory",
            )
        ).all()
        assert len(memory_rows) >= 1
        assert memory_rows[0].embedding is not None
        assert memory_rows[0].content == "Compressed summary of offloaded turns."

        tokens_after = count_active_transcript_tokens(db, user.id, session_id)
        assert tokens_after <= compression_settings.context_threshold_tokens

    summarize.assert_awaited()
    embed.assert_awaited()


@pytest.mark.asyncio
async def test_build_completion_messages_after_expire_all(
    user_a,
    compression_settings,
    mock_compression_apis,
):
    """Regression: enhanced context with facts must survive expire_all after compression."""
    user, _token = user_a
    session_id = f"expire-{uuid.uuid4().hex[:8]}"

    with SessionLocal() as db:
        set_tenant_context(db, user.id)
        _seed_messages(db, user.id, session_id, pairs=6)
        db.add(
            UserFact(
                user_id=user.id,
                fact_key="city",
                fact_value="Austin",
                confidence=1.0,
                source_session_id=session_id,
            )
        )
        db.commit()
        set_tenant_context(db, user.id)

        enhanced = await build_enhanced_context(db, user.id, session_id, "hello")
        await reduce_until_under(db, user.id, session_id, enhanced=enhanced)
        db.expire_all()
        set_tenant_context(db, user.id)
        enhanced = await build_enhanced_context(db, user.id, session_id, "hello")
        messages = build_completion_messages(
            db, user.id, session_id, enhanced=enhanced
        )

        assert any("Austin" in m.get("content", "") for m in messages)
        assert any(
            m.get("role") == "system" and "Compressed context" in m.get("content", "")
            for m in messages
        )
        roles = [m["role"] for m in messages if m["role"] in ("user", "assistant")]
        assert len(roles) >= 1


@pytest.mark.asyncio
async def test_reduce_until_under_stops_at_min_recent_messages(
    user_a,
    compression_settings,
    mock_compression_apis,
    monkeypatch,
):
    """Cannot offload further when min_recent_messages_to_keep blocks progress."""
    user, _token = user_a
    session_id = f"guard-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("MIN_RECENT_MESSAGES_TO_KEEP", "20")
    get_settings.cache_clear()
    settings = get_settings()
    context_manager_module.settings = settings

    with SessionLocal() as db:
        set_tenant_context(db, user.id)
        _seed_messages(db, user.id, session_id, pairs=5)
        active_before = load_active_message_episodes(db, user.id, session_id)
        assert len(active_before) <= get_settings().min_recent_messages_to_keep

        with pytest.raises(CompressionError) as exc_info:
            await reduce_until_under(db, user.id, session_id)

        err = exc_info.value
        assert err.reason == CompressionFailureReason.NO_PROGRESS
        assert err.metrics.compression_attempted is True
        assert err.metrics.compression_succeeded is False
        assert err.metrics.rounds == 0

        offloaded = db.scalars(
            select(Episode).where(
                Episode.user_id == user.id,
                Episode.session_id == session_id,
                Episode.is_offloaded.is_(True),
            )
        ).all()
        assert len(offloaded) == 0

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_summarize_failure_raises_compression_error(
    user_a,
    compression_settings,
    mock_compression_apis,
):
    user, _token = user_a
    session_id = f"summ-fail-{uuid.uuid4().hex[:8]}"
    summarize, _embed = mock_compression_apis
    summarize.side_effect = RuntimeError("summarize down")

    with SessionLocal() as db:
        set_tenant_context(db, user.id)
        _seed_messages(db, user.id, session_id, pairs=6)

        with pytest.raises(CompressionError) as exc_info:
            await reduce_until_under(db, user.id, session_id)

        assert exc_info.value.reason == CompressionFailureReason.SUMMARIZE_FAILED
        assert exc_info.value.metrics.failure_reason == "summarize_failed"


@pytest.mark.asyncio
async def test_embed_failure_after_summarize_raises_without_offload(
    user_a,
    compression_settings,
    mock_compression_apis,
):
    user, _token = user_a
    session_id = f"embed-fail-{uuid.uuid4().hex[:8]}"
    _summarize, embed = mock_compression_apis
    embed.side_effect = RuntimeError("embed down")

    with SessionLocal() as db:
        set_tenant_context(db, user.id)
        _seed_messages(db, user.id, session_id, pairs=6)

        with pytest.raises(CompressionError) as exc_info:
            await reduce_until_under(db, user.id, session_id)

        assert exc_info.value.reason == CompressionFailureReason.EMBED_FAILED

        offloaded = db.scalars(
            select(Episode).where(
                Episode.user_id == user.id,
                Episode.session_id == session_id,
                Episode.is_offloaded.is_(True),
            )
        ).all()
        assert len(offloaded) == 0
        memory_rows = db.scalars(
            select(Episode).where(
                Episode.user_id == user.id,
                Episode.session_id == session_id,
                Episode.episode_kind == "memory",
            )
        ).all()
        assert len(memory_rows) == 0


@pytest.mark.asyncio
async def test_missing_gemini_key_raises_when_over_threshold(
    user_a,
    compression_settings,
    mock_compression_apis,
    monkeypatch,
):
    monkeypatch.setenv("GEMINI_API_KEY", "")
    get_settings.cache_clear()
    settings = get_settings()
    context_manager_module.settings = settings
    assert not settings.gemini_api_key

    user, _token = user_a
    session_id = f"no-gemini-{uuid.uuid4().hex[:8]}"

    with SessionLocal() as db:
        set_tenant_context(db, user.id)
        _seed_messages(db, user.id, session_id, pairs=6)

        with pytest.raises(CompressionError) as exc_info:
            await reduce_until_under(db, user.id, session_id)

        assert exc_info.value.reason == CompressionFailureReason.MISSING_GEMINI_KEY

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_compression_uses_transcript_not_packed_tokens(
    user_a,
    compression_settings,
    mock_compression_apis,
):
    """Selective packing keeps packed prompt small; compression still runs on transcript."""
    user, _token = user_a
    session_id = f"selective-{uuid.uuid4().hex[:8]}"

    with SessionLocal() as db:
        set_tenant_context(db, user.id)
        _seed_messages(db, user.id, session_id, pairs=6)

        with patch.object(compression_settings, "selective_context_enabled", True), patch.object(
            compression_settings, "prompt_token_budget", 150
        ), patch.object(compression_settings, "active_retrieval_floor_turns", 2):
            transcript = count_active_transcript_tokens(db, user.id, session_id)
            packed = count_active_prompt_tokens(db, user.id, session_id)
            assert transcript > compression_settings.context_threshold_tokens
            assert packed < transcript

            metrics = await reduce_until_under(db, user.id, session_id)

        assert metrics.compression_attempted is True
        assert metrics.compression_succeeded is True

        set_tenant_context(db, user.id)
        memory_rows = db.scalars(
            select(Episode).where(
                Episode.user_id == user.id,
                Episode.session_id == session_id,
                Episode.episode_kind == "memory",
            )
        ).all()
        assert len(memory_rows) >= 1
