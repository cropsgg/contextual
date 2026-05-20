"""Admin API access control and user management."""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.security import create_access_token, hash_password
from app.models.token_usage_event import TokenUsageEvent
from app.models.user import User
from app.services.database import SessionLocal
from app.services.rls import set_bypass_rls
from tests.conftest import auth


def _make_admin() -> tuple[User, str]:
    email = f"admin_{uuid.uuid4().hex[:8]}@example.com"
    with SessionLocal() as db:
        set_bypass_rls(db)
        user = User(
            email=email,
            hashed_password=hash_password("adminpass123"),
            role="admin",
            expert_preview_enabled=True,
            token_unlimited=True,
        )
        db.add(user)
        db.flush()
        uid = user.id
        db.commit()
        set_bypass_rls(db)
        user = db.get(User, uid)
        assert user is not None
        token = create_access_token(sub=str(user.id))
        return user, token


def test_non_admin_forbidden(client: TestClient, user_a):
    _, token = user_a
    res = client.get("/api/admin/users", headers=auth(token))
    assert res.status_code == 403


def test_admin_list_and_patch_user(client: TestClient, user_a):
    admin_user, admin_token = _make_admin()
    target, target_token = user_a

    try:
        list_res = client.get("/api/admin/users", headers=auth(admin_token))
        assert list_res.status_code == 200
        emails = [u["email"] for u in list_res.json()]
        assert target.email in emails

        stats_res = client.get("/api/admin/stats", headers=auth(admin_token))
        assert stats_res.status_code == 200
        assert stats_res.json()["total_users"] >= 2

        patch_res = client.patch(
            f"/api/admin/users/{target.id}",
            headers=auth(admin_token),
            json={
                "quota_primary_daily": 2_000_000,
                "quota_fallback_daily": 500_000,
                "token_unlimited": False,
            },
        )
        assert patch_res.status_code == 200
        body = patch_res.json()
        assert body["quota_primary_daily"] == 2_000_000
        assert body["quota_fallback_daily"] == 500_000

        with SessionLocal() as db:
            set_bypass_rls(db)
            row = db.get(User, target.id)
            assert row is not None
            assert row.quota_primary_daily == 2_000_000
    finally:
        with SessionLocal() as db:
            set_bypass_rls(db)
            db.execute(delete(TokenUsageEvent).where(TokenUsageEvent.user_id == admin_user.id))
            db.execute(delete(User).where(User.id == admin_user.id))
            db.commit()

    assert target_token  # silence unused
