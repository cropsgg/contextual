"""Expert preview access control (per-user flag + admin role)."""

from fastapi import HTTPException, status

from app.models.user import User


def user_has_expert_preview(user: User) -> bool:
    role = getattr(user, "role", "user") or "user"
    if role == "admin":
        return True
    return bool(getattr(user, "expert_preview_enabled", False))


def require_expert_preview(user: User) -> None:
    if not user_has_expert_preview(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Expert preview is not enabled for this account",
        )
