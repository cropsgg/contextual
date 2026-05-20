"""RLS cross-tenant isolation tests."""

from sqlalchemy import select

from app.models.episode import Episode
from app.models.user_fact import UserFact
from app.services.database import SessionLocal
from app.services.rls import set_tenant_context
from tests.conftest import auth


def test_user_b_cannot_see_user_a_fact(client, user_a, user_b):
    user_a_obj, token_a = user_a
    user_b_obj, token_b = user_b

    create = client.post(
        "/api/memory/facts",
        json={"fact_key": "secret_lang", "fact_value": "Python"},
        headers=auth(token_a),
    )
    assert create.status_code == 201

    list_b = client.get("/api/memory/facts", headers=auth(token_b))
    assert list_b.status_code == 200
    keys = [f["fact_key"] for f in list_b.json()]
    assert "secret_lang" not in keys

    with SessionLocal() as db:
        set_tenant_context(db, user_b_obj.id)
        row = db.scalar(
            select(UserFact).where(UserFact.fact_key == "secret_lang")
        )
        assert row is None


def test_user_b_cannot_see_user_a_memory_episode(client, user_a, user_b):
    user_a_obj, token_a = user_a
    user_b_obj, token_b = user_b

    with SessionLocal() as db:
        set_tenant_context(db, user_a_obj.id)
        ep = Episode(
            user_id=user_a_obj.id,
            session_id="sess-a",
            episode_kind="memory",
            role="system",
            content="Private memory chunk",
        )
        db.add(ep)
        db.flush()
        ep_id = ep.id
        db.commit()
        assert ep_id is not None

    list_b = client.get("/api/memory/episodes", headers=auth(token_b))
    assert list_b.status_code == 200
    ids = [item["id"] for item in list_b.json()["items"]]
    assert ep_id not in ids

    delete_res = client.delete(
        f"/api/memory/episodes/{ep_id}",
        headers=auth(token_b),
    )
    assert delete_res.status_code == 404

    with SessionLocal() as db:
        set_tenant_context(db, user_b_obj.id)
        assert db.get(Episode, ep_id) is None
