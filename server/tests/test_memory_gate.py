"""Memory gate: signal heuristics, post-validators, and pipeline."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import settings
from app.services.memory_gate import (
    extract_gated_fact_changes,
    gate_fact_candidates,
    has_memory_signals,
    propose_fact_candidates,
)


def test_has_memory_signals_explicit_remember():
    text = "## Recent messages\nuser: Remember my codeword is TIGER12"
    assert has_memory_signals(text) is True


def test_has_memory_signals_strong_durable():
    text = "## Recent messages\nuser: I live in Austin, Texas"
    assert has_memory_signals(text) is True


def test_has_memory_signals_small_talk_only():
    text = "## Recent messages\nuser: OK\nuser: thanks\nuser: sounds good"
    assert has_memory_signals(text) is False


def test_has_memory_signals_ignores_stale_remember_in_lookback():
    text = (
        "## Recent messages\n"
        "user: Remember my codeword is OLD99\n"
        "assistant: OK\n"
        "user: OK\n"
        "user: thanks"
    )
    assert has_memory_signals(text) is False


def test_has_memory_signals_ephemeral_task():
    text = "## Recent messages\nuser: Use port 3000 for this bug today"
    assert has_memory_signals(text) is False


def test_has_memory_signals_correction():
    text = "## Recent messages\nuser: Actually my codeword is NEWCODE"
    assert has_memory_signals(text) is True


def test_has_memory_signals_memory_summary_section():
    text = (
        "## Recent messages\nuser: OK\nuser: thanks\n\n"
        '## Memory summaries\n[{"summary": "User said remember codeword TIGER12"}]'
    )
    assert has_memory_signals(text) is True


@pytest.mark.asyncio
async def test_extract_gated_skips_when_no_signals():
    with patch.object(settings, "memory_gate_enabled", True), patch.object(
        settings, "memory_extraction_skip_if_no_signals", True
    ):
        result = await extract_gated_fact_changes(
            "## Recent messages\nuser: OK\nuser: thanks",
            scope="session",
        )
    assert result.skipped == "no_memory_signals"
    assert result.changes == []


@pytest.mark.asyncio
async def test_extract_gated_global_does_not_skip_small_talk():
    with patch.object(settings, "memory_gate_enabled", True), patch.object(
        settings, "memory_extraction_skip_if_no_signals", True
    ), patch(
        "app.services.memory_gate.propose_fact_candidates",
        new_callable=AsyncMock,
        return_value=[],
    ):
        result = await extract_gated_fact_changes(
            "## Recent messages\nuser: OK\nuser: thanks",
            scope="global",
        )
    assert result.skipped is None


@pytest.mark.asyncio
async def test_propose_fact_candidates_parses_json():
    payload = {"candidates": [{"key": "secret_codeword", "value": "X", "confidence": 0.9}]}
    with patch(
        "app.services.memory_gate._json_completion",
        new_callable=AsyncMock,
        return_value=json.dumps(payload),
    ):
        out = await propose_fact_candidates("transcript")
    assert len(out) == 1
    assert out[0]["key"] == "secret_codeword"


@pytest.mark.asyncio
async def test_gate_remembers_explicit_codeword():
    candidates = [
        {
            "key": "secret_codeword",
            "canonical_key": "secret_codeword",
            "value": "TIGER12",
            "confidence": 0.95,
            "evidence": "user: Remember my codeword is TIGER12",
            "signal": "explicit_remember",
        }
    ]
    decisions = {
        "decisions": [
            {
                "key": "secret_codeword",
                "verdict": "remember",
                "action": "upsert",
                "reason": "Explicit remember request",
                "confidence": 0.95,
            }
        ]
    }
    with patch(
        "app.services.memory_gate._json_completion",
        new_callable=AsyncMock,
        return_value=json.dumps(decisions),
    ):
        result = await gate_fact_candidates(candidates, "transcript")
    assert len(result.changes) == 1
    assert result.changes[0]["action"] == "upsert"
    assert result.changes[0]["value"] == "TIGER12"


@pytest.mark.asyncio
async def test_gate_ignores_assistant_only_evidence():
    candidates = [
        {
            "key": "favorite_color",
            "canonical_key": "favorite_color",
            "value": "blue",
            "confidence": 0.9,
            "evidence": "assistant: Your favorite color is blue",
            "signal": "unknown",
        }
    ]
    decisions = {
        "decisions": [
            {
                "key": "favorite_color",
                "verdict": "remember",
                "action": "upsert",
                "reason": "Stated in chat",
                "confidence": 0.9,
            }
        ]
    }
    with patch(
        "app.services.memory_gate._json_completion",
        new_callable=AsyncMock,
        return_value=json.dumps(decisions),
    ):
        result = await gate_fact_candidates(candidates, "transcript")
    assert result.changes == []
    assert result.ignored >= 1
    assert result.gate_decisions[0]["verdict"] == "ignore"


@pytest.mark.asyncio
async def test_gate_ignores_blocked_task_key():
    candidates = [
        {
            "key": "debug_port",
            "canonical_key": "debug_port",
            "value": "3000",
            "confidence": 0.9,
            "evidence": "user: Use port 3000",
            "signal": "unknown",
        }
    ]
    decisions = {
        "decisions": [
            {
                "key": "debug_port",
                "verdict": "remember",
                "action": "upsert",
                "reason": "User mentioned port",
                "confidence": 0.9,
            }
        ]
    }
    with patch(
        "app.services.memory_gate._json_completion",
        new_callable=AsyncMock,
        return_value=json.dumps(decisions),
    ):
        result = await gate_fact_candidates(candidates, "transcript")
    assert result.changes == []
    assert any(d["verdict"] == "ignore" for d in result.gate_decisions)


@pytest.mark.asyncio
async def test_gate_rejects_low_confidence():
    candidates = [
        {
            "key": "favorite_food",
            "canonical_key": "favorite_food",
            "value": "tacos",
            "confidence": 0.6,
            "evidence": "user: I had tacos for lunch",
            "signal": "preference_statement",
        }
    ]
    decisions = {
        "decisions": [
            {
                "key": "favorite_food",
                "verdict": "remember",
                "action": "upsert",
                "reason": "Food preference",
                "confidence": 0.6,
            }
        ]
    }
    with patch.object(settings, "memory_gate_min_confidence", 0.75), patch(
        "app.services.memory_gate._json_completion",
        new_callable=AsyncMock,
        return_value=json.dumps(decisions),
    ):
        result = await gate_fact_candidates(candidates, "transcript")
    assert result.changes == []


@pytest.mark.asyncio
async def test_gate_remember_noop_counts_as_ignored():
    candidates = [
        {
            "key": "favorite_food",
            "canonical_key": "favorite_food",
            "value": "tacos",
            "confidence": 0.9,
            "evidence": "user: I like tacos",
            "signal": "preference_statement",
        }
    ]
    decisions = {
        "decisions": [
            {
                "key": "favorite_food",
                "verdict": "remember",
                "action": "noop",
                "reason": "Already known",
                "confidence": 0.9,
            }
        ]
    }
    with patch(
        "app.services.memory_gate._json_completion",
        new_callable=AsyncMock,
        return_value=json.dumps(decisions),
    ):
        result = await gate_fact_candidates(candidates, "transcript")
    assert result.changes == []
    assert result.ignored >= 1
    assert result.gate_decisions[0]["verdict"] == "ignore"


@pytest.mark.asyncio
async def test_propose_handles_malformed_json():
    with patch(
        "app.services.memory_gate._json_completion",
        new_callable=AsyncMock,
        return_value="not json at all",
    ):
        out = await propose_fact_candidates("transcript")
    assert out == []


@pytest.mark.asyncio
async def test_extract_gated_pipeline_two_stage():
    propose = {
        "candidates": [
            {
                "key": "city_of_residence",
                "canonical_key": "city_of_residence",
                "value": "Austin, Texas",
                "confidence": 0.9,
                "evidence": "user: I live in Austin, Texas",
                "signal": "strong_durable_statement",
            }
        ]
    }
    gate = {
        "decisions": [
            {
                "key": "city_of_residence",
                "verdict": "remember",
                "action": "upsert",
                "reason": "Durable self-identification",
                "confidence": 0.9,
            }
        ]
    }
    with patch.object(settings, "memory_gate_enabled", True), patch.object(
        settings, "memory_extraction_skip_if_no_signals", False
    ), patch(
        "app.services.memory_gate._json_completion",
        new_callable=AsyncMock,
        side_effect=[json.dumps(propose), json.dumps(gate)],
    ):
        result = await extract_gated_fact_changes(
            "## Recent messages\nuser: I live in Austin, Texas",
            scope="session",
        )
    assert result.skipped is None
    assert len(result.changes) == 1
    assert result.changes[0]["key"] == "city_of_residence"
