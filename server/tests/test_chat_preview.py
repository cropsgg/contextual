"""Chat preview endpoint tests."""

from unittest.mock import AsyncMock, patch

from sqlalchemy import func, select

from app.models.episode import Episode
from app.models.user import User
from app.services.database import SessionLocal
from app.services.rls import set_tenant_context
from tests.conftest import auth
from tests.test_context_compression import _LONG_LINE, _seed_messages


def _enable_expert(db, user_id: int) -> None:
    user = db.get(User, user_id)
    assert user is not None
    user.expert_preview_enabled = True
    db.commit()


def test_preview_does_not_increment_message_count(client, user_a):
    user, token = user_a
    session_id = "preview-session"

    with SessionLocal() as db:
        set_tenant_context(db, user.id)
        _enable_expert(db, user.id)
        u = db.get(User, user.id)
        count_before = u.user_message_count if u else 0

    preview = client.post(
        "/api/chat/preview",
        json={"message": "What do you know about me?", "session_id": session_id},
        headers=auth(token),
    )
    assert preview.status_code == 200
    data = preview.json()
    assert "messages" in data
    assert data["token_count"] > 0
    assert any(m["role"] == "user" for m in data["messages"])

    with SessionLocal() as db:
        set_tenant_context(db, user.id)
        u = db.get(User, user.id)
        count_after = u.user_message_count if u else 0

    assert count_after == count_before


def test_preview_forbidden_without_expert_flag(client, user_a):
    user, token = user_a
    resp = client.post(
        "/api/chat/preview",
        json={"message": "hi", "session_id": "no-expert"},
        headers=auth(token),
    )
    assert resp.status_code == 403


def test_preview_does_not_mutate_episodes(client, user_a):
    user, token = user_a
    session_id = "preview-mutate-check"

    with SessionLocal() as db:
        set_tenant_context(db, user.id)
        _enable_expert(db, user.id)
        set_tenant_context(db, user.id)
        _seed_messages(db, user.id, session_id, pairs=6)
        memory_before = db.scalar(
            select(func.count())
            .select_from(Episode)
            .where(
                Episode.user_id == user.id,
                Episode.session_id == session_id,
                Episode.episode_kind == "memory",
            )
        )
        offloaded_before = db.scalar(
            select(func.count())
            .select_from(Episode)
            .where(
                Episode.user_id == user.id,
                Episode.session_id == session_id,
                Episode.is_offloaded.is_(True),
            )
        )

    from app.core.config import settings
    from app.services.compression_errors import CompressionMetrics

    compress_metrics = CompressionMetrics(
        compression_attempted=True,
        compression_succeeded=True,
        failure_reason=None,
        rounds=1,
        active_token_count=settings.context_threshold_tokens,
        context_threshold=settings.context_threshold_tokens,
        projected_offload_count=4,
    )

    with (
        patch(
            "app.api.chat_preview.build_enhanced_context",
            new_callable=AsyncMock,
        ) as mock_enhanced,
        patch(
            "app.api.chat_preview.count_active_prompt_tokens",
            return_value=settings.context_threshold_tokens + 500,
        ),
        patch(
            "app.api.chat_preview.reduce_until_under",
            new_callable=AsyncMock,
            return_value=compress_metrics,
        ),
    ):
        from app.services.retrieval_status import AssembledContext, RetrievalMode, RetrievalOutcome

        mock_enhanced.return_value = AssembledContext(
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
        preview = client.post(
            "/api/chat/preview",
            json={"message": f"preview probe {_LONG_LINE}", "session_id": session_id},
            headers=auth(token),
        )

    assert preview.status_code == 200
    body = preview.json()
    assert body.get("would_compress") is True
    assert body.get("projected_offload_count", 0) >= 1

    with SessionLocal() as db:
        set_tenant_context(db, user.id)
        memory_after = db.scalar(
            select(func.count())
            .select_from(Episode)
            .where(
                Episode.user_id == user.id,
                Episode.session_id == session_id,
                Episode.episode_kind == "memory",
            )
        )
        offloaded_after = db.scalar(
            select(func.count())
            .select_from(Episode)
            .where(
                Episode.user_id == user.id,
                Episode.session_id == session_id,
                Episode.is_offloaded.is_(True),
            )
        )

    assert memory_after == memory_before
    assert offloaded_after == offloaded_before
