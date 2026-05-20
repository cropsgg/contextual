"""Reusable FastAPI dependencies."""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import get_token_payload
from app.models.user import User
from app.services.database import SessionLocal, get_db
from app.services.rls import set_tenant_context


async def get_current_user_id(
    payload: Annotated[dict, Depends(get_token_payload)],
) -> int:
    sub = payload.get("sub")
    if sub is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )
    try:
        return int(sub)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token subject",
        )


def get_tenant_db(
    user_id: Annotated[int, Depends(get_current_user_id)],
) -> Session:
    db = SessionLocal()
    set_tenant_context(db, user_id)
    try:
        yield db
    finally:
        db.close()


async def get_current_user(
    user_id: Annotated[int, Depends(get_current_user_id)],
    db: Annotated[Session, Depends(get_tenant_db)],
) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    return user


async def require_admin(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    if (current_user.role or "").lower() != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user
