"""Embed active message episodes for selective context retrieval (Phase 5)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.episode import Episode
from app.services.gemini_embeddings import embed_text
from app.services.token_counter import count_text_tokens

logger = logging.getLogger(__name__)


def _split_text_by_tokens(
    text: str,
    *,
    chunk_size: int,
    overlap: int,
) -> list[str]:
    """Split text into token-bounded chunks with overlap."""
    from app.services.token_counter import _encoding

    enc = _encoding()
    tokens = enc.encode(text)
    if len(tokens) <= chunk_size:
        return [text]
    chunks: list[str] = []
    start = 0
    step = max(1, chunk_size - overlap)
    while start < len(tokens):
        piece = tokens[start : start + chunk_size]
        if not piece:
            break
        chunks.append(enc.decode(piece))
        if start + chunk_size >= len(tokens):
            break
        start += step
    return chunks


async def embed_episode_row(db: Session, episode: Episode) -> None:
    """Embed one message episode; chunk if over threshold."""
    if episode.episode_kind != "message" or episode.is_offloaded:
        episode.embed_status = "skipped"
        return
    if not settings.gemini_api_key:
        episode.embed_status = "pending"
        return

    content = (episode.content or "").strip()
    if not content:
        episode.embed_status = "skipped"
        episode.token_count = 0
        return

    token_count = count_text_tokens(content)
    episode.token_count = token_count
    threshold = settings.active_turn_chunk_threshold_tokens

    if token_count <= threshold:
        try:
            vector = await embed_text(content)
            episode.embedding = vector
            episode.embed_status = "ready"
            db.execute(
                delete(Episode).where(
                    Episode.parent_episode_id == episode.id,
                    Episode.episode_kind == "message_chunk",
                )
            )
        except Exception:
            logger.warning(
                "embed_episode failed episode_id=%s", episode.id, exc_info=True
            )
            episode.embed_status = "failed"
        return

    # Long message: child chunks
    db.execute(
        delete(Episode).where(
            Episode.parent_episode_id == episode.id,
            Episode.episode_kind == "message_chunk",
        )
    )
    chunks = _split_text_by_tokens(
        content,
        chunk_size=settings.active_turn_chunk_size_tokens,
        overlap=settings.active_turn_chunk_overlap_tokens,
    )
    episode.embedding = None
    episode.embed_status = "ready"
    now = datetime.now(timezone.utc)
    for idx, chunk_text in enumerate(chunks):
        try:
            vector = await embed_text(chunk_text)
            status = "ready"
        except Exception:
            logger.warning(
                "embed_chunk failed parent=%s idx=%s",
                episode.id,
                idx,
                exc_info=True,
            )
            vector = None
            status = "failed"
        child = Episode(
            user_id=episode.user_id,
            session_id=episode.session_id,
            episode_kind="message_chunk",
            role=episode.role,
            content=chunk_text,
            is_offloaded=False,
            embedding=vector,
            token_count=count_text_tokens(chunk_text),
            embed_status=status,
            parent_episode_id=episode.id,
            chunk_index=idx,
            created_at=now,
        )
        db.add(child)


async def embed_episodes_by_ids(
    db: Session,
    user_id: int,
    episode_ids: list[int],
) -> None:
    """Embed specific message episodes (idempotent)."""
    if not episode_ids:
        return
    rows = list(
        db.scalars(
            select(Episode).where(
                Episode.user_id == user_id,
                Episode.id.in_(episode_ids),
                Episode.episode_kind == "message",
                Episode.is_offloaded.is_(False),
            )
        ).all()
    )
    for ep in rows:
        if ep.embed_status == "ready" and ep.embedding is not None:
            continue
        if ep.embed_status == "ready" and ep.parent_episode_id is None:
            # Chunked parent: children hold embeddings
            child_ready = db.scalar(
                select(Episode.id)
                .where(
                    Episode.parent_episode_id == ep.id,
                    Episode.episode_kind == "message_chunk",
                    Episode.embed_status == "ready",
                )
                .limit(1)
            )
            if child_ready:
                continue
        await embed_episode_row(db, ep)
    db.commit()


async def embed_pending_session_messages(
    db: Session,
    user_id: int,
    session_id: str,
    *,
    limit: int = 20,
) -> None:
    """Backfill pending embeddings for a session (newest first)."""
    pending = list(
        db.scalars(
            select(Episode)
            .where(
                Episode.user_id == user_id,
                Episode.session_id == session_id,
                Episode.episode_kind == "message",
                Episode.is_offloaded.is_(False),
                Episode.embed_status.in_(("pending", "failed")),
            )
            .order_by(Episode.created_at.desc())
            .limit(limit)
        ).all()
    )
    for ep in pending:
        await embed_episode_row(db, ep)
    if pending:
        db.commit()


async def embed_new_turns(user_id: int, session_id: str) -> None:
    """Background: embed recent pending messages in session."""
    from app.services.database import open_tenant_session

    db = open_tenant_session(user_id)
    try:
        await embed_pending_session_messages(db, user_id, session_id, limit=12)
    except Exception:
        logger.exception("embed_new_turns failed session=%s", session_id)
    finally:
        db.close()
