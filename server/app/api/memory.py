"""User facts CRUD (Phase 3)."""

import re
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_tenant_db
from app.models.user import User
from app.models.user_fact import UserFact
from app.schemas import (
    ClearMemoryRequest,
    ClearMemoryResponse,
    MemoryEpisodeListOut,
    MemoryEpisodeOut,
    UserFactCreate,
    UserFactOut,
    UserFactUpdate,
)
from app.services.cache_invalidation import invalidate_user_caches
from app.services.memory_admin import (
    CLEAR_MEMORY_CONFIRM,
    clear_episodic_memory,
    list_memory_episodes,
)

router = APIRouter()


def _normalize_key(raw: str) -> str:
    key = re.sub(r"[^a-z0-9_]+", "_", raw.lower().strip())
    key = re.sub(r"_+", "_", key).strip("_")
    return key[:128] if key else "unknown"


def _fact_out(f: UserFact) -> UserFactOut:
    return UserFactOut(
        id=f.id,
        fact_key=f.fact_key,
        fact_value=f.fact_value,
        confidence=f.confidence,
        pinned=bool(f.pinned),
        status=f.status or "active",
        source_session_id=f.source_session_id,
        created_at=f.created_at.isoformat(),
        updated_at=f.updated_at.isoformat(),
    )


@router.get("/facts", response_model=list[UserFactOut])
def list_facts(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_tenant_db)],
    include_deprecated: Annotated[bool, Query()] = False,
) -> list[UserFactOut]:
    q = select(UserFact).where(UserFact.user_id == current_user.id)
    if not include_deprecated:
        q = q.where(UserFact.status == "active")
    facts = db.scalars(q.order_by(UserFact.updated_at.desc())).all()
    return [_fact_out(f) for f in facts]


@router.post("/facts", response_model=UserFactOut, status_code=status.HTTP_201_CREATED)
def create_fact(
    body: UserFactCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_tenant_db)],
) -> UserFactOut:
    key = _normalize_key(body.fact_key)
    existing = db.scalar(
        select(UserFact).where(
            UserFact.user_id == current_user.id,
            UserFact.fact_key == key,
            UserFact.status == "active",
        )
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Fact with key '{key}' already exists; use PATCH to update.",
        )
    revived = db.scalar(
        select(UserFact).where(
            UserFact.user_id == current_user.id,
            UserFact.fact_key == key,
            UserFact.status.in_(("deprecated", "deleted")),
        )
    )
    if revived:
        now = datetime.now(timezone.utc)
        revived.status = "active"
        revived.deprecated_at = None
        revived.deleted_at = None
        revived.fact_value = body.fact_value.strip()
        revived.confidence = body.confidence
        revived.canonical_key = key
        revived.updated_at = now
        db.flush()
        invalidate_user_caches(db, current_user.id, revived.source_session_id)
        out = _fact_out(revived)
        db.commit()
        return out
    fact = UserFact(
        user_id=current_user.id,
        fact_key=key,
        canonical_key=key,
        fact_value=body.fact_value.strip(),
        confidence=body.confidence,
        status="active",
    )
    db.add(fact)
    db.flush()
    invalidate_user_caches(db, current_user.id, None)
    out = _fact_out(fact)
    db.commit()
    return out


@router.patch("/facts/{fact_id}", response_model=UserFactOut)
def update_fact(
    fact_id: int,
    body: UserFactUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_tenant_db)],
) -> UserFactOut:
    fact = db.scalar(
        select(UserFact).where(
            UserFact.id == fact_id,
            UserFact.user_id == current_user.id,
        )
    )
    if not fact:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fact not found")
    if body.fact_key is not None:
        new_key = _normalize_key(body.fact_key)
        conflict = db.scalar(
            select(UserFact).where(
                UserFact.user_id == current_user.id,
                UserFact.fact_key == new_key,
                UserFact.id != fact_id,
                UserFact.status == "active",
            )
        )
        if conflict:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Fact key '{new_key}' already in use",
            )
        fact.fact_key = new_key
    if body.fact_value is not None:
        fact.fact_value = body.fact_value.strip()
    if body.confidence is not None:
        fact.confidence = body.confidence
    if body.pinned is not None:
        fact.pinned = body.pinned
    fact.updated_at = datetime.now(timezone.utc)
    db.flush()
    invalidate_user_caches(db, current_user.id, fact.source_session_id)
    out = _fact_out(fact)
    db.commit()
    return out


@router.delete("/facts/{fact_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_fact(
    fact_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_tenant_db)],
) -> None:
    fact = db.scalar(
        select(UserFact).where(
            UserFact.id == fact_id,
            UserFact.user_id == current_user.id,
        )
    )
    if not fact:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fact not found")
    now = datetime.now(timezone.utc)
    fact.status = "deleted"
    fact.deleted_at = now
    fact.updated_at = now
    invalidate_user_caches(db, current_user.id, fact.source_session_id)
    db.commit()


def _episode_summary(ep) -> str:
    text = (ep.summary or ep.content or "").strip()
    if len(text) > 200:
        return text[:200] + "..."
    return text


def _episode_out(ep) -> MemoryEpisodeOut:
    return MemoryEpisodeOut(
        id=ep.id,
        session_id=ep.session_id,
        summary=_episode_summary(ep),
        created_at=ep.created_at.isoformat(),
    )


@router.get("/episodes", response_model=MemoryEpisodeListOut)
def list_episodes(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_tenant_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> MemoryEpisodeListOut:
    rows, total = list_memory_episodes(db, current_user.id, page=page, limit=limit)
    return MemoryEpisodeListOut(
        items=[_episode_out(ep) for ep in rows],
        total=total,
        page=page,
        limit=limit,
    )


@router.delete("/episodes/{episode_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_episode(
    episode_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_tenant_db)],
) -> None:
    from app.models.episode import Episode

    ep = db.scalar(
        select(Episode).where(
            Episode.id == episode_id,
            Episode.user_id == current_user.id,
        )
    )
    if not ep:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Episode not found",
        )
    if ep.episode_kind != "memory":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only episodic memory chunks can be deleted via this endpoint",
        )
    db.delete(ep)
    invalidate_user_caches(db, current_user.id, ep.session_id)
    db.commit()


@router.post("/clear", response_model=ClearMemoryResponse)
def clear_memory(
    body: ClearMemoryRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_tenant_db)],
) -> ClearMemoryResponse:
    if body.confirm != CLEAR_MEMORY_CONFIRM:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Confirmation must be exactly: {CLEAR_MEMORY_CONFIRM}",
        )
    facts_deleted, episodes_deleted = clear_episodic_memory(db, current_user.id)
    return ClearMemoryResponse(
        facts_deleted=facts_deleted,
        episodes_deleted=episodes_deleted,
    )
