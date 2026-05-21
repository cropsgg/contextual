# Agent handoff: Contextual Maestro — local memory & retrieval QA

**Purpose:** Onboard a fresh agent session to this repo, run the stack locally, and **prove or disprove** that persistent memory (facts, cross-session retrieval, compression, selective context) works end-to-end.  
**Assumption from owner:** `GEMINI_API_KEY` and `DEEPSEEK_API_KEY` are valid (same keys in prod and dev). Focus on **behavior and wiring**, not key validity.

**Do not** start by reading `node_modules/`, `.next/`, `server/.venv/`, or broad repo trees. Scope to `server/` and `client/` as needed.

---

## 1. Product & architecture (60-second model)

**Contextual Maestro** is a chat app where “memory” is server-side engineering, not model magic:

| Layer | What it does | When it runs |
|-------|----------------|--------------|
| **Chat** | SSE `POST /api/chat` → DeepSeek stream | Every user message |
| **Compression (Phase 2)** | Summarize old turns → `episode_kind=memory` | Active prompt > `CONTEXT_THRESHOLD_TOKENS` (default 4000) |
| **Cross-session retrieval (Phase 3)** | Embed query (Gemini), vector search other sessions’ memory episodes, optional rerank | Every chat turn (if `GEMINI_API_KEY` set) |
| **User facts (Phase 3)** | Extract durable facts → `user_facts` table | Every 4 msgs/session + every 8 msgs/user + after compression |
| **Selective context (Phase 5)** | Pack active tail by hybrid score under `PROMPT_TOKEN_BUDGET` | Every turn if `SELECTIVE_CONTEXT_ENABLED=true` |

**Prompt assembly order** (`context_manager.py` → `build_completion_messages`):

1. System + `<user_profile>` (injected facts)
2. `<relevant_past_context>` (cross-session memories)
3. `<in_session_memory>` (older same-session chunks)
4. Latest compressed summary block
5. Active messages (floor + retrieved turns)

If facts/memories are empty, the model still gets explicit system text like *“No stored user facts selected for this reply”* — it may **disclaim** persistent memory even when the product supports it.

**Stack:** `client/` Next.js 15 + Bun (:3000), `server/` FastAPI (:8000), Postgres + pgvector on host **:5433** (Docker).

---

## 2. Production context (already shipped — do not re-debug unless regressing)

Railway deployment issues were addressed on `main` (recent commits):

| Issue | Fix |
|-------|-----|
| API 502 | `server/Dockerfile` binds uvicorn to `${PORT:-8000}` |
| CORS / network error | UI uses same-origin `/api/*`; API `CORS_ORIGINS` or `FRONTEND_PUBLIC_URL` |
| Build-time proxy to localhost | Runtime proxy: `client/lib/backendProxy.ts`, `client/app/api/[...path]/route.ts`, `client/app/health/route.ts` |
| UI env | Railway UI: `BACKEND_URL=<api public url>`; remove `NEXT_PUBLIC_API_URL` pointing at API |

**Production URLs (owner):**

- UI: `https://contextual-production-a977.up.railway.app`
- API: `https://glistening-determination-production-f71b.up.railway.app`

**Owner symptom on prod:** Chat works (200 OK logs) but model says it has no persistent memory across **new sessions** when user did not say “remember” / “my name is”. That matches **skipped session fact extraction** + **no compressed memory** on short threads — not necessarily broken API keys.

---

## 3. Codebase map — read in this order

| Order | Path | Why |
|-------|------|-----|
| 1 | `README.md` | Quick start, env tables |
| 2 | `server/app/api/chat.py` | Chat SSE, headers, background jobs |
| 3 | `server/app/services/context_manager.py` | Retrieval, compression, prompt blocks |
| 4 | `server/app/services/fact_extraction_v2.py` | When facts are extracted |
| 5 | `server/app/services/memory_gate.py` | `no_memory_signals` skip for session scope |
| 6 | `server/app/services/fact_injection.py` | What gets into `<user_profile>` |
| 7 | `server/app/services/memory_retrieval.py` | Vector search on memory episodes |
| 8 | `client/lib/api.ts` | `API_URL` dev vs prod |
| 9 | `client/lib/backendProxy.ts` | Railway runtime proxy |
| 10 | `docs/selective-context-ops.md` | Phase 5 knobs |
| 11 | `docs/fact-extraction-ops.md` | Fact scopes & triggers |
| 12 | `docs/retrieval-ops.md` | pgvector ANN |

**Tests to run after changes:**

```sh
cd server && pytest tests/test_turn_embedding.py tests/test_active_turn_retrieval.py \
  tests/test_context_packer.py tests/test_chat_stream_selective.py \
  tests/test_memory_gate.py tests/test_chat_stream.py -q
```

---

## 4. Local environment setup (agent must execute)

### 4.1 Postgres

```sh
cd /path/to/contextual
docker compose up -d postgres
```

Verify: port **5433** listening; DB name **`maestro`** per `server/.env.example`.

### 4.2 Backend

```sh
cd server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

**Required in `server/.env`:**

```dotenv
DATABASE_URL=postgresql+psycopg2://contextual_app:maestro_dev@127.0.0.1:5433/maestro
ADMIN_DATABASE_URL=postgresql+psycopg2://maestro:maestro_dev@127.0.0.1:5433/maestro
JWT_SECRET=<any long random string>
DEEPSEEK_API_KEY=<owner key>
GEMINI_API_KEY=<owner key>
CORS_ORIGINS=http://localhost:3000
```

Optional admin seed:

```dotenv
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=<password>
```

Start API:

```sh
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

**Smoke:**

```sh
curl -sS http://localhost:8000/health
# Expect: {"status":"ok","database":"connected"}
```

On first start, logs should show schema migration / `CREATE EXTENSION vector` (no fatal errors).

### 4.3 Frontend (dev mode — direct API, not Railway proxy)

```sh
cd client
cp .env.example .env.local
```

**`client/.env.local`:**

```dotenv
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_DEV_TOOLS=1
```

```sh
bun install
bun run dev
```

Open: `http://localhost:3000/?debug=1` (enables Context monitor + Sources on replies).

### 4.4 Optional: test Railway-style proxy locally

To mimic production UI (same-origin `/api` → backend):

```dotenv
# client/.env.local — do NOT set NEXT_PUBLIC_API_URL for this test
BACKEND_URL=http://localhost:8000
```

Restart `bun run dev`. Browser calls `/api/...` on :3000; route handlers proxy to :8000.

---

## 5. Verification matrix (execute all; record pass/fail)

### A. Auth & chat baseline

| Step | Command / action | Pass criteria |
|------|------------------|---------------|
| A1 | Register user via UI or `POST /api/auth/register` | 200, JWT returned |
| A2 | `GET /api/auth/me` with Bearer | 200, user object |
| A3 | `POST /api/chat` with `{session_id, message}` (SSE) | Stream `token` events + `done` |
| A4 | `GET /api/history/messages?session_id=...` | User + assistant rows persisted |

**SSE curl pattern** (replace token/session):

```sh
TOKEN="..."
SESSION="$(uuidgen | tr '[:upper:]' '[:lower:]')"
curl -N -X POST http://localhost:8000/api/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"session_id\":\"$SESSION\",\"message\":\"Hello\"}"
```

### B. Retrieval headers (per chat request)

On every `POST /api/chat` response, inspect headers:

| Header | Healthy | Broken |
|--------|---------|--------|
| `X-Retrieval-Mode` | `full` or `degraded_keyword` | `unavailable_no_key` → config not loading `GEMINI_API_KEY` |
| `X-Cross-Session-Memory` | `available` or `degraded` | `unavailable` |

In browser: Network → `chat` → Response Headers.  
With curl: `curl -D - -o /dev/null -N ...` and read headers before SSE body.

**If `unavailable_no_key` locally:** `settings.gemini_api_key` empty — fix `.env` load path (cwd must be `server/` for uvicorn) or variable name typo.

### C. User facts pipeline

**Design rules (critical for owner’s prod tests):**

- Session extraction: every **`FACT_EXTRACTION_SESSION_EVERY_N`** (default **4**) user messages **in that session**.
- Global extraction: every **`FACT_EXTRACTION_GLOBAL_EVERY_N`** (default **8**) user messages **account-wide**.
- Session scope with `MEMORY_EXTRACTION_SKIP_IF_NO_SIGNALS=true` (default): skips unless text matches `remember`, `my name is`, `call me`, etc. (`memory_gate.py`).

**Test C1 — explicit remember (should always eventually store facts):**

1. One session only.
2. Send 4 user messages; include: `My name is TestUser. Remember that for future chats.`
3. Wait 15–30s (background `maybe_schedule_extractions` after SSE `done`).
4. `GET /api/memory/facts`:

```sh
curl -sS -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/memory/facts | jq .
```

**Pass:** At least one active fact (e.g. `name`, `preferred_name`, or similar).

**Test C2 — casual messages across sessions (reproduces owner prod behavior):**

1. New session A: 2 generic messages (no “remember”).
2. New session B: `What is my name?`
3. `GET /api/memory/facts`

**Expected fail (by design):** `[]` or no name fact — session extraction skipped, global may not have run yet.

**Test C3 — global extraction at message 8:**

1. Same account, one session, 8 short user messages without magic phrases.
2. Wait 30s.
3. `GET /api/memory/facts` — may still be `[]` if gate/LLM finds nothing worth storing (not a key failure).

**Context status:**

```sh
curl -sS -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/history/context?session_id=$SESSION" | jq .
```

| Field | Meaning |
|-------|---------|
| `last_fact_extraction_at` | null → extraction never succeeded |
| `fact_extraction_last_error` | non-null → check logs |
| `retrieval_mode` | last chat retrieval |
| `retrieval_failure_reason` | e.g. `missing_gemini_api_key` |
| `memory_chunk_count` | 0 until compression runs |
| `memory_paused` | true → compression failing |

### D. Cross-session memory (vector path)

**Prerequisites:** `GEMINI_API_KEY` set; memory episodes with embeddings in **other** sessions.

**Test D1 — compression creates memory:**

1. Lower threshold temporarily for test (optional): `CONTEXT_THRESHOLD_TOKENS=500` in `server/.env`, restart API.
2. One session: send many/long messages until context monitor shows offload/summary OR `memory_chunk_count > 0`.
3. SQL check:

```sql
SELECT id, session_id, episode_kind, embed_status,
       left(content, 80) AS snippet
FROM episodes
WHERE user_id = <uid> AND episode_kind = 'memory'
ORDER BY created_at DESC LIMIT 5;
```

4. Confirm `embed_status` becomes `ready` (background `embed_new_turns` / memory embed).

**Test D2 — recall in new session:**

1. Session 1: establish fact or compressed memory (per C1 or D1).
2. Session 2: ask something that references session 1 (e.g. `What is my name?` or topic from long chat).
3. Check assistant `metadata_json.attribution` (with `?debug=1`) or response headers:
   - `attribution.facts` non-empty and/or `attribution.memories` with `scope: cross_session`.
4. Model answer should use injected context (not “I have no persistent memory”).

### E. Selective active-turn retrieval (Phase 5)

| Step | Action | Pass |
|------|--------|------|
| E1 | `SELECTIVE_CONTEXT_ENABLED=true` (default) | — |
| E2 | Long session, varied topics | Assistant metadata has `attribution.active_turns_selected` / `active_turns_floor` / `packer` |
| E3 | `pytest tests/test_context_packer.py tests/test_active_turn_retrieval.py -q` | All pass |

### F. Frontend proxy (Railway parity)

With `BACKEND_URL=http://localhost:8000` and **no** `NEXT_PUBLIC_API_URL` in production build simulation:

```sh
cd client && NODE_ENV=production BACKEND_URL=http://localhost:8000 bun run build && \
  PORT=3001 bun run start -- -p 3001
```

| Step | Pass |
|------|------|
| `curl -sS http://localhost:3001/health` | Proxied API health JSON |
| `curl -sS -X POST http://localhost:3001/api/auth/login ...` | 200 or 401, not 503 “BACKEND_URL not set” |

### G. Automated test suite

```sh
cd server && pytest -q
```

Record count failures. Phase 5 subset:

```sh
pytest tests/test_turn_embedding.py tests/test_active_turn_retrieval.py \
  tests/test_context_packer.py tests/test_chat_stream_selective.py -q
```

---

## 6. Database diagnostics (psql)

Connect: `postgresql://maestro:maestro_dev@127.0.0.1:5433/maestro`

```sql
-- Extension
SELECT * FROM pg_extension WHERE extname = 'vector';

-- User message count (global extraction cadence)
SELECT id, email, user_message_count, last_fact_extraction_at,
       fact_extraction_last_error, fact_extraction_consecutive_failures
FROM users ORDER BY id DESC LIMIT 5;

-- Facts
SELECT fact_key, fact_value, status, confidence, pinned, updated_at
FROM user_facts WHERE user_id = <uid> AND status = 'active';

-- Memory episodes + embeddings
SELECT episode_kind, COUNT(*),
       COUNT(*) FILTER (WHERE embedding IS NOT NULL) AS with_embedding,
       COUNT(*) FILTER (WHERE embed_status = 'ready') AS ready
FROM episodes WHERE user_id = <uid> GROUP BY episode_kind;

-- Recent extraction runs
SELECT id, scope, status, last_error, result_json, created_at
FROM fact_extraction_runs WHERE user_id = <uid>
ORDER BY id DESC LIMIT 10;
```

---

## 7. Logging: what Railway access logs cannot show

Uvicorn lines like `POST /api/chat 200` **do not** prove memory worked. Success paths are silent except:

- `GEMINI_API_KEY missing; cross-session memory unavailable` (warning)
- `context_compression outcome` (info, on compression)
- `Background fact extraction failed` (exception)
- `Query embed failed` (warning)

**Agent should** grep API process logs while running Test C1/D2:

```sh
# In server terminal or docker logs
# Look for: fact extraction, embed, compression, GEMINI_API_KEY missing
```

Optional improvement (only if owner asks): add one INFO line per chat with `retrieval_mode`, `facts_injected`, `extraction_result`.

---

## 8. Decision tree (report template)

Fill this for the owner after local QA:

```
## Local memory QA report

### Environment
- [ ] Postgres :5433 + vector extension
- [ ] API :8000 health OK
- [ ] UI :3000 with ?debug=1
- GEMINI_API_KEY loaded: YES/NO (from X-Retrieval-Mode, not guess)
- DEEPSEEK_API_KEY loaded: YES/NO (chat works)

### Results
| Test | Pass/Fail | Notes |
|------|-----------|-------|
| A Chat SSE | | |
| B Retrieval headers | | X-Retrieval-Mode= |
| C1 Facts after "remember" | | fact keys: |
| C2 Facts casual multi-session | | expected fail? |
| D Cross-session recall | | |
| E Selective attribution | | |
| F Proxy build | | |
| G pytest | | x/y passed |

### Root cause (if prod-like failure reproduced locally)
- [ ] Facts never extracted (skip signals / message count / extraction errors)
- [ ] Facts exist but not injected (similarity threshold / query mismatch)
- [ ] No memory episodes (threshold not reached)
- [ ] Embeddings pending/failed (embed_status)
- [ ] Retrieval unavailable_no_key (env not loaded in process)
- [ ] Product working as designed; UX/model disclaimer confusing

### Recommended next code/config changes
- ...
```

---

## 9. Agent constraints

- **Do not** commit unless owner asks.
- **Do not** read `server/.venv`, `client/node_modules`, `client/.next`.
- Prefer **targeted** `Grep` / single-file `Read` over full-tree listing.
- Run **one** of API or UI dev server if machine is slow; Postgres can stay up.
- Owner believes API keys work — if tests fail locally with same keys, investigate **env loading**, **skip logic**, **message thresholds**, and **empty stores**, not “get new keys.”

---

## 10. Copy-paste prompt for the new chat

```
You are taking over Contextual Maestro memory/retrieval QA.

Read and follow: docs/agent-handoff-local-memory-qa.md

Mission:
1. Understand the memory pipeline (facts, compression, cross-session retrieval, selective context).
2. Run Postgres + API + UI locally with owner's DEEPSEEK_API_KEY and GEMINI_API_KEY in server/.env.
3. Execute verification matrix sections A–G and SQL checks in section 6.
4. Fill section 8 report template with evidence (curl output, headers, /api/memory/facts, context status, pytest).
5. If memory fails locally, find root cause in code/config (especially memory_gate skip, extraction cadence, GEMINI loaded in Settings).
6. Do not assume prod Railway logs prove memory; prove via facts API + retrieval headers + attribution.

Production URLs for reference only:
- UI: https://contextual-production-a977.up.railway.app
- API: https://glistening-determination-production-f71b.up.railway.app

Start by reading the handoff doc, then docker compose up -d postgres, then server + client per section 4.
```

---

*Handoff version: 2026-05-20 — branch `main`, selective context + Railway proxy work in flight.*
