"""Token quota enforcement and rollover."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models.user import User
from app.services.database import SessionLocal
from app.services.rls import set_bypass_rls, set_tenant_context
from app.services.deepseek import ChatCompletionUsage
from app.services.token_counter import count_chat_messages_tokens
from app.services.token_quota import (
    assert_chat_allowed,
    assert_preflight_estimated_tokens,
    deepseek_quota_blocked,
    fill_usage_from_estimate,
    maybe_rollover_user,
    record_chat_usage,
    resolve_chat_tier,
    total_daily_remaining,
    utc_today,
)
from tests.conftest import auth


def test_utc_rollover_resets_daily_counters(db):
    set_bypass_rls(db)
    user = User(
        email=f"quota_{__import__('uuid').uuid4().hex[:8]}@example.com",
        hashed_password="x",
        tokens_primary_today=500_000,
        tokens_fallback_today=100_000,
        usage_period_date=utc_today() - timedelta(days=1),
    )
    db.add(user)
    db.flush()
    uid = user.id
    db.commit()
    set_tenant_context(db, uid)
    user = db.get(User, uid)
    assert user is not None

    maybe_rollover_user(db, user)
    db.commit()
    set_tenant_context(db, uid)
    user = db.get(User, uid)
    assert user is not None

    assert user.tokens_primary_today == 0
    assert user.tokens_fallback_today == 0
    assert user.usage_period_date == utc_today()


def test_resolve_tier_fallback_when_primary_exhausted(db):
    set_bypass_rls(db)
    user = User(
        email=f"tier_{__import__('uuid').uuid4().hex[:8]}@example.com",
        hashed_password="x",
        quota_primary_daily=100,
        quota_fallback_daily=200,
        tokens_primary_today=100,
        tokens_fallback_today=50,
        usage_period_date=utc_today(),
    )
    db.add(user)
    db.flush()
    uid = user.id
    db.commit()
    set_tenant_context(db, uid)
    user = db.get(User, uid)
    assert user is not None

    res = resolve_chat_tier(user)
    assert res.tier == "fallback"
    assert res.model  # configured fallback model id


def test_assert_chat_allowed_blocks_both_tiers(db):
    set_bypass_rls(db)
    user = User(
        email=f"block_{__import__('uuid').uuid4().hex[:8]}@example.com",
        hashed_password="x",
        quota_primary_daily=10,
        quota_fallback_daily=10,
        tokens_primary_today=10,
        tokens_fallback_today=10,
        usage_period_date=utc_today(),
    )
    db.add(user)
    db.flush()
    uid = user.id
    db.commit()
    set_tenant_context(db, uid)
    user = db.get(User, uid)
    assert user is not None

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        assert_chat_allowed(db, user)
    db.rollback()
    assert exc.value.status_code == 429
    detail = exc.value.detail
    assert isinstance(detail, dict)
    assert detail.get("error") == "quota_exceeded"


def test_record_chat_usage_increments_counters(db):
    set_bypass_rls(db)
    user = User(
        email=f"rec_{__import__('uuid').uuid4().hex[:8]}@example.com",
        hashed_password="x",
        usage_period_date=utc_today(),
    )
    db.add(user)
    db.flush()
    uid = user.id
    db.commit()
    set_tenant_context(db, uid)

    record_chat_usage(
        db,
        uid,
        tier="primary",
        model="deepseek-v4-flash",
        prompt_tokens=100,
        completion_tokens=50,
        session_id="sess-1",
    )
    db.commit()

    set_tenant_context(db, uid)
    refreshed = db.get(User, uid)
    assert refreshed is not None
    assert refreshed.tokens_primary_today == 150
    assert refreshed.tokens_primary_lifetime == 150


def test_total_daily_remaining_sums_tiers(db):
    set_bypass_rls(db)
    user = User(
        email=f"rem_{__import__('uuid').uuid4().hex[:8]}@example.com",
        hashed_password="x",
        quota_primary_daily=1000,
        quota_fallback_daily=500,
        tokens_primary_today=800,
        tokens_fallback_today=100,
        usage_period_date=utc_today(),
    )
    db.add(user)
    db.flush()
    assert total_daily_remaining(user) == 200 + 400


def test_assert_preflight_rejects_large_estimate(db):
    set_bypass_rls(db)
    user = User(
        email=f"pre_{__import__('uuid').uuid4().hex[:8]}@example.com",
        hashed_password="x",
        quota_primary_daily=500,
        quota_fallback_daily=0,
        tokens_primary_today=0,
        tokens_fallback_today=0,
        usage_period_date=utc_today(),
    )
    db.add(user)
    db.flush()
    uid = user.id
    db.commit()
    set_tenant_context(db, uid)
    user = db.get(User, uid)
    assert user is not None

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        assert_preflight_estimated_tokens(db, user, 600)
    db.rollback()
    assert exc.value.status_code == 429
    detail = exc.value.detail
    assert isinstance(detail, dict)
    assert detail.get("error") == "quota_insufficient"


def test_fill_usage_from_estimate_uses_tiktoken():
    usage = ChatCompletionUsage()
    messages = [
        {"role": "user", "content": "hello world"},
        {"role": "assistant", "content": "hi"},
    ]
    filled = fill_usage_from_estimate(
        usage, prompt_messages=messages, completion_text="response"
    )
    assert filled is True
    assert usage.total_tokens > 0
    assert usage.total_tokens >= count_chat_messages_tokens(messages)


def test_deepseek_quota_blocked_when_both_tiers_full(db):
    set_bypass_rls(db)
    user = User(
        email=f"blk_{__import__('uuid').uuid4().hex[:8]}@example.com",
        hashed_password="x",
        quota_primary_daily=10,
        quota_fallback_daily=10,
        tokens_primary_today=10,
        tokens_fallback_today=10,
        usage_period_date=utc_today(),
    )
    db.add(user)
    assert deepseek_quota_blocked(user) is True


def test_chat_returns_429_when_quota_exhausted(client: TestClient, user_a):
    user, token = user_a
    with SessionLocal() as s:
        set_bypass_rls(s)
        row = s.get(User, user.id)
        assert row is not None
        row.quota_primary_daily = 1
        row.quota_fallback_daily = 1
        row.tokens_primary_today = 1
        row.tokens_fallback_today = 1
        row.usage_period_date = utc_today()
        s.commit()

    sid = __import__("uuid").uuid4()
    res = client.post(
        "/api/chat",
        headers=auth(token),
        json={"message": "hello", "session_id": str(sid)},
    )
    assert res.status_code == 429
    body = res.json()
    assert body["detail"]["error"] == "quota_exceeded"
