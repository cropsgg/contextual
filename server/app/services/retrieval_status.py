"""Retrieval outcome types and assembled prompt context."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.services.fact_injection import InjectedFact
from app.services.memory_retrieval import ScoredMemory


class RetrievalMode(str, Enum):
    FULL = "full"
    DEGRADED_KEYWORD = "degraded_keyword"
    UNAVAILABLE_NO_KEY = "unavailable_no_key"
    UNAVAILABLE_EMBED = "unavailable_embed"
    UNAVAILABLE_SEARCH = "unavailable_search"


@dataclass
class RetrievalOutcome:
    mode: RetrievalMode
    cross_session_memory_available: bool
    embed_succeeded: bool
    keyword_fallback_used: bool
    reranked: bool
    rerank_fallback: bool
    embed_latency_ms: float | None = None
    search_latency_ms: float | None = None
    rerank_latency_ms: float | None = None
    failure_reason: str | None = None
    suppressed_fact_count: int = 0

    @property
    def retrieval_degraded(self) -> bool:
        return self.mode in (
            RetrievalMode.DEGRADED_KEYWORD,
            RetrievalMode.UNAVAILABLE_NO_KEY,
            RetrievalMode.UNAVAILABLE_EMBED,
            RetrievalMode.UNAVAILABLE_SEARCH,
        )

    def to_attribution_dict(self) -> dict:
        return {
            "mode": self.mode.value,
            "cross_session_memory_available": self.cross_session_memory_available,
            "reranked": self.reranked,
            "rerank_fallback": self.rerank_fallback,
            "keyword_fallback_used": self.keyword_fallback_used,
            "failure_reason": self.failure_reason,
        }

    def cross_session_header_value(self) -> str:
        if self.mode == RetrievalMode.FULL and self.cross_session_memory_available:
            return "available"
        if self.mode == RetrievalMode.DEGRADED_KEYWORD:
            return "degraded"
        return "unavailable"


@dataclass
class AssembledContext:
    injected_facts: list[InjectedFact]
    cross_session_memories: list[ScoredMemory]
    in_session_memories: list[ScoredMemory]
    retrieval: RetrievalOutcome

    @property
    def memories(self) -> list[ScoredMemory]:
        """All retrieved memories (cross-session + in-session) for backward compat."""
        return self.cross_session_memories + self.in_session_memories


# Alias for transitional imports
EnhancedContext = AssembledContext
