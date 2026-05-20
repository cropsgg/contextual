"""Admin routes: user list, platform stats, quota overrides."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import require_admin
from app.models.user import User
from app.schemas import AdminUserOut, AdminUserUpdate, PlatformStatsOut
from app.services.database import get_db
from app.services.rls import set_bypass_rls
from app.services.token_quota import (
    get_platform_stats,
    list_users_for_admin,
    update_user_quotas_admin,
)

router = APIRouter()


@router.get("/users", response_model=list[AdminUserOut])
def admin_list_users(
    _admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> list[AdminUserOut]:
    set_bypass_rls(db)
    return list_users_for_admin(db)


@router.get("/stats", response_model=PlatformStatsOut)
def admin_platform_stats(
    _admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> PlatformStatsOut:
    set_bypass_rls(db)
    return get_platform_stats(db)


@router.patch("/users/{user_id}", response_model=AdminUserOut)
def admin_update_user(
    user_id: int,
    body: AdminUserUpdate,
    _admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> AdminUserOut:
    set_bypass_rls(db)
    out = update_user_quotas_admin(db, user_id, body)
    db.commit()
    return out
