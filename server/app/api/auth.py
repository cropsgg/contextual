"""Authentication routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import get_current_user
from app.core.limiter import limiter
from app.core.rate_limit import auth_rate_limit
from app.core.security import create_access_token, hash_password, verify_password
from app.models.user import User
from app.schemas import TokenResponse, UserLogin, UserOut, UserRegister
from app.services.database import get_db
from app.services.rls import set_bypass_rls
from app.services.token_quota import get_user_quota_status, maybe_rollover_user

router = APIRouter()


@router.post("/register", response_model=UserOut)
@limiter.limit(auth_rate_limit())
def register(
    request: Request,
    body: UserRegister,
    db: Annotated[Session, Depends(get_db)],
) -> UserOut:
    set_bypass_rls(db)
    email_norm = body.email.lower().strip()
    existing = db.scalar(select(User).where(User.email == email_norm))
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )
    user = User(
        email=email_norm,
        hashed_password=hash_password(body.password),
        quota_primary_daily=settings.default_quota_primary_daily,
        quota_fallback_daily=settings.default_quota_fallback_daily,
    )
    db.add(user)
    db.flush()
    maybe_rollover_user(db, user)
    out = UserOut.model_validate(user)
    out.quota = get_user_quota_status(user)
    db.commit()
    return out


@router.post("/login", response_model=TokenResponse)
@limiter.limit(auth_rate_limit())
def login(
    request: Request,
    body: UserLogin,
    db: Annotated[Session, Depends(get_db)],
) -> TokenResponse:
    set_bypass_rls(db)
    user = db.scalar(select(User).where(User.email == body.email.lower().strip()))
    if user is None or not verify_password(body.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )
    token = create_access_token(sub=str(user.id))
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserOut)
def read_me(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> UserOut:
    set_bypass_rls(db)
    user = db.get(User, current_user.id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    maybe_rollover_user(db, user)
    out = UserOut.model_validate(user)
    out.quota = get_user_quota_status(user)
    db.commit()
    return out
