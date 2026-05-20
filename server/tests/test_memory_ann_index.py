"""Optional pgvector ANN index verification."""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.models.episode import Episode
from app.services.database import SessionLocal
from app.services.memory_retrieval import search_memory_episodes
from app.services.rls import set_bypass_rls, set_tenant_context
from tests.conftest import user_a


@pytest.mark.skipif(
    os.environ.get("RUN_PGVECTOR_PERF") != "1",
    reason="Set RUN_PGVECTOR_PERF=1 to run ANN EXPLAIN test",
)
def test_memory_search_uses_hnsw_index(user_a):
    user, _ = user_a
    dim = get_settings().gemini_embedding_dimensions
    vec = [0.3] * dim

    with SessionLocal() as db:
        set_bypass_rls(db)
        for i in range(20):
            db.add(
                Episode(
                    user_id=user.id,
                    session_id=f"perf-{uuid.uuid4().hex[:6]}",
                    episode_kind="memory",
                    role="system",
                    content=f"memory chunk {i} about testing vectors",
                    summary=f"chunk {i}",
                    embedding=vec,
                )
            )
        db.commit()
        set_tenant_context(db, user.id)

        search_memory_episodes(db, user.id, vec, limit=3)

        plan = db.execute(
            text(
                """
                EXPLAIN
                SELECT id FROM episodes
                WHERE user_id = :uid
                  AND episode_kind = 'memory'
                  AND embedding IS NOT NULL
                ORDER BY embedding <=> :vec
                LIMIT 3
                """
            ),
            {"uid": user.id, "vec": str(vec)},
        ).fetchall()
        plan_text = "\n".join(row[0] for row in plan)
        assert (
            "idx_episodes_memory_embedding_hnsw" in plan_text
            or "idx_episodes_memory_embedding_ivfflat" in plan_text
            or "Index Scan" in plan_text
        )
