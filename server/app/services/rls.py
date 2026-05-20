"""PostgreSQL row-level security session variables (Phase 4)."""

from sqlalchemy import text
from sqlalchemy.orm import Session


def set_tenant_context(db: Session, user_id: int) -> None:
    """Scope subsequent queries on this connection to the given user."""
    db.execute(
        text("SELECT set_config('app.current_user_id', :uid, true)"),
        {"uid": str(int(user_id))},
    )


def set_bypass_rls(db: Session, *, enabled: bool = True) -> None:
    """Allow auth/bootstrap operations to bypass tenant policies."""
    db.execute(
        text("SELECT set_config('app.bypass_rls', :val, true)"),
        {"val": "on" if enabled else "off"},
    )


def clear_tenant_context(db: Session) -> None:
    db.execute(text("SELECT set_config('app.current_user_id', '', true)"))
    set_bypass_rls(db, enabled=False)
