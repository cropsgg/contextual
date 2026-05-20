"""Two-stage memory gate: propose candidates, then remember/ignore filter."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.services.deepseek import (
    ChatCompletionUsage,
    _json_completion,
    _parse_json_object,
    extract_fact_changes,
)
from app.services.fact_extraction_sources import normalize_fact_key

logger = logging.getLogger(__name__)

_EXPLICIT_REMEMBER = re.compile(
    r"\b(remember|don't forget|do not forget|note that|keep in mind)\b",
    re.IGNORECASE,
)
_STRONG_DURABLE = re.compile(
    r"\b(I live in|I'm a|I am a|I work as|my name is|my codeword is|"
    r"call me|I go by|I prefer)\b",
    re.IGNORECASE,
)
_CORRECTION = re.compile(
    r"\b(actually|I meant|correction|not anymore|anymore it's|instead it's)\b",
    re.IGNORECASE,
)
_BLOCKED_KEY_PREFIXES = ("debug_", "temp_", "task_", "tmp_")

_PROPOSE_SYSTEM = (
    "You propose candidate durable facts about the user from chat sources. "
    "Over-propose slightly; a later gate will filter. Keys must be snake_case. "
    "Output valid JSON only."
)

_GATE_SYSTEM = (
    "You gate which candidate facts belong in long-term user memory. "
    "Verdict remember only for: explicit remember intent OR clear durable "
    "self-identification (location, job, long-term preferences, taught secrets). "
    "Verdict ignore for: hypotheticals, one-off tasks, assistant-only claims, "
    "small talk, third-party info, inferred sensitive topics, weak casual mentions. "
    "Output valid JSON only."
)


@dataclass
class GateResult:
    changes: list[dict[str, Any]] = field(default_factory=list)
    ignored: int = 0
    gate_decisions: list[dict[str, Any]] = field(default_factory=list)
    skipped: str | None = None
    candidates_proposed: int = 0


def _signal_match(text: str) -> bool:
    return bool(
        _EXPLICIT_REMEMBER.search(text)
        or _STRONG_DURABLE.search(text)
        or _CORRECTION.search(text)
    )


def has_memory_signals(payload_text: str, *, recent_user_lines: int = 2) -> bool:
    """Zero-LLM check on recent user lines and memory summaries."""
    user_lines: list[str] = []
    for line in payload_text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("user:"):
            user_lines.append(stripped[5:].strip())
    if user_lines:
        window = "\n".join(user_lines[-recent_user_lines:])
        if _signal_match(window):
            return True

    # Global extraction includes compressed memory summaries without user: prefixes.
    if "## Memory summaries" in payload_text:
        try:
            start = payload_text.index("## Memory summaries")
            chunk = payload_text[start : start + 8000]
            if _signal_match(chunk):
                return True
        except ValueError:
            pass
    return False


def _safe_parse_json(text: str) -> dict[str, Any]:
    try:
        parsed = _parse_json_object(text)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        logger.warning("memory_gate JSON parse failed: %s", exc)
        return {}


def _normalize_candidate(raw: dict[str, Any]) -> dict[str, Any] | None:
    key_raw = raw.get("key") or raw.get("fact_key") or ""
    if not key_raw:
        return None
    key = normalize_fact_key(str(key_raw))
    canonical = normalize_fact_key(
        str(raw.get("canonical_key") or raw.get("key") or key_raw)
    )
    value = str(raw.get("value") or raw.get("fact_value") or "").strip()
    try:
        confidence = float(raw.get("confidence", 0.8))
    except (TypeError, ValueError):
        confidence = 0.8
    confidence = max(0.0, min(1.0, confidence))
    evidence = str(raw.get("evidence") or "").strip()
    signal = str(raw.get("signal") or "unknown").strip()
    return {
        "key": key,
        "canonical_key": canonical or key,
        "value": value,
        "confidence": confidence,
        "evidence": evidence,
        "signal": signal,
    }


def _assistant_only_evidence(evidence: str) -> bool:
    ev = evidence.strip().lower()
    return ev.startswith("assistant:")


def _blocked_key(key: str, signal: str) -> bool:
    if signal == "explicit_remember":
        return False
    return any(key.startswith(prefix) for prefix in _BLOCKED_KEY_PREFIXES)


async def propose_fact_candidates(
    payload_text: str,
    *,
    usage_out: ChatCompletionUsage | None = None,
) -> list[dict[str, Any]]:
    user_content = (
        "From the sources below, propose candidate facts about the user. "
        "Output JSON only: {\"candidates\": [{\"key\": \"snake_case\", "
        "\"canonical_key\": \"snake_case\", \"value\": \"...\", "
        "\"confidence\": 0.0-1.0, \"evidence\": \"user: ...\", "
        "\"signal\": \"explicit_remember|strong_durable_statement|"
        "preference_statement|correction|unknown\"}]}. "
        "If none, {\"candidates\": []}.\n\n"
        f"{payload_text}"
    )
    raw = await _json_completion(
        _PROPOSE_SYSTEM,
        user_content,
        model=settings.deepseek_memory_model,
        usage_out=usage_out,
    )
    data = _safe_parse_json(raw)
    items = data.get("candidates", [])
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for item in items[: settings.memory_gate_max_candidates]:
        if not isinstance(item, dict):
            continue
        norm = _normalize_candidate(item)
        if norm is not None and norm["value"]:
            out.append(norm)
    return out


async def gate_fact_candidates(
    candidates: list[dict[str, Any]],
    payload_text: str,
    *,
    usage_out: ChatCompletionUsage | None = None,
) -> GateResult:
    if not candidates:
        return GateResult(candidates_proposed=0)

    numbered = "\n".join(
        f"[{i}] key={c['key']} value={c['value'][:200]!r} "
        f"confidence={c['confidence']} signal={c['signal']} evidence={c['evidence'][:300]!r}"
        for i, c in enumerate(candidates)
    )
    user_content = (
        "For each candidate, decide verdict remember or ignore and action upsert, "
        "delete, or noop. Output JSON only: {\"decisions\": [{\"key\": \"snake_case\", "
        "\"verdict\": \"remember|ignore\", \"action\": \"upsert|delete|noop\", "
        "\"reason\": \"...\", \"confidence\": 0.0-1.0}]}. "
        "One decision per candidate key.\n\n"
        f"Candidates:\n{numbered}\n\nSources:\n{payload_text}"
    )
    raw = await _json_completion(
        _GATE_SYSTEM,
        user_content,
        model=settings.deepseek_memory_model,
        usage_out=usage_out,
    )
    data = _safe_parse_json(raw)
    decisions_raw = data.get("decisions", [])
    if not isinstance(decisions_raw, list):
        decisions_raw = []

    by_key = {c["key"]: c for c in candidates}
    changes: list[dict[str, Any]] = []
    gate_decisions: list[dict[str, Any]] = []
    ignored = 0

    seen_keys: set[str] = set()
    for dec in decisions_raw:
        if not isinstance(dec, dict):
            continue
        key = normalize_fact_key(str(dec.get("key") or ""))
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        candidate = by_key.get(key)
        if candidate is None:
            continue

        verdict = str(dec.get("verdict", "ignore")).lower()
        action = str(dec.get("action", "noop")).lower()
        reason = str(dec.get("reason") or "")[:500]
        try:
            conf = float(dec.get("confidence", candidate["confidence"]))
        except (TypeError, ValueError):
            conf = candidate["confidence"]

        if _assistant_only_evidence(candidate["evidence"]):
            verdict = "ignore"
            reason = reason or "Assistant-only evidence"
        elif _blocked_key(key, candidate["signal"]):
            verdict = "ignore"
            reason = reason or "Ephemeral or task-scoped key"
        elif verdict == "remember" and action == "upsert":
            if conf < settings.memory_gate_min_confidence:
                verdict = "ignore"
                reason = reason or "Below minimum confidence"
            elif not candidate["value"]:
                verdict = "ignore"
                reason = reason or "Empty value"

        decision_record = {
            "key": key,
            "verdict": verdict,
            "action": action,
            "reason": reason,
        }
        gate_decisions.append(decision_record)

        if verdict != "remember":
            ignored += 1
            continue

        applied = False
        if action == "delete":
            changes.append(
                {
                    "action": "delete",
                    "key": key,
                    "canonical_key": candidate["canonical_key"],
                    "confidence": conf,
                }
            )
            applied = True
        elif action == "upsert" and candidate["value"]:
            changes.append(
                {
                    "action": "upsert",
                    "key": key,
                    "canonical_key": candidate["canonical_key"],
                    "value": candidate["value"],
                    "confidence": conf,
                }
            )
            applied = True

        if not applied:
            ignored += 1
            decision_record["verdict"] = "ignore"
            decision_record["reason"] = reason or "Remember verdict without upsert/delete"

    for c in candidates:
        if c["key"] not in seen_keys:
            ignored += 1
            gate_decisions.append(
                {
                    "key": c["key"],
                    "verdict": "ignore",
                    "action": "noop",
                    "reason": "No gate decision returned",
                }
            )

    return GateResult(
        changes=changes,
        ignored=ignored,
        gate_decisions=gate_decisions,
        candidates_proposed=len(candidates),
    )


async def extract_gated_fact_changes(
    payload_text: str,
    *,
    scope: str = "session",
    usage_out: ChatCompletionUsage | None = None,
) -> GateResult:
    """Run full memory gate pipeline or fall back to legacy reconcile."""
    if not settings.memory_gate_enabled:
        changes = await extract_fact_changes(payload_text, usage_out=usage_out)
        return GateResult(changes=changes, candidates_proposed=len(changes))

    # Session runs often; skip zero-signal batches. Global/offload reconcile broader context.
    if (
        settings.memory_extraction_skip_if_no_signals
        and scope == "session"
        and not has_memory_signals(payload_text)
    ):
        return GateResult(skipped="no_memory_signals")

    candidates = await propose_fact_candidates(payload_text, usage_out=usage_out)
    if not candidates:
        return GateResult(candidates_proposed=0)

    return await gate_fact_candidates(candidates, payload_text, usage_out=usage_out)
