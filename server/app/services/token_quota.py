"""Daily token quotas, tier resolution, and usage recording."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.token_usage_event import TokenUsageEvent
from app.models.user import User
from app.schemas import AdminUserOut, AdminUserUpdate, PlatformStatsOut, QuotaStatusOut

logger = logging.getLogger(__name__)


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


def next_reset_at_iso() -> str:
    today = utc_today()
    tomorrow = today + timedelta(days=1)
    reset = datetime(
        tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=timezone.utc
    )
    return reset.isoformat().replace("+00:00", "Z")


def user_is_unlimited(user: User) -> bool:
    if (user.role or "").lower() == "admin":
        return True
    return bool(user.token_unlimited)


def tier_for_model(model: str) -> str | None:
    """Map provider model id to quota bucket."""
    if model == settings.deepseek_model_tier_primary:
        return "primary"
    if model == settings.deepseek_model_tier_fallback:
        return "fallback"
    return None


def maybe_rollover_user(db: Session, user: User) -> User:
    """Reset daily counters when UTC calendar day changed."""
    today = utc_today()
    if user.usage_period_date is None:
        user.usage_period_date = today
        db.flush()
        return user
    if user.usage_period_date < today:
        user.tokens_primary_today = 0
        user.tokens_fallback_today = 0
        user.usage_period_date = today
        db.flush()
    return user


def _remaining(used: int, limit: int) -> int:
    return max(0, int(limit) - int(used))


def total_daily_remaining(user: User) -> int | None:
    """Sum of primary + fallback tokens left today; None when unlimited."""
    if user_is_unlimited(user):
        return None
    primary_limit = int(user.quota_primary_daily or settings.default_quota_primary_daily)
    fallback_limit = int(
        user.quota_fallback_daily or settings.default_quota_fallback_daily
    )
    primary_used = int(user.tokens_primary_today or 0)
    fallback_used = int(user.tokens_fallback_today or 0)
    return _remaining(primary_used, primary_limit) + _remaining(
        fallback_used, fallback_limit
    )


def deepseek_quota_blocked(user: User) -> bool:
    """True when both daily tiers are exhausted (auxiliary LLM calls should skip)."""
    if user_is_unlimited(user):
        return False
    return resolve_chat_tier(user).tier == "blocked"


def fill_usage_from_estimate(
    usage: object,
    *,
    prompt_messages: list[dict[str, str]] | None = None,
    completion_text: str | None = None,
) -> bool:
    """When provider usage is missing, approximate with tiktoken. Returns True if filled."""
    from app.services.deepseek import ChatCompletionUsage
    from app.services.token_counter import count_chat_messages_tokens, count_text_tokens

    if not isinstance(usage, ChatCompletionUsage):
        return False
    if usage.total_tokens > 0:
        return False
    if prompt_messages:
        usage.prompt_tokens = count_chat_messages_tokens(prompt_messages)
    if completion_text:
        usage.completion_tokens = count_text_tokens(completion_text)
    return usage.total_tokens > 0


def assert_preflight_estimated_tokens(
    db: Session, user: User, estimated_tokens: int
) -> None:
    """Reject chat when this turn would exceed remaining daily quota."""
    if user_is_unlimited(user):
        return
    if estimated_tokens <= 0:
        return

    locked = db.execute(
        select(User).where(User.id == user.id).with_for_update()
    ).scalar_one()
    maybe_rollover_user(db, locked)
    remaining = total_daily_remaining(locked)
    buffer = max(0, int(settings.quota_preflight_min_remaining))
    if remaining is not None and estimated_tokens + buffer > remaining:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "quota_insufficient",
                "message": (
                    "This message is too large for your remaining daily token quota. "
                    "Try a shorter message or wait until UTC midnight."
                ),
                "estimated_tokens": estimated_tokens,
                "remaining_tokens": remaining,
                "buffer_tokens": buffer,
                "resets_at": next_reset_at_iso(),
            },
        )
    db.flush()


def record_llm_usage(
    db: Session,
    user_id: int,
    *,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    session_id: str | None,
    tier_hint: str | None = None,
) -> None:
    """Record DeepSeek usage for main chat or auxiliary calls (summarize, rerank, facts)."""
    record_chat_usage(
        db,
        user_id,
        tier=tier_hint or tier_for_model(model) or "primary",
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        session_id=session_id or "",
    )


@dataclass(frozen=True)
class TierResolution:
    model: str
    tier: str  # "primary" | "fallback" | "unlimited"
    remaining_primary: int | None
    remaining_fallback: int | None


def resolve_chat_tier(user: User) -> TierResolution:
    """Pick model for next chat without locking (call assert_chat_allowed after rollover)."""
    primary_model = settings.deepseek_model_tier_primary
    fallback_model = settings.deepseek_model_tier_fallback

    if user_is_unlimited(user):
        return TierResolution(
            model=primary_model,
            tier="unlimited",
            remaining_primary=None,
            remaining_fallback=None,
        )

    primary_used = int(user.tokens_primary_today or 0)
    primary_limit = int(user.quota_primary_daily or settings.default_quota_primary_daily)
    fallback_used = int(user.tokens_fallback_today or 0)
    fallback_limit = int(user.quota_fallback_daily or settings.default_quota_fallback_daily)

    rem_primary = _remaining(primary_used, primary_limit)
    rem_fallback = _remaining(fallback_used, fallback_limit)

    if primary_used < primary_limit:
        return TierResolution(
            model=primary_model,
            tier="primary",
            remaining_primary=rem_primary,
            remaining_fallback=rem_fallback,
        )
    if fallback_used < fallback_limit:
        return TierResolution(
            model=fallback_model,
            tier="fallback",
            remaining_primary=0,
            remaining_fallback=rem_fallback,
        )
    return TierResolution(
        model=fallback_model,
        tier="blocked",
        remaining_primary=0,
        remaining_fallback=0,
    )


def assert_chat_allowed(db: Session, user: User) -> TierResolution:
    """Lock user row, rollover, and raise 429 if both tiers exhausted."""
    locked = db.execute(
        select(User).where(User.id == user.id).with_for_update()
    ).scalar_one()
    maybe_rollover_user(db, locked)
    resolution = resolve_chat_tier(locked)
    if resolution.tier == "blocked":
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "quota_exceeded",
                "message": (
                    "Daily token quota exhausted for both model tiers. "
                    "Resets at UTC midnight."
                ),
                "primary_used": int(locked.tokens_primary_today or 0),
                "primary_limit": int(
                    locked.quota_primary_daily or settings.default_quota_primary_daily
                ),
                "fallback_used": int(locked.tokens_fallback_today or 0),
                "fallback_limit": int(
                    locked.quota_fallback_daily or settings.default_quota_fallback_daily
                ),
                "resets_at": next_reset_at_iso(),
            },
        )
    db.flush()
    return resolution


def get_user_quota_status(user: User) -> QuotaStatusOut:
    primary_limit = int(user.quota_primary_daily or settings.default_quota_primary_daily)
    fallback_limit = int(
        user.quota_fallback_daily or settings.default_quota_fallback_daily
    )
    primary_used = int(user.tokens_primary_today or 0)
    fallback_used = int(user.tokens_fallback_today or 0)
    unlimited = user_is_unlimited(user)

    if unlimited:
        tier_in_use = "unlimited"
    elif primary_used < primary_limit:
        tier_in_use = "primary"
    elif fallback_used < fallback_limit:
        tier_in_use = "fallback"
    else:
        tier_in_use = "blocked"

    return QuotaStatusOut(
        token_unlimited=unlimited,
        primary_limit=primary_limit,
        fallback_limit=fallback_limit,
        primary_used_today=primary_used,
        fallback_used_today=fallback_used,
        primary_remaining=None if unlimited else _remaining(primary_used, primary_limit),
        fallback_remaining=None
        if unlimited
        else _remaining(fallback_used, fallback_limit),
        primary_lifetime=int(user.tokens_primary_lifetime or 0),
        fallback_lifetime=int(user.tokens_fallback_lifetime or 0),
        tier_in_use=tier_in_use,
        primary_model=settings.deepseek_model_tier_primary,
        fallback_model=settings.deepseek_model_tier_fallback,
        resets_at=next_reset_at_iso(),
        usage_period_date=(
            user.usage_period_date.isoformat() if user.usage_period_date else None
        ),
    )


def record_chat_usage(
    db: Session,
    user_id: int,
    *,
    tier: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    session_id: str,
) -> None:
    """Record provider usage after a successful chat stream."""
    total = max(0, int(prompt_tokens)) + max(0, int(completion_tokens))
    if total <= 0:
        return

    if tier == "unlimited":
        return

    locked = db.execute(
        select(User).where(User.id == user_id).with_for_update()
    ).scalar_one()
    maybe_rollover_user(db, locked)

    if user_is_unlimited(locked):
        return

    effective_tier = tier_for_model(model) or tier
    if effective_tier not in ("primary", "fallback"):
        logger.warning(
            "Skipping quota record for unknown tier=%s model=%s user_id=%s",
            tier,
            model,
            user_id,
        )
        return

    resolution = resolve_chat_tier(locked)
    if resolution.tier == "blocked":
        logger.warning(
            "Recording %s tokens for user_id=%s after quota exhausted (model=%s)",
            total,
            user_id,
            model,
        )

    tier = effective_tier
    if tier == "primary":
        locked.tokens_primary_today = int(locked.tokens_primary_today or 0) + total
        locked.tokens_primary_lifetime = int(locked.tokens_primary_lifetime or 0) + total
    elif tier == "fallback":
        locked.tokens_fallback_today = int(locked.tokens_fallback_today or 0) + total
        locked.tokens_fallback_lifetime = (
            int(locked.tokens_fallback_lifetime or 0) + total
        )
    else:
        return

    db.add(
        TokenUsageEvent(
            user_id=user_id,
            tier=tier,
            model=model,
            prompt_tokens=max(0, int(prompt_tokens)),
            completion_tokens=max(0, int(completion_tokens)),
            total_tokens=total,
            session_id=session_id[:36] if session_id else None,
        )
    )
    db.flush()


def _user_to_admin_out(u: User) -> AdminUserOut:
    return AdminUserOut(
        id=u.id,
        email=u.email,
        role=u.role or "user",
        created_at=u.created_at.isoformat() if u.created_at else "",
        expert_preview_enabled=bool(u.expert_preview_enabled),
        token_unlimited=user_is_unlimited(u),
        quota_primary_daily=int(u.quota_primary_daily or 0),
        quota_fallback_daily=int(u.quota_fallback_daily or 0),
        tokens_primary_today=int(u.tokens_primary_today or 0),
        tokens_fallback_today=int(u.tokens_fallback_today or 0),
        tokens_primary_lifetime=int(u.tokens_primary_lifetime or 0),
        tokens_fallback_lifetime=int(u.tokens_fallback_lifetime or 0),
        usage_period_date=(
            u.usage_period_date.isoformat() if u.usage_period_date else None
        ),
    )


def list_users_for_admin(db: Session) -> list[AdminUserOut]:
    rows = db.scalars(select(User).order_by(User.id)).all()
    for u in rows:
        maybe_rollover_user(db, u)
    db.flush()
    return [_user_to_admin_out(u) for u in rows]


def get_platform_stats(db: Session) -> PlatformStatsOut:
    row = db.execute(
        select(
            func.count(User.id),
            func.coalesce(func.sum(User.tokens_primary_today), 0),
            func.coalesce(func.sum(User.tokens_fallback_today), 0),
            func.coalesce(func.sum(User.tokens_primary_lifetime), 0),
            func.coalesce(func.sum(User.tokens_fallback_lifetime), 0),
        )
    ).one()
    return PlatformStatsOut(
        total_users=int(row[0] or 0),
        tokens_primary_today=int(row[1] or 0),
        tokens_fallback_today=int(row[2] or 0),
        tokens_primary_lifetime=int(row[3] or 0),
        tokens_fallback_lifetime=int(row[4] or 0),
        tokens_total_lifetime=int(row[3] or 0) + int(row[4] or 0),
        tokens_total_today=int(row[1] or 0) + int(row[2] or 0),
    )


def update_user_quotas_admin(
    db: Session, target_user_id: int, patch: AdminUserUpdate
) -> AdminUserOut:
    user = db.get(User, target_user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    data = patch.model_dump(exclude_unset=True)
    if "quota_primary_daily" in data and data["quota_primary_daily"] is not None:
        if data["quota_primary_daily"] < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="quota_primary_daily must be non-negative",
            )
        user.quota_primary_daily = data["quota_primary_daily"]
    if "quota_fallback_daily" in data and data["quota_fallback_daily"] is not None:
        if data["quota_fallback_daily"] < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="quota_fallback_daily must be non-negative",
            )
        user.quota_fallback_daily = data["quota_fallback_daily"]
    if "token_unlimited" in data and data["token_unlimited"] is not None:
        user.token_unlimited = data["token_unlimited"]
    if "expert_preview_enabled" in data and data["expert_preview_enabled"] is not None:
        user.expert_preview_enabled = data["expert_preview_enabled"]

    db.flush()
    db.refresh(user)
    return _user_to_admin_out(user)
