"""Cross-session semantic retrieval over memory episodes (Phase 3)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import asc, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.episode import Episode
from app.services.gemini_embeddings import embed_text


@dataclass
class ScoredMemory:
    episode_id: int
    session_id: str
    snippet: str
    distance: float
    scope: str = "cross_session"  # cross_session | in_session

    @property
    def similarity(self) -> float:
        return max(0.0, 1.0 - self.distance)


async def embed_query(text: str) -> list[float]:
    return await embed_text(text)


def search_memory_episodes(
    db: Session,
    user_id: int,
    query_vector: list[float],
    *,
    limit: int | None = None,
    exclude_session_id: str | None = None,
    session_id: str | None = None,
    exclude_episode_ids: set[int] | None = None,
    scope: str = "cross_session",
) -> list[ScoredMemory]:
    """Cosine-distance search over memory chunks with embeddings."""
    k = limit if limit is not None else settings.retrieval_top_k
    distance = Episode.embedding.cosine_distance(query_vector).label("distance")
    filters = [
        Episode.user_id == user_id,
        Episode.episode_kind == "memory",
        Episode.embedding.isnot(None),
    ]
    if session_id is not None:
        filters.append(Episode.session_id == session_id)
    elif exclude_session_id:
        filters.append(Episode.session_id != exclude_session_id)
    stmt = (
        select(
            Episode.id,
            Episode.session_id,
            Episode.content,
            Episode.summary,
            distance,
        )
        .where(*filters)
        .order_by(asc(distance))
        .limit(k * 3)
    )
    rows = db.execute(stmt).all()
    out: list[ScoredMemory] = []
    for row in rows:
        ep_id = row[0]
        if exclude_episode_ids and ep_id in exclude_episode_ids:
            continue
        snippet = (row[3] or row[2] or "").strip()
        if not snippet:
            continue
        distance = float(row[4])
        if distance > 1.0 - settings.retrieval_min_score:
            continue
        out.append(
            ScoredMemory(
                episode_id=ep_id,
                session_id=row[1],
                snippet=snippet[:2000],
                distance=distance,
                scope=scope,
            )
        )
        if len(out) >= k:
            break
    return out


def _filter_in_session_candidates(
    candidates: list[ScoredMemory],
    *,
    exclude_episode_ids: set[int] | None = None,
    latest_summary_content: str | None = None,
    limit: int | None = None,
) -> list[ScoredMemory]:
    """Drop latest-summary duplicates; cap to in-session final k."""
    k = limit if limit is not None else settings.in_session_memory_final_k
    if k < 1:
        return []
    out: list[ScoredMemory] = []
    summary_norm = (latest_summary_content or "").strip().lower()
    for m in candidates:
        if exclude_episode_ids and m.episode_id in exclude_episode_ids:
            continue
        snippet_norm = m.snippet.strip().lower()
        if summary_norm and snippet_norm and snippet_norm in summary_norm:
            continue
        if summary_norm and len(snippet_norm) > 40 and snippet_norm[:200] in summary_norm:
            continue
        out.append(m)
        if len(out) >= k:
            break
    return out


def search_in_session_memory_chunks(
    db: Session,
    user_id: int,
    session_id: str,
    query_vector: list[float],
    *,
    exclude_episode_ids: set[int] | None = None,
    latest_summary_content: str | None = None,
) -> list[ScoredMemory]:
    """Older same-session memory rows (excludes latest summary episode)."""
    k = settings.in_session_memory_final_k
    if k < 1:
        return []
    candidates = search_memory_episodes(
        db,
        user_id,
        query_vector,
        limit=k * 3,
        session_id=session_id,
        exclude_episode_ids=exclude_episode_ids,
        scope="in_session",
    )
    return _filter_in_session_candidates(
        candidates,
        exclude_episode_ids=exclude_episode_ids,
        latest_summary_content=latest_summary_content,
        limit=k,
    )


def search_in_session_memory_chunks_keyword(
    db: Session,
    user_id: int,
    session_id: str,
    query_text: str,
    *,
    exclude_episode_ids: set[int] | None = None,
    latest_summary_content: str | None = None,
) -> list[ScoredMemory]:
    """Keyword fallback for older same-session memory rows."""
    from app.services.memory_keyword_search import search_memory_episodes_keyword

    k = settings.in_session_memory_final_k
    if k < 1:
        return []
    q = query_text.strip()
    if not q:
        return []
    candidates = search_memory_episodes_keyword(
        db,
        user_id,
        q,
        limit=k * 3,
        only_session_id=session_id,
        exclude_episode_ids=exclude_episode_ids,
    )
    return _filter_in_session_candidates(
        candidates,
        exclude_episode_ids=exclude_episode_ids,
        latest_summary_content=latest_summary_content,
        limit=k,
    )
