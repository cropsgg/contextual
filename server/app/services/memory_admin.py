"""Episodic memory administration (Phase 4)."""

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.episode import Episode
from app.models.user_fact import UserFact

CLEAR_MEMORY_CONFIRM = "DELETE_ALL_MEMORY"


def list_memory_episodes(
    db: Session,
    user_id: int,
    *,
    page: int = 1,
    limit: int = 20,
) -> tuple[list[Episode], int]:
    page = max(1, page)
    limit = min(max(1, limit), 100)
    offset = (page - 1) * limit

    filters = (
        Episode.user_id == user_id,
        Episode.episode_kind == "memory",
    )
    total = (
        db.scalar(select(func.count(Episode.id)).where(*filters)) or 0
    )
    rows = db.scalars(
        select(Episode)
        .where(*filters)
        .order_by(Episode.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return list(rows), int(total)


def delete_memory_episode(db: Session, user_id: int, episode_id: int) -> bool:
    ep = db.scalar(
        select(Episode).where(
            Episode.id == episode_id,
            Episode.user_id == user_id,
        )
    )
    if not ep:
        return False
    if ep.episode_kind != "memory":
        return False
    db.delete(ep)
    db.commit()
    return True


def clear_episodic_memory(db: Session, user_id: int) -> tuple[int, int]:
    """Delete all user facts and memory episodes; returns (facts_deleted, episodes_deleted)."""
    facts_deleted = db.execute(
        delete(UserFact).where(UserFact.user_id == user_id)
    ).rowcount or 0
    episodes_deleted = db.execute(
        delete(Episode).where(
            Episode.user_id == user_id,
            Episode.episode_kind == "memory",
        )
    ).rowcount or 0
    from app.services.cache_invalidation import invalidate_user_caches

    invalidate_user_caches(db, user_id, None)
    db.commit()
    return int(facts_deleted), int(episodes_deleted)
