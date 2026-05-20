"""Fact extraction run retry bookkeeping."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.models.fact_extraction_run import FactExtractionRun
from app.models.user import User
from app.services.database import SessionLocal
from app.services.fact_extraction_v2 import process_extraction_run
from app.services.rls import set_bypass_rls, set_tenant_context
from tests.conftest import user_a


@pytest.mark.asyncio
async def test_extraction_run_failure_schedules_retry(user_a):
    user, _ = user_a
    with SessionLocal() as db:
        set_tenant_context(db, user.id)
        run = FactExtractionRun(
            user_id=user.id,
            session_id="sess-retry",
            scope="session",
            status="pending",
            attempts=0,
            max_attempts=3,
            next_retry_at=datetime.now(timezone.utc),
            payload_json={},
        )
        db.add(run)
        db.flush()
        run_id = run.id
        db.commit()

    with patch(
        "app.services.fact_extraction_v2._execute_run",
        new_callable=AsyncMock,
        side_effect=RuntimeError("llm down"),
    ):
        with SessionLocal() as db:
            set_tenant_context(db, user.id)
            ok = await process_extraction_run(db, run_id, user.id)
            assert ok is False
            set_bypass_rls(db)
            run = db.get(FactExtractionRun, run_id)
            assert run is not None
            assert run.status == "failed"
            assert run.attempts == 1
            assert run.next_retry_at is not None
            u = db.get(User, user.id)
            assert u is not None
            assert u.fact_extraction_consecutive_failures >= 1


@pytest.mark.asyncio
async def test_extraction_run_dead_after_max_attempts(user_a):
    user, _ = user_a
    with SessionLocal() as db:
        set_tenant_context(db, user.id)
        run = FactExtractionRun(
            user_id=user.id,
            session_id=None,
            scope="global",
            status="pending",
            attempts=2,
            max_attempts=3,
            next_retry_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            payload_json={},
        )
        db.add(run)
        db.flush()
        run_id = run.id
        db.commit()

    with patch(
        "app.services.fact_extraction_v2._execute_run",
        new_callable=AsyncMock,
        side_effect=RuntimeError("still down"),
    ):
        with SessionLocal() as db:
            set_tenant_context(db, user.id)
            await process_extraction_run(db, run_id, user.id)
            set_bypass_rls(db)
            run = db.get(FactExtractionRun, run_id)
            assert run is not None
            assert run.status == "dead"


@pytest.mark.asyncio
async def test_offload_extraction_skips_duplicate_memory_episode(user_a):
    user, _ = user_a
    mem_id = 4242
    with SessionLocal() as db:
        set_tenant_context(db, user.id)
        db.add(
            FactExtractionRun(
                user_id=user.id,
                session_id="sess-offload",
                scope="offload",
                status="succeeded",
                attempts=1,
                max_attempts=3,
                next_retry_at=datetime.now(timezone.utc),
                payload_json={"memory_episode_id": mem_id},
            )
        )
        db.commit()

    with patch(
        "app.services.fact_extraction_v2.process_extraction_run",
        new_callable=AsyncMock,
    ) as proc:
        with SessionLocal() as db:
            set_tenant_context(db, user.id)
            from app.services.fact_extraction_v2 import run_offload_extraction

            out = await run_offload_extraction(
                db, user.id, "sess-offload", mem_id
            )
            assert out is None
        proc.assert_not_called()
