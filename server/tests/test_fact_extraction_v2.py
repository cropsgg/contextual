"""Fact extraction v2 apply layer."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.user_fact import UserFact
from app.services.database import SessionLocal
from app.services.fact_extraction_apply import apply_fact_changes
from app.services.fact_injection import select_facts_for_injection
from app.services.rls import set_tenant_context
from tests.conftest import user_a


@pytest.mark.asyncio
async def test_apply_upsert_delete_noop_and_pinned(user_a):
    user, _ = user_a
    with SessionLocal() as db:
        set_tenant_context(db, user.id)
        db.add(
            UserFact(
                user_id=user.id,
                fact_key="name",
                canonical_key="name",
                fact_value="Alice",
                confidence=0.9,
                pinned=True,
                status="active",
            )
        )
        db.commit()
        set_tenant_context(db, user.id)

        result = apply_fact_changes(
            db,
            user.id,
            [
                {"action": "noop", "key": "name", "canonical_key": "name"},
                {
                    "action": "delete",
                    "key": "name",
                    "canonical_key": "name",
                    "confidence": 0.95,
                },
                {
                    "action": "upsert",
                    "key": "project",
                    "canonical_key": "project",
                    "value": "Contextual",
                    "confidence": 0.88,
                },
            ],
            session_id=f"s-{uuid.uuid4().hex[:8]}",
        )
        db.commit()
        set_tenant_context(db, user.id)

        assert result.noop >= 1
        assert result.upserted == 1
        assert result.deleted == 0

        name_row = db.scalar(
            select(UserFact).where(
                UserFact.user_id == user.id,
                UserFact.fact_key == "name",
            )
        )
        assert name_row is not None
        assert name_row.status == "active"
        assert name_row.fact_value == "Alice"


@pytest.mark.asyncio
async def test_apply_reactivates_deprecated_fact(user_a):
    user, _ = user_a
    with SessionLocal() as db:
        set_tenant_context(db, user.id)
        db.add(
            UserFact(
                user_id=user.id,
                fact_key="role",
                canonical_key="role",
                fact_value="engineer",
                confidence=0.7,
                status="deprecated",
            )
        )
        db.commit()
        set_tenant_context(db, user.id)

        result = apply_fact_changes(
            db,
            user.id,
            [
                {
                    "action": "upsert",
                    "key": "role",
                    "canonical_key": "role",
                    "value": "staff engineer",
                    "confidence": 0.92,
                }
            ],
            session_id=f"s-{uuid.uuid4().hex[:8]}",
        )
        db.commit()
        set_tenant_context(db, user.id)

        assert result.upserted == 1
        row = db.scalar(
            select(UserFact).where(
                UserFact.user_id == user.id,
                UserFact.fact_key == "role",
            )
        )
        assert row is not None
        assert row.status == "active"
        assert row.fact_value == "staff engineer"


@pytest.mark.asyncio
async def test_deprecated_facts_not_injected(user_a):
    user, _ = user_a
    with SessionLocal() as db:
        set_tenant_context(db, user.id)
        db.add(
            UserFact(
                user_id=user.id,
                fact_key="old",
                fact_value="gone",
                status="deprecated",
            )
        )
        db.add(
            UserFact(
                user_id=user.id,
                fact_key="current",
                fact_value="here",
                status="active",
            )
        )
        db.commit()
        set_tenant_context(db, user.id)
        selected, _ = await select_facts_for_injection(db, user.id, "query")
        keys = {f.fact_key for f in selected}
        assert "current" in keys
        assert "old" not in keys
