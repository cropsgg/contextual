"""Session metadata API tests (Phase B)."""

import uuid

from sqlalchemy import func, select

from app.models.chat_session import ChatSession
from app.models.episode import Episode
from app.services.database import SessionLocal
from app.services.rls import set_tenant_context
from tests.conftest import auth, user_a


def test_session_create_list_rename_delete(client, user_a):
    user, token = user_a

    create = client.post(
        "/api/history/sessions",
        headers=auth(token),
    )
    assert create.status_code == 201, create.text
    body = create.json()
    session_id = body["session_id"]
    assert body["title"] == "New conversation"

    listed = client.get("/api/history/sessions", headers=auth(token))
    assert listed.status_code == 200
    ids = [s["session_id"] for s in listed.json()]
    assert session_id in ids

    renamed = client.patch(
        f"/api/history/sessions/{session_id}",
        json={"title": "My renamed chat"},
        headers=auth(token),
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "My renamed chat"

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
        db.commit()

    deleted = client.delete(
        f"/api/history/sessions/{session_id}",
        headers=auth(token),
    )
    assert deleted.status_code == 200
    assert deleted.json()["episodes_deleted"] >= 1

    with SessionLocal() as db:
        set_tenant_context(db, user.id)
        ep_count = db.scalar(
            select(func.count())
            .select_from(Episode)
            .where(
                Episode.user_id == user.id,
                Episode.session_id == session_id,
            )
        )
        row = db.scalar(
            select(ChatSession).where(
                ChatSession.user_id == user.id,
                ChatSession.session_id == session_id,
            )
        )
    assert ep_count == 0
    assert row is None


def test_delete_session_does_not_remove_facts(client, user_a):
    user, token = user_a
    session_id = str(uuid.uuid4())

    create = client.post("/api/history/sessions", headers=auth(token))
    assert create.status_code == 201
    session_id = create.json()["session_id"]

    with SessionLocal() as db:
        set_tenant_context(db, user.id)
        from app.models.user_fact import UserFact

        db.add(
            UserFact(
                user_id=user.id,
                fact_key="color",
                fact_value="teal",
            )
        )
        db.add(
            Episode(
                user_id=user.id,
                session_id=session_id,
                episode_kind="message",
                role="user",
                content="msg",
            )
        )
        db.commit()

    with SessionLocal() as db:
        set_tenant_context(db, user.id)
        from app.models.user_fact import UserFact

        fact_before = db.scalar(
            select(func.count())
            .select_from(UserFact)
            .where(UserFact.user_id == user.id)
        )

    resp = client.delete(
        f"/api/history/sessions/{session_id}",
        headers=auth(token),
    )
    assert resp.status_code == 200

    with SessionLocal() as db:
        set_tenant_context(db, user.id)
        fact_after = db.scalar(
            select(func.count())
            .select_from(UserFact)
            .where(UserFact.user_id == user.id)
        )
    assert fact_after == fact_before
