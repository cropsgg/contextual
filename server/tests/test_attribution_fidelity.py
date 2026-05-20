"""Attribution matches prompt injection."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import get_settings
from app.models.user_fact import UserFact
from app.services.context_manager import (
    assemble_enhanced_context,
    build_attribution_metadata,
    format_enhanced_system_blocks,
)
from app.services.database import SessionLocal
from app.services.memory_rerank import RerankResult
from app.services.rls import set_tenant_context
from tests.conftest import user_a


@pytest.mark.asyncio
async def test_facts_in_blocks_match_attribution(user_a, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("FACT_INJECTION_MAX", "10")
    get_settings.cache_clear()

    user, _ = user_a
    session_id = f"fid-{uuid.uuid4().hex[:8]}"

    with SessionLocal() as db:
        set_tenant_context(db, user.id)
        db.add(
            UserFact(
                user_id=user.id,
                fact_key="color",
                fact_value="blue",
                confidence=1.0,
            )
        )
        db.commit()
        set_tenant_context(db, user.id)

        vec = [0.2] * get_settings().gemini_embedding_dimensions
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
            ctx = await assemble_enhanced_context(db, user.id, session_id, "hello")

        blocks = format_enhanced_system_blocks(ctx)
        profile = next(b for b in blocks if "<user_profile>" in b["content"])
        meta = build_attribution_metadata(ctx)

        for f in meta["attribution"]["facts"]:
            assert f["fact_key"] in profile["content"]
            assert f["fact_value"] in profile["content"]
