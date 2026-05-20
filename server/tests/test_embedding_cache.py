"""Postgres embedding cache hit/miss."""

from __future__ import annotations

import uuid

import pytest

from app.services.embedding_cache_metrics import reset_embedding_cache_metrics
from app.services.embedding_cache_store import (
    embedding_cache_key,
    lookup_cached_embedding,
    store_cached_embedding,
)
from app.services.gemini_embeddings import embed_text


@pytest.mark.asyncio
async def test_embedding_cache_hit_miss(monkeypatch):
    reset_embedding_cache_metrics()
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    from app.core.config import get_settings

    get_settings.cache_clear()

    vec = [0.1] * get_settings().gemini_embedding_dimensions
    key = embedding_cache_key(f"cache-test-{uuid.uuid4().hex}")

    assert lookup_cached_embedding(key) is None
    store_cached_embedding(key, vec)
    cached = lookup_cached_embedding(key)
    assert cached is not None
    assert len(cached) == len(vec)


@pytest.mark.asyncio
async def test_embed_text_uses_cache(monkeypatch):
    reset_embedding_cache_metrics()
    calls = {"n": 0}

    async def fake_embed(text: str) -> list[float]:
        calls["n"] += 1
        dim = __import__("app.core.config", fromlist=["get_settings"]).get_settings().gemini_embedding_dimensions
        return [0.2] * dim

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    from app.core.config import get_settings

    get_settings.cache_clear()
    import app.services.gemini_embeddings as ge

    monkeypatch.setattr(ge, "_embed_text_uncached", fake_embed)

    text = f"same-text-{uuid.uuid4().hex}"
    v1 = await embed_text(text)
    v2 = await embed_text(text)
    assert v1 == v2
    assert calls["n"] == 1
