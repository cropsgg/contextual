"""LLM reranking for retrieved memory candidates (Phase 3)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from app.core.config import settings
from app.services.deepseek import ChatCompletionUsage, rerank_memory_candidates
from app.services.memory_retrieval import ScoredMemory

logger = logging.getLogger(__name__)

_rerank_total = 0
_rerank_failed = 0


@dataclass
class RerankResult:
    memories: list[ScoredMemory]
    reranked: bool
    rerank_fallback: bool
    latency_ms: float


async def rerank_memories(
    query: str,
    candidates: list[ScoredMemory],
    *,
    keep: int | None = None,
    usage_out: ChatCompletionUsage | None = None,
) -> RerankResult:
    global _rerank_total, _rerank_failed
    final_k = keep if keep is not None else settings.retrieval_final_k
    t0 = time.perf_counter()

    if not candidates:
        return RerankResult(
            memories=[],
            reranked=False,
            rerank_fallback=False,
            latency_ms=0.0,
        )

    if len(candidates) <= final_k:
        latency = (time.perf_counter() - t0) * 1000
        return RerankResult(
            memories=candidates,
            reranked=False,
            rerank_fallback=False,
            latency_ms=latency,
        )

    _rerank_total += 1
    try:
        indices = await rerank_memory_candidates(
            query, candidates, keep=final_k, usage_out=usage_out
        )
        picked: list[ScoredMemory] = []
        seen: set[int] = set()
        for i in indices:
            if i in seen or not (0 <= i < len(candidates)):
                continue
            seen.add(i)
            picked.append(candidates[i])
        latency = (time.perf_counter() - t0) * 1000
        if picked:
            logger.info(
                "memory_rerank",
                extra={
                    "latency_ms": round(latency, 2),
                    "candidate_count": len(candidates),
                    "final_k": final_k,
                    "succeeded": True,
                    "fallback": False,
                },
            )
            return RerankResult(
                memories=picked[:final_k],
                reranked=True,
                rerank_fallback=False,
                latency_ms=latency,
            )
    except Exception:
        _rerank_failed += 1
        latency = (time.perf_counter() - t0) * 1000
        logger.exception(
            "memory_rerank",
            extra={
                "latency_ms": round(latency, 2),
                "candidate_count": len(candidates),
                "final_k": final_k,
                "succeeded": False,
                "fallback": True,
            },
        )

    latency = (time.perf_counter() - t0) * 1000
    return RerankResult(
        memories=candidates[:final_k],
        reranked=False,
        rerank_fallback=True,
        latency_ms=latency,
    )
