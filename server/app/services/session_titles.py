"""LLM-generated session titles from the first user message."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.deepseek import ChatCompletionUsage, generate_short_title
from app.services.token_quota import (
    deepseek_quota_blocked,
    fill_usage_from_estimate,
    maybe_rollover_user,
    record_llm_usage,
)

logger = logging.getLogger(__name__)

_TITLE_MAX_LEN = 80


def _truncate_title(text: str) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= _TITLE_MAX_LEN:
        return cleaned
    return cleaned[: _TITLE_MAX_LEN - 1].rstrip() + "…"


async def generate_session_title(
    first_user_message: str,
    *,
    db: Session | None = None,
    user_id: int | None = None,
    session_id: str | None = None,
) -> str:
    """Return a short title; falls back to truncation if LLM unavailable or quota blocked."""
    msg = first_user_message.strip()
    if not msg:
        return "New conversation"
    if not settings.deepseek_api_key:
        return _truncate_title(msg)

    if db is not None and user_id is not None:
        from app.models.user import User

        user = db.get(User, user_id)
        if user is not None:
            maybe_rollover_user(db, user)
            if deepseek_quota_blocked(user):
                return _truncate_title(msg)

    try:
        usage = ChatCompletionUsage()
        title = await generate_short_title(msg, usage_out=usage)
        if usage.total_tokens <= 0:
            fill_usage_from_estimate(
                usage,
                prompt_messages=[{"role": "user", "content": msg[:2000]}],
                completion_text=title,
            )
        if db is not None and user_id is not None and usage.total_tokens > 0:
            record_llm_usage(
                db,
                user_id,
                model=settings.deepseek_summarize_model,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                session_id=session_id,
            )
        title = title.strip().strip('"').strip("'")
        if title:
            return _truncate_title(title)
    except Exception:
        logger.exception("session title generation failed")

    return _truncate_title(msg)
