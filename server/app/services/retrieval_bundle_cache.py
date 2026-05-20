"""Serialize/deserialize AssembledContext for Postgres retrieval_bundle_cache."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.episode import Episode
from app.models.retrieval_bundle_cache import RetrievalBundleCache
from app.models.user_fact import UserFact
from app.services.embedding_cache_store import normalize_embed_text
from app.services.fact_injection import InjectedFact
from app.services.memory_retrieval import ScoredMemory
from app.services.retrieval_status import AssembledContext, RetrievalMode, RetrievalOutcome
from app.services.rls import set_bypass_rls

logger = logging.getLogger(__name__)

_last_bundle_hit: bool | None = None


def get_last_retrieval_bundle_cache_hit() -> bool | None:
    return _last_bundle_hit


def record_retrieval_bundle_cache_hit(hit: bool) -> None:
    global _last_bundle_hit
    _last_bundle_hit = hit


def compute_version_token(
    db: Session,
    user_id: int,
    session_id: str,
) -> str:
    latest_mem = db.scalar(
        select(Episode.id)
        .where(
            Episode.user_id == user_id,
            Episode.session_id == session_id,
            Episode.episode_kind == "memory",
        )
        .order_by(Episode.created_at.desc(), Episode.id.desc())
        .limit(1)
    )
    active_count = db.scalar(
        select(func.count())
        .select_from(Episode)
        .where(
            Episode.user_id == user_id,
            Episode.session_id == session_id,
            or_(Episode.episode_kind == "message", Episode.episode_kind.is_(None)),
            Episode.is_offloaded.is_(False),
        )
    )
    max_fact_updated = db.scalar(
        select(func.max(UserFact.updated_at)).where(
            UserFact.user_id == user_id,
            UserFact.status == "active",
        )
    )
    offloaded_count = db.scalar(
        select(func.count())
        .select_from(Episode)
        .where(
            Episode.user_id == user_id,
            Episode.session_id == session_id,
            Episode.episode_kind == "message",
            Episode.is_offloaded.is_(True),
        )
    )
    mem_part = str(latest_mem or 0)
    fact_part = max_fact_updated.isoformat() if max_fact_updated else "none"
    return f"{mem_part}:{int(active_count or 0)}:{fact_part}:{int(offloaded_count or 0)}"


def build_retrieval_cache_key(
    user_id: int,
    session_id: str,
    query: str,
    version_token: str,
) -> str:
    qhash = hashlib.sha256(normalize_embed_text(query).encode()).hexdigest()[:32]
    return f"retrieval:{user_id}:{session_id}:{qhash}:{version_token}"


def _memory_to_dict(m: ScoredMemory) -> dict[str, Any]:
    return {
        "episode_id": m.episode_id,
        "session_id": m.session_id,
        "snippet": m.snippet,
        "distance": m.distance,
        "scope": m.scope,
    }


def _memory_from_dict(d: dict[str, Any]) -> ScoredMemory:
    return ScoredMemory(
        episode_id=int(d["episode_id"]),
        session_id=str(d["session_id"]),
        snippet=str(d["snippet"]),
        distance=float(d.get("distance", 1.0 - float(d.get("similarity", 0.0)))),
        scope=str(d.get("scope", "cross_session")),
    )


def _fact_to_dict(f: InjectedFact) -> dict[str, Any]:
    return {
        "id": f.id,
        "fact_key": f.fact_key,
        "fact_value": f.fact_value,
        "confidence": f.confidence,
        "pinned": f.pinned,
        "score": f.score,
        "selection_reason": f.selection_reason,
    }


def _fact_from_dict(d: dict[str, Any]) -> InjectedFact:
    return InjectedFact(
        id=int(d["id"]),
        fact_key=str(d["fact_key"]),
        fact_value=str(d["fact_value"]),
        confidence=float(d["confidence"]),
        pinned=bool(d["pinned"]),
        score=float(d["score"]),
        selection_reason=d["selection_reason"],
    )


def serialize_assembled_context(ctx: AssembledContext) -> dict[str, Any]:
    r = ctx.retrieval
    return {
        "injected_facts": [_fact_to_dict(f) for f in ctx.injected_facts],
        "cross_session_memories": [_memory_to_dict(m) for m in ctx.cross_session_memories],
        "in_session_memories": [_memory_to_dict(m) for m in ctx.in_session_memories],
        "retrieval": {
            "mode": r.mode.value,
            "cross_session_memory_available": r.cross_session_memory_available,
            "embed_succeeded": r.embed_succeeded,
            "keyword_fallback_used": r.keyword_fallback_used,
            "reranked": r.reranked,
            "rerank_fallback": r.rerank_fallback,
            "embed_latency_ms": r.embed_latency_ms,
            "search_latency_ms": r.search_latency_ms,
            "rerank_latency_ms": r.rerank_latency_ms,
            "failure_reason": r.failure_reason,
            "suppressed_fact_count": r.suppressed_fact_count,
        },
    }


def deserialize_assembled_context(data: dict[str, Any]) -> AssembledContext:
    rd = data["retrieval"]
    outcome = RetrievalOutcome(
        mode=RetrievalMode(rd["mode"]),
        cross_session_memory_available=bool(rd["cross_session_memory_available"]),
        embed_succeeded=bool(rd["embed_succeeded"]),
        keyword_fallback_used=bool(rd["keyword_fallback_used"]),
        reranked=bool(rd["reranked"]),
        rerank_fallback=bool(rd["rerank_fallback"]),
        embed_latency_ms=rd.get("embed_latency_ms"),
        search_latency_ms=rd.get("search_latency_ms"),
        rerank_latency_ms=rd.get("rerank_latency_ms"),
        failure_reason=rd.get("failure_reason"),
        suppressed_fact_count=int(rd.get("suppressed_fact_count", 0)),
    )
    return AssembledContext(
        injected_facts=[_fact_from_dict(f) for f in data.get("injected_facts", [])],
        cross_session_memories=[
            _memory_from_dict(m) for m in data.get("cross_session_memories", [])
        ],
        in_session_memories=[
            _memory_from_dict(m) for m in data.get("in_session_memories", [])
        ],
        retrieval=outcome,
    )


def get_cached_assembled_context(db: Session, cache_key: str) -> AssembledContext | None:
    set_bypass_rls(db, enabled=True)
    try:
        row = db.scalar(
            select(RetrievalBundleCache).where(
                RetrievalBundleCache.cache_key == cache_key,
                RetrievalBundleCache.expires_at > datetime.now(timezone.utc),
            )
        )
        if row is None:
            record_retrieval_bundle_cache_hit(False)
            return None
        try:
            payload = row.payload_json
            if isinstance(payload, str):
                payload = json.loads(payload)
            record_retrieval_bundle_cache_hit(True)
            return deserialize_assembled_context(payload)
        except Exception:
            logger.debug("retrieval bundle cache deserialize failed", exc_info=True)
            record_retrieval_bundle_cache_hit(False)
            return None
    finally:
        set_bypass_rls(db, enabled=False)


def store_assembled_context_cache(
    db: Session,
    cache_key: str,
    ctx: AssembledContext,
) -> None:
    set_bypass_rls(db, enabled=True)
    try:
        expires = datetime.now(timezone.utc) + timedelta(
            seconds=settings.retrieval_bundle_cache_ttl_seconds
        )
        payload = serialize_assembled_context(ctx)
        stmt = insert(RetrievalBundleCache).values(
            cache_key=cache_key,
            payload_json=payload,
            expires_at=expires,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["cache_key"],
            set_={
                "payload_json": stmt.excluded.payload_json,
                "expires_at": stmt.excluded.expires_at,
            },
        )
        db.execute(stmt)
        db.flush()
    finally:
        set_bypass_rls(db, enabled=False)
