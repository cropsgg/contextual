"""In-process embedding cache hit/miss counters (last request + cumulative)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EmbeddingCacheMetrics:
    hits: int = 0
    misses: int = 0
    last_hit: bool | None = None

    def record(self, hit: bool) -> None:
        self.last_hit = hit
        if hit:
            self.hits += 1
        else:
            self.misses += 1

    @property
    def hit_rate(self) -> float | None:
        total = self.hits + self.misses
        if total == 0:
            return None
        return self.hits / total


_metrics = EmbeddingCacheMetrics()


def record_embedding_cache(hit: bool) -> None:
    _metrics.record(hit)


def get_embedding_cache_metrics() -> EmbeddingCacheMetrics:
    return _metrics


def reset_embedding_cache_metrics() -> None:
    global _metrics
    _metrics = EmbeddingCacheMetrics()
