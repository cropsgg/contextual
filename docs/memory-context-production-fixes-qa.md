# Memory & Context Production Fixes — QA Report

Date: 2026-05-21

## Environment

- Postgres :5433 with `vector` extension and `maestro_test` database
- API :8000 health OK (`{"status":"ok","database":"connected"}`)
- UI :3000 with rebuilt image; `BACKEND_URL=http://backend:8000` in container env
- GEMINI/DEEPSEEK keys: loaded in Docker API (chat/compression paths unchanged from prior QA)

## Results

| Test | Pass/Fail | Notes |
|------|-----------|-------|
| A Chat SSE | Pass (prior QA) | Not re-run end-to-end this pass; backend rebuilt with fix |
| B Retrieval headers | Pass (prior QA) | `X-Retrieval-Mode: full` when keys present |
| C1 Facts after "remember" | Pass (prior QA) | Explicit remember + cadence unchanged |
| C2 Facts casual 2 msgs | Pass | Still `[]` at cadence (expected) |
| C3 Casual 8 msgs | Expected change | Session extraction no longer skipped by default; gate LLM decides |
| D Cross-session recall | Partial | Compression now triggers on transcript tokens; memory episodes creatable |
| E Selective attribution | Pass | `AttributionOut` now includes `active_turns_*` and `packer` |
| F Proxy build | **Pass** | `GET http://localhost:3000/health` → proxied JSON; `POST /api/auth/login` → **401** (not 503) |
| G pytest | **Pass** | **27/27** in Docker on `maestro_test` |

## Evidence

### F — Docker proxy (after `BACKEND_URL` + frontend rebuild)

```text
$ curl -sS http://localhost:3000/health
{"status":"ok","database":"connected"}

$ curl -sS -w '\nHTTP:%{http_code}\n' -X POST http://localhost:3000/api/auth/login \
  -H 'Content-Type: application/json' -d '{"email":"x@y.com","password":"wrong"}'
{"detail":"Incorrect email or password"}
HTTP:401
```

### G — pytest (isolated DB, Docker)

```text
docker compose run --rm backend pytest \
  tests/test_memory_gate.py tests/test_context_packer.py tests/test_context_compression.py -q
...........................                                              [100%]
27 passed in 37.86s
```

Database: `postgresql+psycopg2://contextual_app:maestro_dev@postgres:5432/maestro_test`

### Context monitor API — transcript vs packed tokens

```json
{
  "active_token_count": 2,
  "packed_token_count": 23,
  "context_threshold": 4000,
  ...
}
```

### Compression threshold fix (unit test)

`test_compression_uses_transcript_not_packed_tokens` verifies selective packing keeps `packed < transcript` while `reduce_until_under` still compresses when transcript exceeds threshold.

### Memory gate default

`memory_extraction_skip_if_no_signals` default is now `False`; `test_extract_gated_session_runs_on_cadence_without_signal_words` confirms session scope calls `propose_fact_candidates` for small talk.

## Root cause (addressed)

- **Compression never ran:** threshold used packed prompt size; selective context kept packed count low. Fixed: `count_active_transcript_tokens()` drives compression; packed count used for quota/debug only.
- **Casual facts skipped:** session gate skipped batches without signal words. Fixed: default `MEMORY_EXTRACTION_SKIP_IF_NO_SIGNALS=false`.
- **Docker UI 503:** frontend had no runtime `BACKEND_URL`. Fixed in `docker-compose.yml`.
- **pytest hung on shared DB:** tests migrated `maestro` while API held connections. Fixed: `maestro_test` + admin URL pointed at test DB in `conftest.py`.

## Notes

- Host-side `pytest` may hang on psycopg2 connect in this environment; use Docker command above for CI/local verification.
- Existing Postgres volumes need `maestro_test` created once (init script `02-test-db.sql` or conftest fallback).
