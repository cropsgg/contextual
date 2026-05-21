"""Unit tests for active-turn hybrid scoring helpers."""

from app.services.active_turn_retrieval import (
    RankedActiveTurn,
    _entity_overlap,
    _mmr_select,
    _recency_score,
)


def test_recency_decay_recent_higher():
    assert _recency_score(0) > _recency_score(10) > _recency_score(50)


def test_entity_overlap():
    assert _entity_overlap("PLUMBER42 codeword", "secret PLUMBER42") > 0.2
    assert _entity_overlap("hello", "goodbye") == 0.0


def test_mmr_diversifies_similar():
    candidates = [
        RankedActiveTurn(
            episode_id=1,
            parent_episode_id=None,
            role="user",
            content="debug postgres connection pool timeout",
            score=0.9,
            reason="vector",
            chronological_index=0,
        ),
        RankedActiveTurn(
            episode_id=2,
            parent_episode_id=None,
            role="user",
            content="debug postgres connection pool latency",
            score=0.85,
            reason="vector",
            chronological_index=1,
        ),
        RankedActiveTurn(
            episode_id=3,
            parent_episode_id=None,
            role="user",
            content="favorite color is blue",
            score=0.7,
            reason="bm25",
            chronological_index=2,
        ),
    ]
    picked = _mmr_select(candidates, top_k=2)
    ids = {p.episode_id for p in picked}
    assert len(picked) == 2
    assert 3 in ids
