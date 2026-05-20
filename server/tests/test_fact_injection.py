"""Selective fact injection."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import get_settings
from app.models.user_fact import UserFact
from app.services.context_manager import assemble_enhanced_context, build_attribution_metadata
from app.services.database import SessionLocal
from app.services.fact_injection import select_facts_for_injection
from app.services.memory_rerank import RerankResult
from app.services.rls import set_tenant_context
from tests.conftest import user_a


@pytest.fixture
def fact_settings(monkeypatch):
    import app.core.config as app_config
    import app.services.context_manager as context_manager_module

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("FACT_INJECTION_MAX", "2")
    get_settings.cache_clear()
    settings = get_settings()
    app_config.settings = settings
    context_manager_module.settings = settings
    yield settings
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_cap_and_pinned(user_a, fact_settings):
    user, _ = user_a

    with SessionLocal() as db:
        set_tenant_context(db, user.id)
        for i in range(5):
            db.add(
                UserFact(
                    user_id=user.id,
                    fact_key=f"key_{i}",
                    fact_value=f"value {i}",
                    confidence=0.5,
                    pinned=(i == 0),
                )
            )
        db.commit()
        set_tenant_context(db, user.id)

        selected, suppressed = await select_facts_for_injection(
            db, user.id, "query", query_vector=None
        )
        assert len(selected) <= 2
        assert any(f.pinned for f in selected)
        assert suppressed >= 3


@pytest.mark.asyncio
async def test_attribution_only_injected_facts(user_a, fact_settings):
    user, _ = user_a
    session_id = f"facts-{uuid.uuid4().hex[:8]}"

    with SessionLocal() as db:
        set_tenant_context(db, user.id)
        for i in range(4):
            db.add(
                UserFact(
                    user_id=user.id,
                    fact_key=f"k{i}",
                    fact_value=f"v{i}",
                )
            )
        db.commit()
        set_tenant_context(db, user.id)

        fake_vec = [0.0] * get_settings().gemini_embedding_dimensions
        with (
            patch(
                "app.services.context_manager._embed_query_with_retry",
                new_callable=AsyncMock,
                return_value=fake_vec,
            ),
            patch(
                "app.services.fact_injection.embed_text",
                new_callable=AsyncMock,
                return_value=fake_vec,
            ),
            patch(
                "app.services.context_manager.rerank_memories",
                new_callable=AsyncMock,
                return_value=RerankResult([], False, False, 0.0),
            ),
        ):
            ctx = await assemble_enhanced_context(db, user.id, session_id, "hello")

        meta = build_attribution_metadata(ctx)
        assert len(meta["attribution"]["facts"]) == len(ctx.injected_facts)
        assert len(ctx.injected_facts) <= 2
