"""Hybrid retrieval over active (non-offloaded) conversation turns (Phase 5)."""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass

from sqlalchemy import asc, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.episode import Episode
from app.services.memory_keyword_search import (
    ScoredActiveTurn,
    search_active_turns_keyword,
)
from app.services.memory_retrieval import embed_query
from app.services.token_counter import count_text_tokens

logger = logging.getLogger(__name__)

_ENTITY_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_]{2,}")


@dataclass
class RankedActiveTurn:
    episode_id: int
    parent_episode_id: int | None
    role: str
    content: str
    score: float
    reason: str
    chronological_index: int
    is_chunk: bool = False
    vector_score: float = 0.0
    bm25_score: float = 0.0

    @property
    def display_episode_id(self) -> int:
        return self.parent_episode_id or self.episode_id


@dataclass
class ActiveRetrievalResult:
    selected: list[RankedActiveTurn]
    floor_episode_ids: list[int]
    embed_available: bool
    degraded: bool


def _extract_entities(text: str) -> set[str]:
    return {m.group(0).lower() for m in _ENTITY_RE.finditer(text)}


def _entity_overlap(query: str, content: str) -> float:
    qe = _extract_entities(query)
    if not qe:
        return 0.0
    te = _extract_entities(content)
    if not te:
        return 0.0
    inter = len(qe & te)
    union = len(qe | te)
    return inter / union if union else 0.0


def _recency_score(age_turns: int) -> float:
    half = max(1, settings.recency_half_life_turns)
    return math.exp(-age_turns / half)


def load_active_timeline(
    db: Session,
    user_id: int,
    session_id: str,
) -> list[Episode]:
    """Active message episodes only (not chunks), chronological."""
    return list(
        db.scalars(
            select(Episode)
            .where(
                Episode.user_id == user_id,
                Episode.session_id == session_id,
                Episode.episode_kind == "message",
                Episode.is_offloaded.is_(False),
            )
            .order_by(Episode.created_at.asc(), Episode.id.asc())
        ).all()
    )


def _load_retrieval_candidates(
    db: Session,
    user_id: int,
    session_id: str,
    floor_ids: set[int],
) -> list[tuple[Episode, int]]:
    """Message rows + ready chunks with embeddings; index = position in timeline."""
    timeline = load_active_timeline(db, user_id, session_id)
    index_by_id = {ep.id: i for i, ep in enumerate(timeline)}
    out: list[tuple[Episode, int]] = []

    for ep in timeline:
        if ep.id in floor_ids:
            continue
        if ep.embed_status == "ready" and ep.embedding is not None:
            out.append((ep, index_by_id[ep.id]))
        elif ep.embed_status == "ready":
            chunks = list(
                db.scalars(
                    select(Episode)
                    .where(
                        Episode.parent_episode_id == ep.id,
                        Episode.episode_kind == "message_chunk",
                        Episode.is_offloaded.is_(False),
                        Episode.embed_status == "ready",
                        Episode.embedding.isnot(None),
                    )
                    .order_by(Episode.chunk_index.asc())
                ).all()
            )
            for ch in chunks:
                out.append((ch, index_by_id[ep.id]))

    return out


def _vector_search_candidates(
    db: Session,
    user_id: int,
    session_id: str,
    query_vector: list[float],
    *,
    exclude_ids: set[int],
    limit: int,
) -> dict[int, float]:
    """episode_id -> cosine similarity (1 - distance)."""
    distance = Episode.embedding.cosine_distance(query_vector).label("distance")
    filters = [
        Episode.user_id == user_id,
        Episode.session_id == session_id,
        Episode.is_offloaded.is_(False),
        Episode.embedding.isnot(None),
        Episode.embed_status == "ready",
        or_(
            Episode.episode_kind == "message",
            Episode.episode_kind == "message_chunk",
        ),
    ]
    stmt = (
        select(Episode.id, distance)
        .where(*filters)
        .order_by(asc(distance))
        .limit(limit * 4)
    )
    scores: dict[int, float] = {}
    for row in db.execute(stmt).all():
        ep_id = row[0]
        if ep_id in exclude_ids:
            continue
        dist = float(row[1])
        sim = max(0.0, 1.0 - dist)
        if ep_id not in scores or sim > scores[ep_id]:
            scores[ep_id] = sim
        if len(scores) >= limit * 2:
            break
    return scores


def _text_similarity(a: str, b: str) -> float:
    ta = set(_extract_entities(a))
    tb = set(_extract_entities(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _mmr_select(
    candidates: list[RankedActiveTurn],
    *,
    top_k: int,
) -> list[RankedActiveTurn]:
    if not candidates:
        return []
    lam = settings.mmr_lambda
    selected: list[RankedActiveTurn] = []
    remaining = sorted(candidates, key=lambda c: c.score, reverse=True)
    while remaining and len(selected) < top_k:
        if not selected:
            selected.append(remaining.pop(0))
            continue
        best_idx = 0
        best_mmr = -1.0
        for i, cand in enumerate(remaining):
            rel = cand.score
            redun = max(
                _text_similarity(cand.content, s.content) for s in selected
            )
            mmr = lam * rel - (1.0 - lam) * redun
            if mmr > best_mmr:
                best_mmr = mmr
                best_idx = i
        selected.append(remaining.pop(best_idx))
    return selected


def _expand_neighbours(
    ranked: list[RankedActiveTurn],
    timeline: list[Episode],
    *,
    floor_ids: set[int],
) -> list[RankedActiveTurn]:
    index_by_id = {ep.id: i for i, ep in enumerate(timeline)}
    by_id = {r.display_episode_id: r for r in ranked}
    out: list[RankedActiveTurn] = list(ranked)

    for r in list(ranked):
        parent_id = r.display_episode_id
        if parent_id not in index_by_id:
            continue
        idx = index_by_id[parent_id]
        for ni in (idx - 1, idx + 1):
            if ni < 0 or ni >= len(timeline):
                continue
            neighbour = timeline[ni]
            if neighbour.id in floor_ids or neighbour.id in by_id:
                continue
            nr = RankedActiveTurn(
                episode_id=neighbour.id,
                parent_episode_id=None,
                role=neighbour.role,
                content=neighbour.content,
                score=r.score * 0.85,
                reason="neighbour_expansion",
                chronological_index=ni,
            )
            by_id[neighbour.id] = nr
            out.append(nr)
    return out


async def retrieve_active_turns(
    db: Session,
    user_id: int,
    session_id: str,
    current_query: str,
) -> ActiveRetrievalResult:
    """Score and select relevant active turns excluding the always-floor."""
    timeline = load_active_timeline(db, user_id, session_id)
    floor_n = max(1, settings.active_retrieval_floor_turns)
    floor_eps = timeline[-floor_n:] if timeline else []
    floor_ids = {ep.id for ep in floor_eps}

    if not current_query.strip() or len(timeline) <= floor_n:
        return ActiveRetrievalResult(
            selected=[],
            floor_episode_ids=[ep.id for ep in floor_eps],
            embed_available=False,
            degraded=False,
        )

    exclude_for_search = set(floor_ids)
    candidates_raw = _load_retrieval_candidates(
        db, user_id, session_id, exclude_for_search
    )
    if not candidates_raw:
        return ActiveRetrievalResult(
            selected=[],
            floor_episode_ids=[ep.id for ep in floor_eps],
            embed_available=False,
            degraded=False,
        )

    bm25_map: dict[int, float] = {}
    for hit in search_active_turns_keyword(
        db,
        user_id,
        session_id,
        current_query,
        exclude_episode_ids=exclude_for_search,
    ):
        key = hit.display_episode_id
        bm25_map[key] = max(bm25_map.get(key, 0.0), hit.bm25_score)
        if hit.is_chunk and hit.parent_episode_id:
            bm25_map[hit.parent_episode_id] = max(
                bm25_map.get(hit.parent_episode_id, 0.0), hit.bm25_score
            )

    vector_scores: dict[int, float] = {}
    embed_available = False
    degraded = False
    if settings.gemini_api_key:
        try:
            query_vector = await embed_query(current_query)
            vector_scores = _vector_search_candidates(
                db,
                user_id,
                session_id,
                query_vector,
                exclude_ids=exclude_for_search,
                limit=settings.active_retrieval_top_k * 3,
            )
            embed_available = bool(vector_scores)
        except Exception:
            logger.warning("active turn query embed failed", exc_info=True)
            degraded = True

    if not embed_available and not bm25_map:
        return ActiveRetrievalResult(
            selected=[],
            floor_episode_ids=[ep.id for ep in floor_eps],
            embed_available=False,
            degraded=degraded,
        )

    max_bm25 = max(bm25_map.values()) if bm25_map else 1.0
    if max_bm25 <= 0:
        max_bm25 = 1.0

    scored: list[RankedActiveTurn] = []
    seen_parent: set[int] = set()
    for ep, chrono_idx in candidates_raw:
        parent_id = ep.parent_episode_id
        display_id = parent_id or ep.id
        if display_id in seen_parent and parent_id is None:
            continue
        vec = vector_scores.get(ep.id, vector_scores.get(display_id, 0.0))
        bm25 = bm25_map.get(display_id, bm25_map.get(ep.id, 0.0)) / max_bm25
        age = len(timeline) - 1 - chrono_idx
        rec = _recency_score(age)
        ent = _entity_overlap(current_query, ep.content or "")
        if not embed_available:
            vec = 0.0
        total = (
            settings.scoring_weight_vector * vec
            + settings.scoring_weight_bm25 * bm25
            + settings.scoring_weight_recency * rec
            + settings.scoring_weight_entity * ent
        )
        if total <= 0.01:
            continue
        reasons = []
        if vec > 0.1:
            reasons.append("vector")
        if bm25 > 0.1:
            reasons.append("bm25")
        if rec > 0.3:
            reasons.append("recency")
        if ent > 0.1:
            reasons.append("entity")
        scored.append(
            RankedActiveTurn(
                episode_id=ep.id,
                parent_episode_id=parent_id,
                role=ep.role if ep.role in ("user", "assistant", "system") else "user",
                content=ep.content,
                score=total,
                reason="+".join(reasons) if reasons else "hybrid",
                chronological_index=chrono_idx,
                is_chunk=ep.episode_kind == "message_chunk",
                vector_score=vec,
                bm25_score=bm25,
            )
        )
        seen_parent.add(display_id)

    # Deduplicate by parent message for MMR
    by_parent: dict[int, RankedActiveTurn] = {}
    for s in scored:
        pid = s.display_episode_id
        if pid not in by_parent or s.score > by_parent[pid].score:
            by_parent[pid] = s
    deduped = list(by_parent.values())

    mmr_selected = _mmr_select(
        deduped, top_k=settings.active_retrieval_top_k
    )
    expanded = _expand_neighbours(mmr_selected, timeline, floor_ids=floor_ids)

    final_map: dict[int, RankedActiveTurn] = {}
    for t in expanded:
        pid = t.display_episode_id
        if pid in floor_ids:
            continue
        if pid not in final_map or t.score > final_map[pid].score:
            final_map[pid] = t

    selected = sorted(
        final_map.values(),
        key=lambda x: (x.chronological_index, -x.score),
    )

    return ActiveRetrievalResult(
        selected=selected,
        floor_episode_ids=[ep.id for ep in floor_eps],
        embed_available=embed_available,
        degraded=degraded,
    )
