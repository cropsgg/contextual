# Fact extraction operations

## Scopes

| Scope | Trigger | Sources |
|-------|---------|---------|
| `session` | Every `FACT_EXTRACTION_SESSION_EVERY_N` messages in a session | Active messages in session, latest session memory summary, existing facts |
| `global` | Every `FACT_EXTRACTION_GLOBAL_EVERY_N` user messages | Active messages (lookback), latest memory per session (capped), existing facts |
| `offload` | After successful context compression | Offloaded message batch, new memory summary, existing facts |

## LLM actions

The extractor returns `changes[]` with `upsert`, `delete`, or `noop`:

- **noop** — value unchanged; no DB write
- **upsert** — create or update active fact (`canonical_key`, optional `aliases`)
- **delete** — soft-deprecate when contradicted (confidence ≥ `FACT_DELETE_MIN_CONFIDENCE`)

Pinned facts are never deleted or overwritten.

## Dedup

1. **Aliases** — LLM `canonical_key` + `aliases`; server merges alias rows into canonical
2. **Embedding merge** — post-pass cosine similarity ≥ `FACT_DEDUP_SIMILARITY` merges near-duplicates

## Caps and lifecycle

- `FACT_MAX_PER_USER` — lowest-confidence non-pinned facts deprecated when over cap
- `status`: `active` | `deprecated` | `deleted` (injection uses `active` only)

## Reliability

Runs are stored in `fact_extraction_runs` with exponential backoff. After `FACT_EXTRACTION_MAX_CONSECUTIVE_FAILURES` user-level failures, new auto-runs pause until the next successful extraction.

Chat is never blocked by extraction failures.
