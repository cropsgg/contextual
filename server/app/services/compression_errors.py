"""Structured errors and metrics for Phase 2 context compression."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class CompressionFailureReason(StrEnum):
    MISSING_GEMINI_KEY = "missing_gemini_api_key"
    SUMMARIZE_FAILED = "summarize_failed"
    EMBED_FAILED = "embed_failed"
    NO_PROGRESS = "no_progress"
    STILL_OVER_THRESHOLD = "still_over_threshold"
    QUOTA_EXHAUSTED = "quota_exhausted"


@dataclass(frozen=True)
class CompressionMetrics:
    compression_attempted: bool
    compression_succeeded: bool
    failure_reason: str | None
    rounds: int = 0
    active_token_count: int | None = None
    context_threshold: int | None = None
    projected_offload_count: int = 0

    def log_fields(self) -> dict[str, object]:
        return {
            "compression_attempted": self.compression_attempted,
            "compression_succeeded": self.compression_succeeded,
            "failure_reason": self.failure_reason,
            "compression_rounds": self.rounds,
            "active_token_count": self.active_token_count,
            "context_threshold": self.context_threshold,
        }


class CompressionError(Exception):
    """Compression could not bring active context under the token threshold."""

    def __init__(
        self,
        message: str,
        *,
        reason: CompressionFailureReason,
        metrics: CompressionMetrics,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.metrics = metrics
