"""Pytest fixtures for API integration tests."""

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

# Use test database if provided, else default dev DB
os.environ.setdefault(
    "DATABASE_URL",
    os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql+psycopg2://contextual_app:maestro_dev@127.0.0.1:5433/maestro",
    ),
)

from app.core.config import get_settings
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.chat_session import ChatSession
from app.models.episode import Episode
from app.models.token_usage_event import TokenUsageEvent
from app.models.user import User
from app.models.user_fact import UserFact
from app.services.database import SessionLocal, create_db_and_tables, init_extensions, migrate_schema
from app.services.rls import set_bypass_rls, set_tenant_context

get_settings.cache_clear()


@pytest.fixture(scope="session", autouse=True)
def _init_db():
    init_extensions()
    create_db_and_tables()
    migrate_schema()


@pytest.fixture(autouse=True)
def _disable_rate_limiting():
    """Many tests register users via /api/auth/register from one TestClient IP."""
    from app.core.limiter import limiter

    previous = limiter.enabled
    limiter.enabled = False
    try:
        yield
    finally:
        limiter.enabled = previous


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client():
    return TestClient(app)


def _register_user(client: TestClient, email: str, password: str = "testpass123") -> User:
    set_bypass = SessionLocal()
    try:
        set_bypass_rls(set_bypass)
        existing = set_bypass.scalar(select(User).where(User.email == email))
        if existing:
            set_bypass.execute(delete(Episode).where(Episode.user_id == existing.id))
            set_bypass.execute(
                delete(ChatSession).where(ChatSession.user_id == existing.id)
            )
            set_bypass.execute(delete(UserFact).where(UserFact.user_id == existing.id))
            set_bypass.execute(
                delete(TokenUsageEvent).where(TokenUsageEvent.user_id == existing.id)
            )
            set_bypass.execute(delete(User).where(User.id == existing.id))
            set_bypass.commit()
    finally:
        set_bypass.close()

    res = client.post(
        "/api/auth/register",
        json={"email": email, "password": password},
    )
    assert res.status_code == 200, res.text
    with SessionLocal() as s:
        set_bypass_rls(s)
        user = s.scalar(select(User).where(User.email == email))
        assert user is not None
        return user


@pytest.fixture()
def user_a(client: TestClient) -> tuple[User, str]:
    email = f"a_{uuid.uuid4().hex[:8]}@example.com"
    user = _register_user(client, email)
    token = create_access_token(sub=str(user.id))
    return user, token


@pytest.fixture()
def user_b(client: TestClient) -> tuple[User, str]:
    email = f"b_{uuid.uuid4().hex[:8]}@example.com"
    user = _register_user(client, email)
    token = create_access_token(sub=str(user.id))
    return user, token


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
