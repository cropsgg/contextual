"""Memory episodes list/delete/clear tests."""

from sqlalchemy import func, select

from app.models.episode import Episode
from app.services.database import SessionLocal
from app.services.rls import set_tenant_context
from tests.conftest import auth


def test_clear_episodic_memory_keeps_messages(client, user_a):
    user, token = user_a
    session_id = "clear-test-session"

    with SessionLocal() as db:
        set_tenant_context(db, user.id)
        db.add(
            Episode(
                user_id=user.id,
                session_id=session_id,
                episode_kind="message",
                role="user",
                content="hello",
            )
        )
        db.add(
            Episode(
                user_id=user.id,
                session_id=session_id,
                episode_kind="memory",
                role="system",
                content="compressed summary",
            )
        )
        db.commit()

    client.post(
        "/api/memory/facts",
        json={"fact_key": "color", "fact_value": "blue"},
        headers=auth(token),
    )

    clear = client.post(
        "/api/memory/clear",
        json={"confirm": "DELETE_ALL_MEMORY"},
        headers=auth(token),
    )
    assert clear.status_code == 200
    body = clear.json()
    assert body["facts_deleted"] >= 1
    assert body["episodes_deleted"] >= 1

    with SessionLocal() as db:
        set_tenant_context(db, user.id)
        msg_count = db.scalar(
            select(func.count(Episode.id)).where(
                Episode.user_id == user.id,
                Episode.episode_kind == "message",
            )
        )
        mem_count = db.scalar(
            select(func.count(Episode.id)).where(
                Episode.user_id == user.id,
                Episode.episode_kind == "memory",
            )
        )
        assert msg_count >= 1
        assert mem_count == 0

    facts = client.get("/api/memory/facts", headers=auth(token))
    assert facts.json() == []
