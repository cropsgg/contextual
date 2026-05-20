# Memory retrieval operations

## ANN index (pgvector)

Cross-session and in-session memory search use cosine distance on `episodes.embedding` where `episode_kind = 'memory'`.

### HNSW (default)

Created automatically by `migrate_schema()` when `MEMORY_ANN_INDEX=hnsw` (default):

- Index: `idx_episodes_memory_embedding_hnsw`
- Parameters: `m=16`, `ef_construction=64` (env: `HNSW_M`, `HNSW_EF_CONSTRUCTION`)
- Partial predicate: `episode_kind = 'memory' AND embedding IS NOT NULL`

**Tradeoffs:** Best steady-state latency/recall without a training step. Higher build cost and disk than IVFFlat.

**Query tuning:** At query time PostgreSQL/pgvector may use `hnsw.ef_search` (session GUC) to balance recall vs latency.

### IVFFlat (alternative)

Set `MEMORY_ANN_INDEX=ivfflat`. Index `idx_episodes_memory_embedding_ivfflat` with `lists` from `IVFFLAT_LISTS` (default 100; rule of thumb: `sqrt(row_count)`).

After bulk loads, run `REINDEX INDEX idx_episodes_memory_embedding_ivfflat`.

**Tradeoffs:** Faster builds on large tables; recall depends on `ivfflat.probes` (try 10–20).

### Disable ANN (dev)

`MEMORY_ANN_INDEX=none` skips index creation; queries use sequential scan (fine for small datasets).

## Verify index usage

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, session_id, content, embedding <=> '[...]'::vector AS distance
FROM episodes
WHERE user_id = 1
  AND episode_kind = 'memory'
  AND embedding IS NOT NULL
ORDER BY distance
LIMIT 5;
```

Expect an index scan on `idx_episodes_memory_embedding_hnsw` when the table is large enough.

Integration test: set `RUN_PGVECTOR_PERF=1` and run `pytest tests/test_memory_ann_index.py`.

## Keyword fallback (FTS)

When query embedding fails, search uses `idx_episodes_memory_fts` (GIN on `to_tsvector('english', coalesce(summary, content))`).

## Rerank observability

DeepSeek rerank logs structured `memory_rerank` events with `latency_ms`, `succeeded`, and `fallback`.

Rerank-only failure keeps vector ordering and sets `rerank_fallback=true` in attribution; it does **not** mark retrieval as fully degraded.

Full degradation (`retrieval_degraded`) applies when semantic search is unavailable and keyword fallback cannot run or finds nothing.
