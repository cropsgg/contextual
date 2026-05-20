"""Apply LLM fact change actions (upsert | delete | noop)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.user_fact import UserFact
from app.services.fact_extraction_sources import normalize_fact_key

logger = logging.getLogger(__name__)


@dataclass
class ApplyResult:
    upserted: int = 0
    deleted: int = 0
    noop: int = 0
    deprecated_by_cap: int = 0


def _resolve_canonical_key(change: dict[str, Any], normalized_key: str) -> str:
    raw = change.get("canonical_key") or change.get("key") or normalized_key
    return normalize_fact_key(str(raw))


def _find_fact_row(
    db: Session,
    user_id: int,
    key: str,
    canonical: str,
) -> UserFact | None:
    active = db.scalar(
        select(UserFact).where(
            UserFact.user_id == user_id,
            UserFact.status == "active",
            UserFact.fact_key.in_([key, canonical]),
        )
    )
    if active is not None:
        return active
    return db.scalar(
        select(UserFact)
        .where(
            UserFact.user_id == user_id,
            UserFact.status == "deprecated",
            UserFact.fact_key.in_([key, canonical]),
        )
        .order_by(UserFact.updated_at.desc())
        .limit(1)
    )


def enforce_max_facts(db: Session, user_id: int) -> int:
    """Deprecate lowest-confidence non-pinned facts over cap."""
    cap = settings.fact_max_per_user
    active = list(
        db.scalars(
            select(UserFact)
            .where(
                UserFact.user_id == user_id,
                UserFact.status == "active",
            )
            .order_by(UserFact.confidence.asc(), UserFact.updated_at.asc())
        ).all()
    )
    deprecated = 0
    now = datetime.now(timezone.utc)
    while len(active) > cap:
        victim = next((f for f in active if not f.pinned), None)
        if victim is None:
            break
        victim.status = "deprecated"
        victim.deprecated_at = now
        active.remove(victim)
        deprecated += 1
    return deprecated


def apply_fact_changes(
    db: Session,
    user_id: int,
    changes: list[dict[str, Any]],
    *,
    session_id: str | None,
    run_id: int | None = None,
) -> ApplyResult:
    result = ApplyResult()
    now = datetime.now(timezone.utc)

    for change in changes:
        action = str(change.get("action", "upsert")).lower()
        key_raw = change.get("key") or change.get("fact_key") or ""
        if not key_raw and action != "noop":
            continue
        key = normalize_fact_key(str(key_raw)) if key_raw else ""
        canonical = _resolve_canonical_key(change, key) if key else ""

        if action == "noop":
            result.noop += 1
            continue

        existing = _find_fact_row(db, user_id, key, canonical) if key else None

        if action == "delete":
            if existing and existing.pinned:
                result.noop += 1
                continue
            try:
                conf = float(change.get("confidence", 0.85))
            except (TypeError, ValueError):
                conf = 0.85
            if conf < settings.fact_delete_min_confidence:
                result.noop += 1
                continue
            if existing:
                existing.status = "deprecated"
                existing.deprecated_at = now
                existing.last_extraction_run_id = run_id
                result.deleted += 1
            else:
                result.noop += 1
            continue

        if action != "upsert":
            continue

        value_raw = change.get("value") or change.get("fact_value") or ""
        value = str(value_raw).strip()[:4000]
        if not value:
            continue
        try:
            conf = float(change.get("confidence", 1.0))
        except (TypeError, ValueError):
            conf = 1.0
        conf = max(0.0, min(1.0, conf))
        if conf < 0.5:
            continue

        store_key = canonical or key
        if existing and existing.pinned:
            if existing.fact_value == value:
                result.noop += 1
            else:
                result.noop += 1
            continue

        if existing:
            if existing.fact_value == value and existing.confidence == conf:
                if existing.status == "active":
                    result.noop += 1
                    continue
            existing.status = "active"
            existing.deprecated_at = None
            existing.deleted_at = None
            existing.fact_key = store_key
            existing.canonical_key = canonical or store_key
            existing.fact_value = value
            existing.confidence = conf
            existing.source_session_id = session_id
            existing.updated_at = now
            existing.last_extraction_run_id = run_id
            result.upserted += 1
        else:
            db.add(
                UserFact(
                    user_id=user_id,
                    fact_key=store_key,
                    canonical_key=canonical or store_key,
                    fact_value=value,
                    confidence=conf,
                    source_session_id=session_id,
                    status="active",
                    last_extraction_run_id=run_id,
                )
            )
            result.upserted += 1

        aliases = change.get("aliases") or []
        if isinstance(aliases, list):
            for alias in aliases:
                alias_key = normalize_fact_key(str(alias))
                if not alias_key or alias_key == store_key:
                    continue
                alias_row = db.scalar(
                    select(UserFact).where(
                        UserFact.user_id == user_id,
                        UserFact.fact_key == alias_key,
                        UserFact.status == "active",
                    )
                )
                if alias_row and alias_row.pinned:
                    continue
                if alias_row and alias_row.id != (existing.id if existing else -1):
                    alias_row.status = "deprecated"
                    alias_row.deprecated_at = now
                    alias_row.canonical_key = store_key

    result.deprecated_by_cap = enforce_max_facts(db, user_id)
    return result
