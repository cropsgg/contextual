"""Selective user-fact injection for prompt assembly (Phase 3+)."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.user_fact import UserFact
from app.services.gemini_embeddings import embed_text

logger = logging.getLogger(__name__)

SelectionReason = Literal["pinned", "similarity", "recency", "confidence"]


@dataclass
class InjectedFact:
    id: int
    fact_key: str
    fact_value: str
    confidence: float
    pinned: bool
    score: float
    selection_reason: SelectionReason


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return max(0.0, min(1.0, dot / (na * nb)))


def _recency_score(updated_at: datetime, *, half_life_days: float = 30.0) -> float:
    now = datetime.now(timezone.utc)
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (now - updated_at).total_seconds() / 86400.0)
    return math.exp(-age_days / half_life_days)


async def select_facts_for_injection(
    db: Session,
    user_id: int,
    current_query: str,
    *,
    query_vector: list[float] | None = None,
) -> tuple[list[InjectedFact], int]:
    """Return facts to inject and count of stored facts excluded."""
    all_facts = list(
        db.scalars(
            select(UserFact)
            .where(
                UserFact.user_id == user_id,
                UserFact.status == "active",
            )
            .order_by(UserFact.updated_at.desc())
        ).all()
    )
    # One row per canonical_key (prefer highest confidence / pinned)
    by_canonical: dict[str, UserFact] = {}
    for f in all_facts:
        canon = f.canonical_key or f.fact_key
        prev = by_canonical.get(canon)
        if prev is None or (f.pinned and not prev.pinned) or (
            not prev.pinned and f.confidence > prev.confidence
        ):
            by_canonical[canon] = f
    all_facts = list(by_canonical.values())
    if not all_facts:
        return [], 0

    settings = get_settings()
    max_n = settings.fact_injection_max
    min_sim = settings.fact_injection_min_similarity
    embed_cap = settings.fact_injection_embed_cap

    fact_vectors: dict[int, list[float]] = {}
    if query_vector is not None:
        to_embed = [f for f in all_facts if not f.pinned][:embed_cap]
        for fact in to_embed:
            try:
                text = f"{fact.fact_key}: {fact.fact_value}"
                fact_vectors[fact.id] = await embed_text(text)
            except Exception:
                logger.debug("fact embed failed for id=%s", fact.id, exc_info=True)

    scored: list[tuple[UserFact, float, SelectionReason]] = []
    for fact in all_facts:
        if fact.pinned:
            scored.append((fact, 1.0, "pinned"))
            continue

        sim = 0.0
        if query_vector is not None and fact.id in fact_vectors:
            sim = _cosine_similarity(query_vector, fact_vectors[fact.id])
            if sim < min_sim:
                continue
            reason: SelectionReason = "similarity"
        else:
            reason = "recency"

        rec = _recency_score(fact.updated_at)
        conf = max(0.0, min(1.0, float(fact.confidence)))
        composite = 0.5 * sim + 0.3 * rec + 0.2 * conf
        if reason == "recency" and query_vector is None:
            composite = 0.6 * rec + 0.4 * conf
        scored.append((fact, composite, reason))

    scored.sort(key=lambda x: x[1], reverse=True)

    pinned = [(f, s, r) for f, s, r in scored if f.pinned]
    rest = [(f, s, r) for f, s, r in scored if not f.pinned]
    ordered = pinned + rest

    selected: list[InjectedFact] = []
    for fact, score, reason in ordered[:max_n]:
        selected.append(
            InjectedFact(
                id=fact.id,
                fact_key=fact.fact_key,
                fact_value=fact.fact_value,
                confidence=float(fact.confidence),
                pinned=bool(fact.pinned),
                score=round(score, 4),
                selection_reason=reason,
            )
        )

    suppressed = len(all_facts) - len(selected)
    return selected, suppressed
