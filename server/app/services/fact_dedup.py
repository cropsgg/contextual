"""Post-extraction dedup: canonical_key aliases + embedding similarity merge."""

from __future__ import annotations

import logging
import math

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.user_fact import UserFact
from app.services.gemini_embeddings import embed_text

logger = logging.getLogger(__name__)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


async def merge_alias_keys(db: Session, user_id: int) -> int:
    """Deprecate active rows whose fact_key differs from canonical_key."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    merged = 0
    facts = list(
        db.scalars(
            select(UserFact).where(
                UserFact.user_id == user_id,
                UserFact.status == "active",
            )
        ).all()
    )
    by_canonical: dict[str, UserFact] = {}
    for f in facts:
        canon = f.canonical_key or f.fact_key
        if f.fact_key == canon:
            by_canonical.setdefault(canon, f)
            continue
        if f.pinned:
            continue
        keeper = by_canonical.get(canon)
        if keeper is None:
            f.fact_key = canon
            f.canonical_key = canon
            by_canonical[canon] = f
            continue
        f.status = "deprecated"
        f.deprecated_at = now
        merged += 1
    return merged


async def merge_embedding_duplicates(db: Session, user_id: int) -> int:
    """Merge highly similar active facts (never merge pinned into another)."""
    from datetime import datetime, timezone

    threshold = settings.fact_dedup_similarity_threshold
    now = datetime.now(timezone.utc)
    facts = list(
        db.scalars(
            select(UserFact).where(
                UserFact.user_id == user_id,
                UserFact.status == "active",
            )
            .order_by(UserFact.confidence.desc())
        ).all()
    )
    vectors: dict[int, list[float]] = {}
    for f in facts:
        try:
            vectors[f.id] = await embed_text(f"{f.fact_key}: {f.fact_value}")
        except Exception:
            logger.debug("dedup embed failed id=%s", f.id, exc_info=True)

    merged = 0
    seen: set[int] = set()
    for i, fa in enumerate(facts):
        if fa.id in seen:
            continue
        va = vectors.get(fa.id)
        if va is None:
            continue
        for fb in facts[i + 1 :]:
            if fb.id in seen or fb.pinned:
                continue
            vb = vectors.get(fb.id)
            if vb is None:
                continue
            if fa.fact_key == fb.fact_key:
                continue
            if _cosine_similarity(va, vb) < threshold:
                continue
            if fa.pinned and fb.pinned:
                continue
            loser = fb if fa.confidence >= fb.confidence else fa
            if loser.pinned:
                continue
            winner = fa if loser is fb else fb
            loser.status = "deprecated"
            loser.deprecated_at = now
            loser.canonical_key = winner.canonical_key or winner.fact_key
            seen.add(loser.id)
            merged += 1
    return merged


async def run_fact_dedup(db: Session, user_id: int) -> int:
    alias_merged = await merge_alias_keys(db, user_id)
    embed_merged = await merge_embedding_duplicates(db, user_id)
    return alias_merged + embed_merged
