"""Tests for per-turn embedding and chunk splitting."""

from app.services.turn_embedding import _split_text_by_tokens
from app.services.token_counter import count_text_tokens


def test_split_text_short_unchanged():
    text = "Hello world"
    chunks = _split_text_by_tokens(text, chunk_size=100, overlap=10)
    assert chunks == [text]


def test_split_text_produces_multiple_chunks():
    text = "word " * 2000
    chunks = _split_text_by_tokens(text, chunk_size=50, overlap=10)
    assert len(chunks) > 1
    for c in chunks:
        assert count_text_tokens(c) <= 55
