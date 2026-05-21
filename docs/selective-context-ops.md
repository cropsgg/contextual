# Selective context retrieval (Phase 5)

Query-aware packing for the **active** conversation tail. Older turns stay in Postgres; only a budgeted subset is sent to the LLM each request.

## How it differs from compression

| Layer | When | What |
|-------|------|------|
| **Compression** (Phase 2) | Active prompt > `CONTEXT_THRESHOLD_TOKENS` | Summarise oldest offload-eligible messages into a `memory` episode |
| **Selective retrieval** (Phase 5) | Every chat turn (when enabled) | Pick relevant **non-offloaded** turns + always keep last N verbatim |

Both compose: compression shrinks history; selective retrieval avoids shipping the entire active tail on every turn.

## Prompt tiers (8K default budget)

1. System prompt + `<user_profile>` + cross-session blocks (never dropped)
2. Latest compressed summary (`memory` episode)
3. Always-floor: last `ACTIVE_RETRIEVAL_FLOOR_TURNS` messages (default 6)
4. Retrieved active turns (hybrid score, MMR, neighbour expansion)
5. Eviction order when over budget: drop lowest-scored retrieved turns → trim summary → truncate oldest floor turn (floor never removed)

## Scoring (no LLM rerank)

```
score = 0.55 * vector + 0.20 * bm25 + 0.15 * recency + 0.10 * entity_overlap
```

- **Vector:** cosine similarity on per-turn (or chunk) embeddings (`gemini-embedding-001`)
- **BM25:** Postgres `ts_rank` on active messages/chunks
- **Recency:** `exp(-age_turns / RECENCY_HALF_LIFE_TURNS)`
- **Entity overlap:** token overlap on words length ≥ 3

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `SELECTIVE_CONTEXT_ENABLED` | `true` | Kill switch; `false` restores pre-Phase-5 packing |
| `PROMPT_TOKEN_BUDGET` | `8000` | Hard cap on assembled prompt tokens |
| `ACTIVE_RETRIEVAL_FLOOR_TURNS` | `6` | Always-included recent turns |
| `ACTIVE_RETRIEVAL_TOP_K` | `8` | Max turns before neighbour expansion |
| `ACTIVE_TURN_CHUNK_THRESHOLD_TOKENS` | `1000` | Split longer messages into chunks |
| `ACTIVE_TURN_CHUNK_SIZE_TOKENS` | `500` | Chunk size |
| `ACTIVE_TURN_CHUNK_OVERLAP_TOKENS` | `50` | Chunk overlap |
| `SCORING_WEIGHT_VECTOR` | `0.55` | Hybrid weight |
| `SCORING_WEIGHT_BM25` | `0.20` | Hybrid weight |
| `SCORING_WEIGHT_RECENCY` | `0.15` | Hybrid weight |
| `SCORING_WEIGHT_ENTITY` | `0.10` | Hybrid weight |
| `RECENCY_HALF_LIFE_TURNS` | `20` | Recency decay |
| `MMR_LAMBDA` | `0.7` | MMR relevance vs diversity |

## Embeddings

- Short messages: embed the `message` row directly (`embed_status=ready`).
- Long messages: parent row has no embedding; child `message_chunk` rows are embedded.
- Background job runs after each chat turn (`embed_new_turns`).
- Requires `GEMINI_API_KEY` for vector leg; without it, BM25 + recency still run.

## Attribution

Assistant `metadata_json.attribution` includes:

- `active_turns_selected` — episode ids + scores
- `active_turns_floor` — always-included ids
- `packer` — `budget`, `tokens_used`, `evictions`

UI (engineering only): `?debug=1` or `NEXT_PUBLIC_DEV_TOOLS=1` → **Sources** on replies, including **Selected from history**. Normal users see a plain chat with no attribution UI.

## Troubleshooting

| Symptom | Likely cause | Action |
|---------|----------------|--------|
| No “Selected from history” | Cold start, embeddings `pending` | Wait for background embed; check `GEMINI_API_KEY` |
| `active_retrieval_degraded` in attribution | Query embed failed | BM25+recency only; fix Gemini |
| Prompt still large | Huge messages in floor | Lower floor or raise budget; long turns are chunked but floor is verbatim |
| `embed_status=pending` stuck | API errors | Logs for `embed_new_turns`; restart API after key fix |
| Wrong recall | Weights / top_k | Tune `SCORING_WEIGHT_*`, `ACTIVE_RETRIEVAL_TOP_K` |

## Tests

```sh
cd server && pytest tests/test_turn_embedding.py tests/test_active_turn_retrieval.py \
  tests/test_context_packer.py tests/test_chat_stream_selective.py -q
```

## Railway Postgres

- Use a **pgvector** template (plain Railway Postgres lacks `vector`).
- API `ADMIN_DATABASE_URL` / `DATABASE_URL`: `postgresql+psycopg2://...` with the **private** host (`*.railway.internal`), not `https://...` or `DATABASE_PUBLIC_URL` on the API service.
- Database path: `/railway` is fine — grants use the DB name from `ADMIN_DATABASE_URL` (no separate `maestro` database required).
- Enable extension once in Query: `CREATE EXTENSION IF NOT EXISTS vector;`
- Tables are created by the API on startup, not manually in the Data UI.

## Railway UI + API

- **API service** (`glistening-determination`): `$PORT` bind; DB URLs, `JWT_SECRET`, keys. CORS must allow the UI origin:
  - `CORS_ORIGINS=https://contextual-production-a977.up.railway.app` **or**
  - `FRONTEND_PUBLIC_URL=https://contextual-production-a977.up.railway.app`
  - Without this, proxied `OPTIONS /api/auth/*` returns **400 Disallowed CORS origin**.
- **UI service** (`contextual`): runtime **`BACKEND_URL`** = API public URL (required; e.g. `https://glistening-determination-production-f71b.up.railway.app`). Proxied via route handlers at request time (not build-time rewrites). Production builds ignore `NEXT_PUBLIC_API_URL` and call same-origin `/api/*`.
- Remove `NEXT_PUBLIC_API_URL` from the UI service if set to the API host (causes cross-origin calls and CORS failures).
- Auth routes are **POST** only: `/api/auth/register`, `/api/auth/login` (GET returns 405).
