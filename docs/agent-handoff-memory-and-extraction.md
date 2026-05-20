# Agent handoff — Memory, Gemini, and fact extraction

**Date:** 2026-05-19  
**Repo:** `/Users/crops/Desktop/contextual` (The Contextual Maestro)  
**Audience:** Implementing agent (fixes + verification)  
**Context:** Follow-up to browser QA (`docs/browser-qa-handoff.md`) and Gemini re-test (`docs/browser-qa-report-2026-05-19-gemini-retest.md`). User enabled Gemini API; retrieval path was re-verified on 2026-05-19.

---

## Executive summary

| Area | Status |
|------|--------|
| Gemini embeddings + retrieval (`full` mode) | **Working** |
| Cross-session recall when facts exist in DB | **Working** |
| Sources UI + Settings memory CRUD display | **Working** |
| Debug context panel (`?debug=1`) | **Working** |
| Auth `/api/auth/me`, chat SSE, quota, admin | **Working** (from prior QA) |
| **Auto fact extraction (chat → DB facts)** | **Broken** — `ObjectDeletedError` in background job |
| **Full J-2 E2E (chat-only teach → new session recall)** | **Blocked** on extraction fix |

**Primary task for implementing agent:** Fix background fact extraction in `server/app/services/fact_extraction_v2.py`, then re-run the verification script in §7.

---

## Environment (how to run)

### Stack

| Service | How | URL |
|---------|-----|-----|
| Postgres | `docker compose up -d postgres` | host `:5433` |
| API | `docker compose up -d --build backend` | `http://localhost:8000` |
| Frontend | `cd client && bun run dev` | `http://localhost:3000` |

### Critical operational notes

1. **`GEMINI_API_KEY` changes require container recreate** — `get_settings()` is `@lru_cache` in `server/app/core/config.py`. After editing `server/.env`:
   ```bash
   docker compose up -d --build --force-recreate backend
   ```
2. **Do not paste `server/.env` into chat** — verify via container only, e.g.:
   ```bash
   docker exec contextual-maestro-api python3 -c "
   import asyncio
   from app.services.gemini_embeddings import embed_text
   async def main():
       v = await embed_text('hello')
       print('dims', len(v))
   asyncio.run(main())
   "
   ```
   Expected: `dims 768`.
3. **Local `uvicorn --reload` was unreliable** in QA (slow/hung startup). Prefer Docker API for testing.
4. Registration emails must use a domain with a **dot** (e.g. `@example.com`). `@test.local` returns **422**.

### Required env (names only)

- `DEEPSEEK_API_KEY` — chat completion
- `GEMINI_API_KEY` — embeddings (must have Generative Language API enabled; blocked keys return `403 API_KEY_SERVICE_BLOCKED`)

---

## Test accounts

| Role | Email | Password |
|------|-------|----------|
| Admin | `admin@example.com` | `TestAdmin123!` |
| User A (QA) | `qa_a_1779201033@example.com` | `TestPass123!` |
| User B (QA) | `qa_b_1779201033@example.com` | `TestPass123!` |

Admin user id in DB (when using seed data): **972**.

---

## What is working (verified 2026-05-19)

### Gemini / retrieval

- Embeddings succeed (`gemini-embedding-001`, 768 dimensions).
- Chat response headers (when facts exist):
  - `x-retrieval-mode: full`
  - `x-cross-session-memory: available`
- Attribution API returns `mode: "full"` and lists profile facts (not `unavailable_embed`).

### Cross-session memory (J-2) — partial E2E

- **With facts in DB:** New session asks “What is my secret codeword?” → assistant replies **`PLUMBER42`** (correct; uses stored `secret_codeword`).
- **Without new facts from chat:** Teaching a new codeword in chat only (e.g. `GIRAFFE88`) does **not** update facts — see § “What is not working”.

### UI

| ID | Feature | Verification |
|----|---------|----------------|
| I-1 / I-2 | Sources button + expand | After reply, click “Sources used for this reply” → **Profile facts** list (e.g. `secret_codeword: PLUMBER42`, `favorite_color: blue`). Screenshot: `docs/qa-screenshots/J-3-sources-fixed.png` |
| G | Settings → Memory | `/settings` → “What I know about you” lists facts; Edit/Delete; “Clear episodic memory” |
| K | Debug monitor | `http://localhost:3000/?debug=1` → Offloaded msgs, Memory chunks, History |

### Prior QA (still assumed good; not re-tested in last pass)

Auth register/login/logout, `/api/auth/me` (quota object), chat SSE streaming, session sidebar, quota exhaustion UI (429), admin console, two-user isolation. See `docs/browser-qa-report-2026-05-19.md` § “Features working correctly”.

### Code fixes already landed (this effort)

1. **`server/app/services/fact_extraction_v2.py`** — Added missing `_reclaim_stale_running_runs()` (was `NameError` in logs).
2. **`server/app/services/gemini_embeddings.py`** — Prefer `x-goog-api-key` header; clearer logging on `API_KEY_SERVICE_BLOCKED`.
3. **`server/app/api/auth.py`** — `/me` builds `UserOut` before `db.commit()` (fixes prior `ObjectDeletedError` on `/me`).

---

## What is not working

### 1. J-4 — Automatic fact extraction (PRIMARY BUG)

**Symptom:** Facts stated in chat are not written to `user_facts` / Settings unless created via `POST /api/memory/facts`. Background job logs:

```
Background fact extraction failed
  File "/app/app/api/chat.py", line 70, in _maybe_extract_facts
    await maybe_schedule_extractions(...)
  File "/app/app/services/fact_extraction_v2.py", line 283, in maybe_schedule_extractions
    await run_session_extraction(db, user_id, session_id)
  File "/app/app/services/fact_extraction_v2.py", line 220, in run_session_extraction
    ok = await process_extraction_run(db, run.id)
sqlalchemy.orm.exc.ObjectDeletedError: Instance '<FactExtractionRun ...>' has been deleted, or its row is otherwise not present.
```

**Evidence:**

- After chat: `SELECT ... FROM fact_extraction_runs ORDER BY id DESC` shows admin run **id=47**, `status=pending`, `scope=session`, never completes.
- API test: teach `GIRAFFE88` in session 1 → wait 25s → `GET /api/memory/facts` still only `PLUMBER42` / `blue`.

**Likely cause:** In `run_session_extraction`, `run_global_extraction`, and `run_offload_extraction`, the pattern is:

```python
run = enqueue_extraction_run(...)
db.commit()                    # expires ORM instance
ok = await process_extraction_run(db, run.id)  # accessing run.id may reload deleted/expired row
return run.id if ok else None    # same issue on return
```

`process_extraction_run` also calls `db.commit()` early (marks `running`), then on failure `db.rollback()` + `db.expire_all()` — any stale `run` reference in the caller is unsafe.

**Suggested fix (minimal):**

```python
run = enqueue_extraction_run(...)
run_id = run.id
db.commit()
ok = await process_extraction_run(db, run_id)
return run_id if ok else None
```

Apply the same pattern to `run_global_extraction` and `run_offload_extraction`. In `process_pending_extraction_runs`, use `run_id = run.id` before awaiting `process_extraction_run`.

**Related files:**

- `server/app/services/fact_extraction_v2.py` — queue + `process_extraction_run`
- `server/app/api/chat.py` — `_maybe_extract_facts` (background task)
- `server/app/services/fact_extraction_apply.py`, `fact_extraction_sources.py`, `deepseek.py` (`extract_fact_changes`)

### 2. J-2 — Full end-to-end (blocked on J-4)

**Handoff intent (from `docs/browser-qa-handoff.md`):**

1. Session 1: User teaches a unique fact in chat (e.g. codeword).
2. Wait for async extraction (not instant).
3. Session 2: Ask about that fact → assistant should use memory; Sources should cite it.

**Current state:** Step 3 **passes** if step 2 used manual API or pre-seeded facts. Step 2 **fails** for chat-only teach because extraction never succeeds.

### 3. Extraction scheduling cadence (product / test awareness)

From `server/app/core/config.py`:

- `fact_extraction_session_every_n: int = 4` — session extraction runs when `session_message_count % 4 == 0`
- `fact_extraction_global_every_n: int = 8`

A **single** “remember this” message may not enqueue session extraction unless the counter aligns. For QA, either send 4 user messages in a session or temporarily lower `FACT_EXTRACTION_SESSION_EVERY_N=1` in env.

### 4. J-1 — Context compression (not verified)

Debug panel shows “No compression yet” / 0 memory chunks for tested sessions. Long-context compression was **BLOCKED** in original QA — not re-tested after Gemini fix.

### 5. Original QA items still open (unchanged)

Not in scope of memory fix unless implementing agent has bandwidth:

| IDs | Feature |
|-----|---------|
| B-4 | Stop generation |
| D-3, D-4 | Rename / delete session |
| H-* | Expert preview flows |
| Many BLOCKED items | See `docs/browser-qa-report-2026-05-19.md` |

---

## Suggested implementation order

1. **P0** — Fix `ObjectDeletedError` in `fact_extraction_v2.py` (all `run.id` after `commit` paths).
2. **P0** — Rebuild/restart API container; confirm pending runs process:
   ```bash
   docker logs contextual-maestro-api 2>&1 | grep -i "fact extraction"
   docker exec contextual-maestro-db psql -U maestro -d maestro -c \
     "SELECT id, status, left(last_error,80), scope FROM fact_extraction_runs ORDER BY id DESC LIMIT 5;"
   ```
3. **P1** — Run §7 verification script (J-4 + J-2 E2E).
4. **P2** — Add regression test: enqueue extraction → commit → `process_extraction_run` without `ObjectDeletedError` (see `server/tests/` patterns in `test_chat_stream.py`).
5. **P2** — Consider extraction on first high-signal message (optional product change).

---

## Verification script (copy-paste for implementing agent)

Run from repo root with API on `:8000`:

```bash
python3 << 'PY'
import json, time, uuid, urllib.request, http.client
from urllib.parse import urlparse

BASE = "http://localhost:8000"
CODE = "GIRAFFE88"  # change each run to avoid collision

def req(method, path, token=None, data=None):
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode() if data else None
    r = urllib.request.Request(BASE + path, data=body, headers=h, method=method)
    with urllib.request.urlopen(r, timeout=120) as resp:
        return resp.status, dict(resp.headers), resp.read()

def login(email, password):
    _, _, raw = req("POST", "/api/auth/login", data={"email": email, "password": password})
    return json.loads(raw)["access_token"]

def create_session(token):
    _, _, raw = req("POST", "/api/history/sessions", token=token)
    return json.loads(raw)["session_id"]

def chat(token, session_id, message):
    u = urlparse(BASE)
    conn = http.client.HTTPConnection(u.hostname, u.port, timeout=120)
    conn.request("POST", "/api/chat", json.dumps({"message": message, "session_id": session_id}),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    resp = conn.getresponse()
    hdrs = dict(resp.getheaders())
    text = resp.read().decode()
    conn.close()
    tokens = []
    for block in text.split("\n\n"):
        ev, data = None, None
        for line in block.split("\n"):
            if line.startswith("event:"): ev = line[6:].strip()
            elif line.startswith("data:"): data = json.loads(line[5:])
        if ev == "token" and data:
            tokens.append(data.get("content", data.get("text", "")))
    return resp.status, hdrs, "".join(tokens)

token = login("admin@example.com", "TestAdmin123!")
# Send 4 messages so session_message_count % 4 == 0 (or set FACT_EXTRACTION_SESSION_EVERY_N=1)
s1 = create_session(token)
for i in range(3):
    chat(token, s1, f"Message {i+1}: please acknowledge with OK.")
chat(token, s1, f"Remember exactly: my secret codeword is {CODE}. Reply with only OK.")
print("waiting 30s for extraction...")
time.sleep(30)
_, _, facts_raw = req("GET", "/api/memory/facts", token=token)
facts = json.loads(facts_raw)
print("facts:", {f["fact_key"]: f["fact_value"] for f in facts})
assert any(CODE in str(f["fact_value"]) for f in facts), "J-4 FAIL: codeword not extracted"

s2 = create_session(token)
_, hdrs2, reply2 = chat(token, s2, "What is my secret codeword? Reply with only the codeword.")
print("reply2:", reply2.strip(), "retrieval:", hdrs2.get("x-retrieval-mode"))
assert CODE in reply2.upper(), f"J-2 FAIL: expected {CODE}, got {reply2!r}"
print("J-4 + J-2 PASS")
PY
```

**Pass criteria:**

- `GIRAFFE88` (or chosen code) appears in `/api/memory/facts` without manual POST.
- New session recall returns that codeword.
- No `ObjectDeletedError` in `docker logs contextual-maestro-api` during extraction.

---

## Key file map

| Path | Role |
|------|------|
| `server/app/services/gemini_embeddings.py` | Embed API client |
| `server/app/services/context_manager.py` | `build_enhanced_context`, compression |
| `server/app/services/fact_extraction_v2.py` | Extraction queue + **bug here** |
| `server/app/api/chat.py` | SSE chat + `_maybe_extract_facts` |
| `server/app/api/auth.py` | Login, `/me` |
| `server/app/api/memory.py` | Facts CRUD |
| `server/app/core/config.py` | `fact_extraction_*` settings |
| `client/` | Next.js UI (Sources, Settings, debug panel) |
| `docker-compose.yml` | Postgres + API services |

---

## Prior reports and screenshots

| Artifact | Purpose |
|----------|---------|
| `docs/browser-qa-handoff.md` | Full 94-item checklist |
| `docs/browser-qa-report-2026-05-19.md` | First full QA run |
| `docs/browser-qa-report-2026-05-19-gemini-retest.md` | Gemini 403 era (superseded for embed) |
| `docs/qa-screenshots/J-2-cross-session.png` | Wrong answer when embed broken |
| `docs/qa-screenshots/J-3-sources-fixed.png` | Sources with profile facts (fixed) |
| `docs/qa-screenshots/E-4-quota-exhausted.png` | Quota UI |

---

## Handoff prompt (paste to implementing agent)

```
You are fixing The Contextual Maestro memory pipeline in /Users/crops/Desktop/contextual.

Read docs/agent-handoff-memory-and-extraction.md first.

Working: Gemini embeddings, retrieval mode "full", cross-session recall when facts exist,
Sources UI, Settings memory list, debug panel, auth/chat from prior QA.

Broken: Background fact extraction crashes with sqlalchemy.orm.exc.ObjectDeletedError
in fact_extraction_v2.py after db.commit() when accessing run.id — fix by capturing
run_id before commit in run_session_extraction / run_global_extraction / run_offload_extraction.

After fix: recreate backend container, run the verification script in that doc (§7),
confirm fact_extraction_runs reach status=succeeded and J-2 E2E passes chat-only.

Do not commit server/.env. Use docker compose for API. Bun for client.
```

---

*End of handoff.*
