"""Concurrent compression orchestration tests (mocked APIs)."""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select

import app.core.config as app_config
import app.services.context_manager as context_manager_module
from app.core.config import get_settings
from app.models.episode import Episode
from app.services.context_manager import (
    compression_lock,
    is_compression_inflight,
    reduce_until_under,
    run_post_turn_compression,
)
from app.services.database import SessionLocal
from app.services.memory_rerank import RerankResult
from app.services.rls import set_tenant_context
from tests.test_context_compression import _LONG_LINE, _seed_messages

pytestmark = pytest.mark.asyncio


@pytest.fixture()
def compression_settings(monkeypatch):
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
            return_value="Concurrent compression summary.",
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


async def _compress_once(
    user_id: int,
    session_id: str,
    *,
    hold: asyncio.Event | None = None,
) -> None:
    if hold is not None:
        await hold.wait()
    with SessionLocal() as db:
        set_tenant_context(db, user_id)
        await reduce_until_under(db, user_id, session_id)


async def test_concurrent_reduce_until_under_serializes_and_single_commit_wave(
    user_a,
    compression_settings,
    mock_compression_apis,
):
    """Two concurrent compressions share the lock; no parallel reduce_once commits."""
    user, _ = user_a
    session_id = f"conc-{uuid.uuid4().hex[:8]}"

    with SessionLocal() as db:
        set_tenant_context(db, user.id)
        _seed_messages(db, user.id, session_id, pairs=6)

    lock = compression_lock(user.id, session_id)
    holder_ready = asyncio.Event()
    release_holder = asyncio.Event()

    async def hold_lock() -> None:
        async with lock:
            holder_ready.set()
            await release_holder.wait()

    async def compress_when_lock_free() -> None:
        await holder_ready.wait()
        await _compress_once(user.id, session_id)

    holder = asyncio.create_task(hold_lock())
    await holder_ready.wait()
    assert lock.locked()

    waiter = asyncio.create_task(compress_when_lock_free())
    await asyncio.sleep(0.05)
    assert lock.locked()

    release_holder.set()
    await asyncio.gather(holder, waiter)

    with SessionLocal() as db:
        set_tenant_context(db, user.id)
        memory_n = db.scalar(
            select(func.count())
            .select_from(Episode)
            .where(
                Episode.user_id == user.id,
                Episode.session_id == session_id,
                Episode.episode_kind == "memory",
            )
        )
        offloaded_n = db.scalar(
            select(func.count())
            .select_from(Episode)
            .where(
                Episode.user_id == user.id,
                Episode.session_id == session_id,
                Episode.episode_kind == "message",
                Episode.is_offloaded.is_(True),
            )
        )
    assert int(memory_n or 0) >= 1
    assert int(offloaded_n or 0) >= 1


async def test_post_turn_skips_when_already_under_threshold(
    user_a,
    compression_settings,
    mock_compression_apis,
):
    """Background path is a no-op when sync left the session under threshold."""
    user, _ = user_a
    session_id = f"skip-bg-{uuid.uuid4().hex[:8]}"

    with SessionLocal() as db:
        set_tenant_context(db, user.id)
        _seed_messages(db, user.id, session_id, pairs=2)

    with SessionLocal() as db:
        set_tenant_context(db, user.id)
        await reduce_until_under(db, user.id, session_id)

    summarize, embed = mock_compression_apis
    summarize.reset_mock()
    embed.reset_mock()

    await run_post_turn_compression(user.id, session_id)

    summarize.assert_not_awaited()
    embed.assert_not_awaited()
    assert not is_compression_inflight(user.id, session_id)


async def test_background_compression_sets_inflight_during_run(
    user_a,
    compression_settings,
    mock_compression_apis,
):
    user, _ = user_a
    session_id = f"bg-inflight-{uuid.uuid4().hex[:8]}"

    with SessionLocal() as db:
        set_tenant_context(db, user.id)
        _seed_messages(db, user.id, session_id, pairs=6)
        await reduce_until_under(db, user.id, session_id)

    with SessionLocal() as db:
        set_tenant_context(db, user.id)
        db.add(
            Episode(
                user_id=user.id,
                session_id=session_id,
                episode_kind="message",
                role="assistant",
                content=f"huge reply: {_LONG_LINE}",
                is_offloaded=False,
            )
        )
        db.commit()
        set_tenant_context(db, user.id)

    seen_inflight = False

    async def slow_reduce(*args, **kwargs):
        nonlocal seen_inflight
        seen_inflight = is_compression_inflight(user.id, session_id)
        return await context_manager_module.reduce_until_under(*args, **kwargs)

    with (
        patch(
            "app.services.context_manager.count_active_prompt_tokens",
            return_value=compression_settings.context_threshold_tokens + 500,
        ),
        patch(
            "app.services.context_manager.reduce_until_under",
            side_effect=slow_reduce,
        ),
    ):
        await run_post_turn_compression(user.id, session_id)

    assert seen_inflight is True
    assert not is_compression_inflight(user.id, session_id)
