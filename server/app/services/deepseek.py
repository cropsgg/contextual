"""DeepSeek (OpenAI-compatible) streaming client."""

import json
import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
from openai import APIError, AsyncOpenAI

from app.core.config import settings

if TYPE_CHECKING:
    from app.services.memory_retrieval import ScoredMemory

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None


@dataclass
class ChatCompletionUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def _apply_response_usage(resp: object, usage_out: ChatCompletionUsage | None) -> None:
    if usage_out is None:
        return
    u = getattr(resp, "usage", None)
    if u is None:
        return
    usage_out.prompt_tokens = int(getattr(u, "prompt_tokens", 0) or 0)
    usage_out.completion_tokens = int(getattr(u, "completion_tokens", 0) or 0)


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        # Lazy singleton; restart the process if API credentials change.
        timeout = httpx.Timeout(120.0, connect=15.0)
        _client = AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url.rstrip("/"),
            timeout=timeout,
            max_retries=0,
        )
    return _client


async def stream_chat_completion(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    usage_out: ChatCompletionUsage | None = None,
) -> AsyncIterator[str]:
    if not settings.deepseek_api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not set")

    model_name = model or settings.deepseek_model
    client = _get_client()
    try:
        stream = await client.chat.completions.create(
            model=model_name,
            messages=messages,
            stream=True,
            stream_options={"include_usage": True},
        )
    except APIError as e:
        logger.warning("DeepSeek API error: %s", e)
        raise RuntimeError(f"DeepSeek API error: {e}") from e
    except Exception as e:
        logger.exception("DeepSeek request failed")
        raise RuntimeError("DeepSeek request failed") from e

    async for chunk in stream:
        if usage_out is not None and getattr(chunk, "usage", None) is not None:
            u = chunk.usage
            usage_out.prompt_tokens = int(u.prompt_tokens or 0)
            usage_out.completion_tokens = int(u.completion_tokens or 0)
        if not chunk.choices:
            continue
        choice = chunk.choices[0]
        delta = choice.delta
        if delta and delta.content:
            yield delta.content


async def generate_short_title(
    user_message: str,
    *,
    usage_out: ChatCompletionUsage | None = None,
) -> str:
    """One-line session title from the first user message."""
    if not settings.deepseek_api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not set")

    client = _get_client()
    resp = await client.chat.completions.create(
        model=settings.deepseek_summarize_model,
        messages=[
            {
                "role": "system",
                "content": (
                    "Write a very short chat title (max 8 words). "
                    "Reply with title text only, no quotes."
                ),
            },
            {"role": "user", "content": user_message.strip()[:2000]},
        ],
        stream=False,
    )
    _apply_response_usage(resp, usage_out)
    choice0 = resp.choices[0] if resp.choices else None
    msg = choice0.message if choice0 else None
    return ((msg.content if msg else None) or "").strip()


async def summarize_conversation(
    to_summarize: list[dict[str, str]],
    previous_summary: str | None,
    *,
    usage_out: ChatCompletionUsage | None = None,
) -> str:
    """Non-streaming summarization for context compression."""
    if not settings.deepseek_api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not set")

    prior = (
        f"\n\nExisting compressed summary of earlier conversation (merge and update):\n{previous_summary}\n"
        if previous_summary
        else ""
    )
    body = "\n".join(
        f"{m.get('role', 'user')}: {m.get('content', '')}" for m in to_summarize
    )
    user_content = (
        prior
        + "\n\nSummarize the following conversation history. Preserve all key facts, "
        "unresolved questions, and architectural decisions. Output a concise summary "
        "suitable for long-term memory.\n\n"
        + body
    )

    client = _get_client()
    try:
        resp = await client.chat.completions.create(
            model=settings.deepseek_summarize_model,
            messages=[
                {
                    "role": "system",
                    "content": "You compress chat history into durable memory notes.",
                },
                {"role": "user", "content": user_content},
            ],
            stream=False,
        )
    except APIError as e:
        logger.warning("DeepSeek summarize error: %s", e)
        raise RuntimeError(f"DeepSeek summarize error: {e}") from e

    _apply_response_usage(resp, usage_out)
    choice0 = resp.choices[0] if resp.choices else None
    msg = choice0.message if choice0 else None
    text = (msg.content if msg else None) or ""
    text = text.strip()
    if not text:
        raise RuntimeError("DeepSeek summarization returned empty text")
    return text


async def _json_completion(
    system: str,
    user: str,
    *,
    model: str | None = None,
    usage_out: ChatCompletionUsage | None = None,
) -> str:
    if not settings.deepseek_api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not set")
    client = _get_client()
    resp = await client.chat.completions.create(
        model=model or settings.deepseek_rerank_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        stream=False,
    )
    _apply_response_usage(resp, usage_out)
    choice0 = resp.choices[0] if resp.choices else None
    msg = choice0.message if choice0 else None
    return ((msg.content if msg else None) or "").strip()


def _parse_json_object(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


async def rerank_memory_candidates(
    query: str,
    candidates: list["ScoredMemory"],
    *,
    keep: int,
    usage_out: ChatCompletionUsage | None = None,
) -> list[int]:
    """Return indices into candidates ordered by relevance (best first)."""
    def _sid_label(sid: str) -> str:
        return f"{sid[:8]}…" if len(sid) >= 8 else sid

    numbered = "\n".join(
        f"[{i}] (session {_sid_label(c.session_id)}): {c.snippet[:500]}"
        for i, c in enumerate(candidates)
    )
    user_content = (
        f"User query: {query}\n\n"
        f"Memory snippets:\n{numbered}\n\n"
        f"Pick the {keep} most relevant snippet indices for answering the query. "
        "Respond with JSON only: {\"indices\": [0, 2]}"
    )
    raw = await _json_completion(
        "You rerank memory snippets by relevance. Output valid JSON only.",
        user_content,
        usage_out=usage_out,
    )
    data = _parse_json_object(raw)
    indices = data.get("indices", [])
    if not isinstance(indices, list):
        raise ValueError("indices must be a list")
    out: list[int] = []
    for raw in indices:
        try:
            out.append(int(raw))
        except (TypeError, ValueError):
            continue
    return out


async def extract_facts_from_transcript(
    transcript: str,
    *,
    usage_out: ChatCompletionUsage | None = None,
) -> list[dict]:
    """Return list of {key, value, confidence} from conversation transcript."""
    user_content = (
        "Analyze the recent conversation history. Identify any persistent facts about "
        "the user (name, occupation, likes, dislikes, current projects). Output them as "
        "a JSON object only: {\"facts\": [{\"key\": \"snake_case_key\", \"value\": \"...\", "
        "\"confidence\": 0.9}]}. Only include high-confidence information. "
        "If none, return {\"facts\": []}.\n\n"
        f"{transcript}"
    )
    raw = await _json_completion(
        "You extract durable user profile facts. Keys must be snake_case. JSON only.",
        user_content,
        usage_out=usage_out,
    )
    data = _parse_json_object(raw)
    facts = data.get("facts", [])
    if not isinstance(facts, list):
        return []
    return [f for f in facts if isinstance(f, dict)]


async def extract_fact_changes(
    payload_text: str,
    *,
    usage_out: ChatCompletionUsage | None = None,
) -> list[dict]:
    """Return list of change dicts: upsert | delete | noop with canonical_key."""
    user_content = (
        "Reconcile user profile facts against the sources below. "
        "Output JSON only: {\"changes\": [{\"action\": \"upsert|delete|noop\", "
        "\"key\": \"snake_case\", \"canonical_key\": \"snake_case\", "
        "\"aliases\": [\"optional_alias\"], \"value\": \"...\", \"confidence\": 0.0-1.0}]}. "
        "Rules: prefer noop when value unchanged; delete when contradicted in recent "
        "messages or memory summaries; never delete pinned facts; emit canonical_key; "
        "list aliases for merge. If no changes, {\"changes\": []}.\n\n"
        f"{payload_text}"
    )
    raw = await _json_completion(
        "You reconcile durable user facts. Actions: upsert, delete, noop. JSON only.",
        user_content,
        usage_out=usage_out,
    )
    data = _parse_json_object(raw)
    changes = data.get("changes", [])
    if not isinstance(changes, list):
        return []
    return [c for c in changes if isinstance(c, dict)]
