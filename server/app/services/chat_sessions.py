"""Per-session counters and metadata."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.chat_session import ChatSession

DEFAULT_TITLE = "New conversation"
_PREVIEW_MAX = 120


def _preview_snippet(text: str) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= _PREVIEW_MAX:
        return cleaned
    return cleaned[: _PREVIEW_MAX - 1] + "…"


def create_chat_session(db: Session, user_id: int, session_id: str) -> ChatSession:
    now = datetime.now(timezone.utc)
    stmt = (
        insert(ChatSession)
        .values(
            user_id=user_id,
            session_id=session_id,
            user_message_count=0,
            title=DEFAULT_TITLE,
            last_activity_at=now,
        )
        .on_conflict_do_update(
            constraint="uq_chat_sessions_user_session",
            set_={"last_activity_at": now},
        )
        .returning(ChatSession.id)
    )
    row_id = db.execute(stmt).scalar_one()
    row = db.get(ChatSession, row_id)
    assert row is not None
    return row


def bump_session_message_count(
    db: Session,
    user_id: int,
    session_id: str,
    *,
    preview_from: str | None = None,
) -> int:
    now = datetime.now(timezone.utc)
    preview = _preview_snippet(preview_from) if preview_from else None
    stmt = insert(ChatSession).values(
        user_id=user_id,
        session_id=session_id,
        user_message_count=1,
        title=DEFAULT_TITLE,
        preview_text=preview,
        last_activity_at=now,
    )
    update_fields: dict = {
        "user_message_count": ChatSession.user_message_count + 1,
        "last_activity_at": now,
    }
    if preview is not None:
        update_fields["preview_text"] = preview
    stmt = stmt.on_conflict_do_update(
        constraint="uq_chat_sessions_user_session",
        set_=update_fields,
    )
    db.execute(stmt)
    db.flush()
    db.expire_all()
    row = db.scalar(
        select(ChatSession).where(
            ChatSession.user_id == user_id,
            ChatSession.session_id == session_id,
        )
    )
    return int(row.user_message_count) if row else 1


def get_session_message_count(db: Session, user_id: int, session_id: str) -> int:
    row = db.scalar(
        select(ChatSession).where(
            ChatSession.user_id == user_id,
            ChatSession.session_id == session_id,
        )
    )
    return int(row.user_message_count) if row else 0


def get_chat_session(
    db: Session, user_id: int, session_id: str
) -> ChatSession | None:
    return db.scalar(
        select(ChatSession).where(
            ChatSession.user_id == user_id,
            ChatSession.session_id == session_id,
        )
    )


def set_session_title(
    db: Session,
    user_id: int,
    session_id: str,
    title: str,
    *,
    generated: bool = False,
) -> None:
    row = get_chat_session(db, user_id, session_id)
    if row is None:
        create_chat_session(db, user_id, session_id)
        row = get_chat_session(db, user_id, session_id)
    assert row is not None
    row.title = title.strip()[:256] or DEFAULT_TITLE
    if generated:
        row.title_generated_at = datetime.now(timezone.utc)
    db.flush()


def delete_chat_session_row(db: Session, user_id: int, session_id: str) -> None:
    db.execute(
        delete(ChatSession).where(
            ChatSession.user_id == user_id,
            ChatSession.session_id == session_id,
        )
    )
