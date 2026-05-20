"""Google Gemini embedding API (REST) for Phase 2+."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.core.config import settings
from app.services.embedding_cache_store import (
    embedding_cache_key,
    lookup_cached_embedding,
    store_cached_embedding,
)

logger = logging.getLogger(__name__)

_GEMINI_EMBED_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:embedContent"
_embed_locks: dict[str, asyncio.Lock] = {}


def _parse_embedding_values(data: dict[str, Any]) -> list[float] | None:
    emb = data.get("embedding")
    values = None
    if isinstance(emb, dict):
        values = emb.get("values")
    if not values and isinstance(data.get("embeddings"), list) and data["embeddings"]:
        first = data["embeddings"][0]
        if isinstance(first, dict) and "values" in first:
            values = first["values"]
    if not isinstance(values, list) or not values:
        return None
    return [float(x) for x in values]


def _normalize_vector(values: list[float], dim: int) -> list[float]:
    if len(values) == dim:
        return values
    if len(values) > dim:
        logger.warning(
            "Truncating Gemini embedding from %s to %s dimensions (MRL truncation)",
            len(values),
            dim,
        )
        return values[:dim]
    logger.warning(
        "Padding Gemini embedding from %s to %s dimensions with zeros",
        len(values),
        dim,
    )
    return values + [0.0] * (dim - len(values))


async def _embed_text_uncached(text: str) -> list[float]:
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")

    url = _GEMINI_EMBED_URL.format(model=settings.gemini_embedding_model)
    dim = settings.gemini_embedding_dimensions
    base: dict[str, Any] = {
        "model": f"models/{settings.gemini_embedding_model}",
        "content": {"parts": [{"text": text}]},
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
        last_err: str | None = None
        for use_dim in (True, False):
            payload = dict(base)
            if use_dim:
                payload["outputDimensionality"] = dim
            headers = {
                "Content-Type": "application/json",
                "x-goog-api-key": settings.gemini_api_key,
            }
            r = await client.post(url, json=payload, headers=headers)
            if r.status_code >= 400:
                last_err = r.text[:800]
                if use_dim and r.status_code == 400:
                    logger.warning(
                        "Gemini embed rejected outputDimensionality; retrying without it: %s",
                        last_err,
                    )
                    continue
                if r.status_code == 403 and "API_KEY_SERVICE_BLOCKED" in last_err:
                    logger.error(
                        "Gemini API key is blocked for generativelanguage.googleapis.com "
                        "(enable Generative Language API and key restrictions in Google Cloud "
                        "project tied to this key). See: "
                        "https://console.cloud.google.com/apis/library/generativelanguage.googleapis.com"
                    )
                logger.warning("Gemini embed HTTP %s: %s", r.status_code, last_err)
                r.raise_for_status()

            data = r.json()
            values = _parse_embedding_values(data)
            if values is None:
                raise RuntimeError("Gemini embed response missing embedding values")
            return _normalize_vector(values, dim)

        raise RuntimeError(last_err or "Gemini embed failed")


async def embed_text(text: str) -> list[float]:
    """Embed with Postgres cache (content-addressed; per-key lock on miss)."""
    key = embedding_cache_key(text)
    cached = await asyncio.to_thread(lookup_cached_embedding, key)
    if cached is not None:
        return cached

    lock = _embed_locks.setdefault(key, asyncio.Lock())
    async with lock:
        cached = await asyncio.to_thread(lookup_cached_embedding, key)
        if cached is not None:
            return cached
        vector = await _embed_text_uncached(text)
        await asyncio.to_thread(store_cached_embedding, key, vector)
        return vector
