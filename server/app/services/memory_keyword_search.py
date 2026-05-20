"""Keyword fallback search over memory episodes when embedding is unavailable."""

from __future__ import annotations

from sqlalchemy import desc, func, select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.episode import Episode
from app.services.memory_retrieval import ScoredMemory


def search_memory_episodes_keyword(
    db: Session,
    user_id: int,
    query_text: str,
    *,
    limit: int | None = None,
    exclude_session_id: str | None = None,
    only_session_id: str | None = None,
    exclude_episode_ids: set[int] | None = None,
) -> list[ScoredMemory]:
    """Full-text search on memory episode content/summary (degraded retrieval)."""
    k = limit if limit is not None else settings.retrieval_keyword_top_k
    q = query_text.strip()
    if not q:
        return []

    ts_query = func.websearch_to_tsquery("english", q)
    doc = func.to_tsvector(
        "english",
        func.coalesce(Episode.summary, Episode.content, ""),
    )
    rank = func.ts_rank(doc, ts_query).label("rank")

    filters = [
        Episode.user_id == user_id,
        Episode.episode_kind == "memory",
        doc.op("@@")(ts_query),
    ]
    if exclude_session_id:
        filters.append(Episode.session_id != exclude_session_id)
    if only_session_id is not None:
        filters.append(Episode.session_id == only_session_id)

    stmt = (
        select(
            Episode.id,
            Episode.session_id,
            Episode.content,
            Episode.summary,
            rank,
        )
        .where(*filters)
        .order_by(desc(rank), desc(Episode.created_at))
        .limit(k)
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
        rank_val = float(row[4] or 0.0)
        similarity = min(1.0, max(0.0, rank_val))
        out.append(
            ScoredMemory(
                episode_id=row[0],
                session_id=row[1],
                snippet=snippet[:2000],
                distance=1.0 - similarity,
                scope="cross_session" if only_session_id is None else "in_session",
            )
        )
    return out


def ensure_memory_fts_index(conn) -> None:
    """GIN index for keyword fallback (idempotent)."""
    conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_episodes_memory_fts
            ON episodes USING gin (
                to_tsvector('english', coalesce(summary, content, ''))
            )
            WHERE episode_kind = 'memory'
            """
        )
    )
