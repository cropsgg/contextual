"""Postgres-backed embedding cache with advisory-lock stampede protection."""

from __future__ import annotations

import hashlib
import logging
import unicodedata
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.embedding_cache import EmbeddingCache
from app.services.database import SessionLocal
from app.services.embedding_cache_metrics import record_embedding_cache
from app.services.rls import set_bypass_rls

logger = logging.getLogger(__name__)


def normalize_embed_text(text: str) -> str:
    collapsed = " ".join(unicodedata.normalize("NFC", text).split())
    return collapsed.strip()


def embedding_cache_key(text: str) -> str:
    normalized = normalize_embed_text(text)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"{digest}:{settings.gemini_embedding_model}:{settings.gemini_embedding_dimensions}"


def _open_cache_session() -> Session:
    return SessionLocal()


def _purge_expired(db: Session) -> None:
    now = datetime.now(timezone.utc)
    db.execute(delete(EmbeddingCache).where(EmbeddingCache.expires_at < now))


def lookup_cached_embedding(cache_key: str) -> list[float] | None:
    db = _open_cache_session()
    set_bypass_rls(db, enabled=True)
    try:
        _purge_expired(db)
        row = db.scalar(
            select(EmbeddingCache).where(
                EmbeddingCache.cache_key == cache_key,
                EmbeddingCache.expires_at > datetime.now(timezone.utc),
            )
        )
        if row is None:
            record_embedding_cache(False)
            return None
        vector = list(row.embedding)
        row.hit_count = int(row.hit_count or 0) + 1
        db.commit()
        record_embedding_cache(True)
        return vector
    except Exception:
        db.rollback()
        logger.debug("embedding cache lookup failed", exc_info=True)
        record_embedding_cache(False)
        return None
    finally:
        set_bypass_rls(db, enabled=False)
        db.close()


def store_cached_embedding(cache_key: str, vector: list[float]) -> None:
    db = _open_cache_session()
    set_bypass_rls(db, enabled=True)
    try:
        expires = datetime.now(timezone.utc) + timedelta(
            seconds=settings.embedding_cache_ttl_seconds
        )
        stmt = insert(EmbeddingCache).values(
            cache_key=cache_key,
            embedding=vector,
            expires_at=expires,
            hit_count=0,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["cache_key"],
            set_={
                "embedding": stmt.excluded.embedding,
                "expires_at": stmt.excluded.expires_at,
            },
        )
        db.execute(stmt)
        db.commit()
    except Exception:
        db.rollback()
        logger.debug("embedding cache store failed", exc_info=True)
    finally:
        set_bypass_rls(db, enabled=False)
        db.close()


def fetch_or_compute_embedding(
    cache_key: str,
    compute_fn,
) -> list[float]:
    """Sync path: lookup, else advisory lock + double-check + compute + store."""
    cached = lookup_cached_embedding(cache_key)
    if cached is not None:
        return cached

    db = _open_cache_session()
    set_bypass_rls(db, enabled=True)
    try:
        lock_id = int(hashlib.sha256(cache_key.encode()).hexdigest()[:15], 16) % (2**31)
        db.execute(text("SELECT pg_advisory_xact_lock(:lid)"), {"lid": lock_id})
        cached = db.scalar(
            select(EmbeddingCache).where(
                EmbeddingCache.cache_key == cache_key,
                EmbeddingCache.expires_at > datetime.now(timezone.utc),
            )
        )
        if cached is not None:
            record_embedding_cache(True)
            return list(cached.embedding)

        vector = compute_fn()
        expires = datetime.now(timezone.utc) + timedelta(
            seconds=settings.embedding_cache_ttl_seconds
        )
        stmt = insert(EmbeddingCache).values(
            cache_key=cache_key,
            embedding=vector,
            expires_at=expires,
            hit_count=0,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["cache_key"],
            set_={
                "embedding": stmt.excluded.embedding,
                "expires_at": stmt.excluded.expires_at,
            },
        )
        db.execute(stmt)
        db.commit()
        record_embedding_cache(False)
        return vector
    except Exception:
        db.rollback()
        raise
    finally:
        set_bypass_rls(db, enabled=False)
        db.close()
