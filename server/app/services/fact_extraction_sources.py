"""Build payloads for fact extraction v2 (transcript + memory + existing facts)."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.episode import Episode
from app.models.user_fact import UserFact


def normalize_fact_key(raw: str) -> str:
    import re

    key = re.sub(r"[^a-z0-9_]+", "_", raw.lower().strip())
    key = re.sub(r"_+", "_", key).strip("_")
    return key[:128] if key else "unknown"


def load_active_transcript(
    db: Session,
    user_id: int,
    *,
    session_id: str | None,
    limit: int | None = None,
) -> str:
    lim = limit if limit is not None else settings.fact_extraction_lookback_messages
    q = (
        select(Episode)
        .where(
            Episode.user_id == user_id,
            or_(Episode.episode_kind == "message", Episode.episode_kind.is_(None)),
            Episode.is_offloaded.is_(False),
        )
        .order_by(Episode.created_at.desc())
        .limit(lim)
    )
    if session_id is not None:
        q = q.where(Episode.session_id == session_id)
    episodes = list(db.scalars(q).all())
    episodes.reverse()
    lines: list[str] = []
    for ep in episodes:
        role = ep.role if ep.role in ("user", "assistant") else "user"
        lines.append(f"{role}: {ep.content}")
    return "\n".join(lines)


def load_latest_memory_summaries(
    db: Session,
    user_id: int,
    *,
    max_sessions: int | None = None,
) -> list[dict[str, str]]:
    cap = max_sessions if max_sessions is not None else settings.fact_extraction_memory_sessions_cap
    rows = db.execute(
        select(
            Episode.session_id,
            Episode.content,
            Episode.created_at,
        )
        .where(
            Episode.user_id == user_id,
            Episode.episode_kind == "memory",
        )
        .order_by(Episode.session_id, Episode.created_at.desc())
    ).all()
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for session_id, content, created_at in rows:
        if session_id in seen:
            continue
        seen.add(session_id)
        out.append(
            {
                "session_id": session_id,
                "summary": (content or "")[:4000],
                "created_at": created_at.isoformat() if created_at else "",
            }
        )
        if len(out) >= cap:
            break
    return out


def load_offload_batch(
    db: Session,
    user_id: int,
    session_id: str,
    memory_episode: Episode | None,
) -> str:
    if memory_episode is None or not memory_episode.metadata_json:
        return ""
    ids = memory_episode.metadata_json.get("offloaded_episode_ids") or []
    if not ids:
        return ""
    episodes = db.scalars(
        select(Episode).where(
            Episode.user_id == user_id,
            Episode.id.in_(ids),
        )
    ).all()
    by_id = {ep.id: ep for ep in episodes}
    lines: list[str] = []
    for eid in ids:
        ep = by_id.get(eid)
        if not ep:
            continue
        role = ep.role if ep.role in ("user", "assistant") else "user"
        lines.append(f"{role}: {ep.content}")
    return "\n".join(lines)


def load_existing_facts_for_prompt(db: Session, user_id: int) -> list[dict[str, Any]]:
    facts = db.scalars(
        select(UserFact)
        .where(
            UserFact.user_id == user_id,
            UserFact.status == "active",
        )
        .order_by(UserFact.updated_at.desc())
    ).all()
    out: list[dict[str, Any]] = []
    for f in facts:
        out.append(
            {
                "key": f.fact_key,
                "canonical_key": f.canonical_key or f.fact_key,
                "value": f.fact_value,
                "confidence": float(f.confidence),
                "pinned": bool(f.pinned),
                "status": f.status,
            }
        )
    return out


def build_extraction_payload(
    db: Session,
    user_id: int,
    *,
    session_id: str | None,
    scope: str,
    memory_episode: Episode | None = None,
) -> dict[str, Any]:
    transcript = load_active_transcript(
        db, user_id, session_id=session_id if scope == "session" else None
    )
    if scope == "global":
        memories = load_latest_memory_summaries(db, user_id)
    elif scope == "session" and session_id:
        mem = db.scalar(
            select(Episode)
            .where(
                Episode.user_id == user_id,
                Episode.session_id == session_id,
                Episode.episode_kind == "memory",
            )
            .order_by(Episode.created_at.desc())
            .limit(1)
        )
        memories = (
            [{"session_id": session_id, "summary": (mem.content or "")[:4000]}]
            if mem
            else []
        )
    else:
        memories = []

    offload_text = ""
    if scope == "offload" and session_id:
        offload_text = load_offload_batch(db, user_id, session_id, memory_episode)

    existing = load_existing_facts_for_prompt(db, user_id)
    return {
        "scope": scope,
        "session_id": session_id,
        "transcript": transcript,
        "memory_summaries": memories,
        "offload_batch": offload_text,
        "existing_facts": existing,
    }


def payload_to_llm_text(payload: dict[str, Any]) -> str:
    parts = [f"Scope: {payload.get('scope')}"]
    if payload.get("transcript"):
        parts.append("## Recent messages\n" + payload["transcript"])
    if payload.get("memory_summaries"):
        parts.append(
            "## Memory summaries\n"
            + json.dumps(payload["memory_summaries"], ensure_ascii=False)
        )
    if payload.get("offload_batch"):
        parts.append("## Offloaded messages\n" + payload["offload_batch"])
    if payload.get("existing_facts"):
        parts.append(
            "## Existing user facts\n"
            + json.dumps(payload["existing_facts"], ensure_ascii=False)
        )
    return "\n\n".join(parts)
