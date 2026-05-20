# Caching operations (Postgres)

## Tables

| Cache | Key shape | Default TTL |
|-------|-----------|-------------|
| `embedding_cache` | `sha256(normalized_text):model:dimensions` | 7 days |
| `retrieval_bundle_cache` | `retrieval:{user}:{session}:{query_hash}:{version_token}` | 60s |
| `prompt_assembly_cache` | `prompt:{user}:{session}:{counts}:{facts_version}:…` | 45s |

## Version token (retrieval bundle)

Invalidates when any of these change:

- Latest memory episode id in session
- Active message count
- Max `updated_at` of active user facts
- Offloaded message count

## Invalidation matrix

Call `invalidate_user_caches(user_id, session_id?)` after:

- New user or assistant message committed
- New memory episode / compression
- Fact create, update, delete, or extraction apply
- Memory admin clear

Embedding cache is content-addressed; no user invalidation needed.

## Streaming safety

- **Safe**: pre-stream `build_completion_messages` when fingerprint unchanged
- **Unsafe**: caching mid-SSE or across turns without fingerprint bump

`PROMPT_ASSEMBLY_CACHE_ENABLED` defaults to `false` in production.

## Metrics (context status)

- `embedding_cache_hit_rate` — cumulative session hit rate
- `retrieval_bundle_cache_hit` — last request hit/miss boolean
