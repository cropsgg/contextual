"""Approximate token counting for chat messages (OpenAI-style encoding).

DeepSeek does not publish an official tiktoken mapping; `cl100k_base` is a practical
approximation for English-heavy prompts and threshold triggers.
"""

from __future__ import annotations

import tiktoken

from app.core.config import settings

_enc_cache: tiktoken.Encoding | None = None


def _encoding() -> tiktoken.Encoding:
    global _enc_cache
    if _enc_cache is None:
        try:
            _enc_cache = tiktoken.encoding_for_model(settings.token_encoding_model)
        except KeyError:
            _enc_cache = tiktoken.get_encoding("cl100k_base")
    return _enc_cache


def count_chat_messages_tokens(messages: list[dict[str, str]]) -> int:
    """Rough OpenAI-style message overhead + content token count."""
    enc = _encoding()
    total = 0
    per_msg = 4  # approximation for role/content framing
    for m in messages:
        total += per_msg
        role = m.get("role", "")
        content = m.get("content", "")
        total += len(enc.encode(role))
        total += len(enc.encode(content))
    total += 2  # assistant priming slack
    return total


def count_text_tokens(text: str) -> int:
    return len(_encoding().encode(text))
