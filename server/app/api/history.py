"""Chat history for sidebar, refresh, and context monitor (Phase 2+)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_tenant_db
from app.models.chat_session import ChatSession
from app.models.episode import Episode
from app.models.user import User
from app.schemas import (
    AttributionFactItem,
    AttributionMemoryItem,
    AttributionOut,
    AttributionRetrievalOut,
    ContextStatusOut,
    MessageOut,
    OffloadedMessageListOut,
    OffloadedMessageOut,
    SessionCompressionSummaryOut,
    SessionCreateOut,
    SessionDeleteOut,
    SessionSummary,
    SessionUpdate,
)
from app.services.chat_sessions import (
    create_chat_session,
    delete_chat_session_row,
    get_chat_session,
)
from app.services.context_manager import (
    context_status,
    list_offloaded_messages,
    session_compression_summary,
)

router = APIRouter()


def _safe_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@router.post("/sessions", response_model=SessionCreateOut, status_code=status.HTTP_201_CREATED)
def create_session(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_tenant_db)],
) -> SessionCreateOut:
    session_id = str(uuid.uuid4())
    row = create_chat_session(db, current_user.id, session_id)
    title = row.title
    last_at = row.last_activity_at.isoformat()
    db.commit()
    return SessionCreateOut(
        session_id=session_id,
        title=title,
        last_message_at=last_at,
    )


@router.get("/sessions", response_model=list[SessionSummary])
def list_sessions(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_tenant_db)],
) -> list[SessionSummary]:
    rows = db.scalars(
        select(ChatSession)
        .where(ChatSession.user_id == current_user.id)
        .order_by(ChatSession.last_activity_at.desc())
    ).all()
    result: list[SessionSummary] = []
    for row in rows:
        result.append(
            SessionSummary(
                session_id=row.session_id,
                last_message_at=row.last_activity_at.isoformat(),
                title=row.title or "New conversation",
                preview_text=row.preview_text,
            )
        )
    return result


@router.patch("/sessions/{session_id}", response_model=SessionSummary)
def update_session(
    session_id: str,
    body: SessionUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_tenant_db)],
) -> SessionSummary:
    sid = session_id.strip()
    row = get_chat_session(db, current_user.id, sid)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    row.title = body.title.strip()[:256]
    db.flush()
    summary = SessionSummary(
        session_id=sid,
        last_message_at=row.last_activity_at.isoformat(),
        title=row.title,
        preview_text=row.preview_text,
    )
    db.commit()
    return summary


@router.delete("/sessions/{session_id}", response_model=SessionDeleteOut)
def delete_session(
    session_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_tenant_db)],
) -> SessionDeleteOut:
    sid = session_id.strip()
    row = get_chat_session(db, current_user.id, sid)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    result = db.execute(
        delete(Episode).where(
            Episode.user_id == current_user.id,
            Episode.session_id == sid,
        )
    )
    episodes_deleted = int(result.rowcount or 0)
    delete_chat_session_row(db, current_user.id, sid)
    db.expire_all()
    db.commit()
    return SessionDeleteOut(session_id=sid, episodes_deleted=episodes_deleted)


@router.get("/messages", response_model=list[MessageOut])
def get_messages(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_tenant_db)],
    session_id: Annotated[str, Query(min_length=1, max_length=36)],
) -> list[MessageOut]:
    sid = session_id.strip()
    episodes = db.scalars(
        select(Episode)
        .where(
            Episode.user_id == current_user.id,
            Episode.session_id == sid,
            or_(Episode.episode_kind == "message", Episode.episode_kind.is_(None)),
            Episode.is_offloaded.is_(False),
        )
        .order_by(Episode.created_at.asc())
    ).all()
    out: list[MessageOut] = []
    for ep in episodes:
        out.append(
            MessageOut(
                id=ep.id,
                role=ep.role,
                content=ep.content,
                created_at=ep.created_at.isoformat(),
            )
        )
    return out


_OFFLOADED_SNIPPET_LEN = 500


@router.get("/offloaded", response_model=OffloadedMessageListOut)
def list_offloaded(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_tenant_db)],
    session_id: Annotated[str, Query(min_length=1, max_length=36)],
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> OffloadedMessageListOut:
    """Paginated archive of offloaded messages for this session (RLS: same user only)."""
    sid = session_id.strip()
    rows, total = list_offloaded_messages(
        db, current_user.id, sid, page=page, limit=limit
    )
    items = [
        OffloadedMessageOut(
            id=ep.id,
            role=ep.role,
            snippet=ep.content[:_OFFLOADED_SNIPPET_LEN],
            created_at=ep.created_at.isoformat(),
            offloaded_at=ep.offloaded_at.isoformat() if ep.offloaded_at else None,
        )
        for ep in rows
    ]
    return OffloadedMessageListOut(items=items, total=total, page=page, limit=limit)


@router.get("/compression-summary", response_model=SessionCompressionSummaryOut)
def get_compression_summary(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_tenant_db)],
    session_id: Annotated[str, Query(min_length=1, max_length=36)],
) -> SessionCompressionSummaryOut:
    """Latest in-session compressed summary (read-only, RLS-scoped to the current user)."""
    sid = session_id.strip()
    data = session_compression_summary(db, current_user.id, sid)
    if not data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No compression summary for this session",
        )
    return SessionCompressionSummaryOut(**data)


@router.get("/context", response_model=ContextStatusOut)
def get_context_status(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_tenant_db)],
    session_id: Annotated[str, Query(min_length=1, max_length=36)],
) -> ContextStatusOut:
    sid = session_id.strip()
    data = context_status(db, current_user.id, sid)
    return ContextStatusOut(**data)


@router.get("/attribution", response_model=AttributionOut)
def get_attribution(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_tenant_db)],
    session_id: Annotated[str, Query(min_length=1, max_length=36)],
    message_id: Annotated[int | None, Query()] = None,
) -> AttributionOut:
    sid = session_id.strip()
    if message_id is not None:
        ep = db.scalar(
            select(Episode).where(
                Episode.id == message_id,
                Episode.user_id == current_user.id,
                Episode.session_id == sid,
                Episode.role == "assistant",
            )
        )
    else:
        ep = db.scalar(
            select(Episode)
            .where(
                Episode.user_id == current_user.id,
                Episode.session_id == sid,
                Episode.role == "assistant",
                or_(Episode.episode_kind == "message", Episode.episode_kind.is_(None)),
            )
            .order_by(Episode.created_at.desc())
            .limit(1)
        )

    if not ep:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Assistant message not found",
        )

    meta = ep.metadata_json or {}
    attr = meta.get("attribution") if isinstance(meta, dict) else None
    if not attr:
        return AttributionOut(facts=[], memories=[])

    facts_raw = attr.get("facts", []) if isinstance(attr, dict) else []
    mem_raw = attr.get("memories", []) if isinstance(attr, dict) else []

    facts: list[AttributionFactItem] = []
    for item in facts_raw:
        if isinstance(item, dict) and "fact_key" in item and "fact_value" in item:
            facts.append(
                AttributionFactItem(
                    fact_key=str(item["fact_key"]),
                    fact_value=str(item["fact_value"]),
                    selection_reason=(
                        str(item["selection_reason"])
                        if item.get("selection_reason")
                        else None
                    ),
                    pinned=bool(item.get("pinned", False)),
                )
            )

    memories: list[AttributionMemoryItem] = []
    for item in mem_raw:
        if isinstance(item, dict) and "episode_id" in item:
            memories.append(
                AttributionMemoryItem(
                    episode_id=int(item["episode_id"]),
                    session_id=str(item.get("session_id", "")),
                    snippet=str(item.get("snippet", "")),
                    score=_safe_float(item.get("score")),
                    scope=str(item.get("scope", "cross_session")),
                )
            )

    retrieval_out: AttributionRetrievalOut | None = None
    ret_raw = attr.get("retrieval") if isinstance(attr, dict) else None
    if isinstance(ret_raw, dict):
        retrieval_out = AttributionRetrievalOut(
            mode=str(ret_raw.get("mode", "full")),
            cross_session_memory_available=bool(
                ret_raw.get("cross_session_memory_available", True)
            ),
            reranked=bool(ret_raw.get("reranked", False)),
            rerank_fallback=bool(ret_raw.get("rerank_fallback", False)),
            keyword_fallback_used=bool(ret_raw.get("keyword_fallback_used", False)),
            failure_reason=(
                str(ret_raw["failure_reason"])
                if ret_raw.get("failure_reason")
                else None
            ),
        )

    return AttributionOut(facts=facts, memories=memories, retrieval=retrieval_out)
