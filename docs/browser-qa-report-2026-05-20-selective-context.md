# Browser QA Report — Selective Context & Full Stack

**Date:** 2026-05-20  
**Environment:** Docker Compose (`contextual-maestro-db`, `contextual-maestro-api`, `contextual-maestro-web`)  
**UI:** `http://localhost:3000/?debug=1`  
**API:** `http://localhost:8000`  
**Test account:** `qa_selective_1779305295@example.com` / `TestPass123!`  
**Admin:** `admin@example.com` / `TestAdmin123!` (from project docs)

## Executive summary

| Area | Verdict |
|------|---------|
| Auth, sessions, settings, chat SSE | **PASS** |
| Memory / fact extraction | **PASS** |
| Selective context (server-side packing) | **PASS** (DB + packer metadata) |
| Selective context UI (“Selected from history”) | **FAIL** (API contract gap) |
| Compression (Phase 2) in long thread | **PARTIAL** (not triggered under selective packing) |
| Docker `DEEPSEEK_API_KEY` wiring | **FAIL** (fixed during QA via `source server/.env`) |

**Health score (critical paths):** 8/10 — core chat and memory work; Phase 5 attribution UI blocked by schema; compression interaction with selective packing needs design review.

---

## Environment notes

1. **Initial blocker:** `docker compose` set `DEEPSEEK_API_KEY: ${DEEPSEEK_API_KEY:-}` from the host shell, which was empty even though `server/.env` had a key. Container showed `DEEPSEEK len 0` → chat would 503/fail. **Fix applied:** `set -a && source server/.env && docker compose up -d backend --force-recreate`.
2. **API restart invalidates JWT:** After recreate, browser showed “Invalid or expired token” until re-login. Expected.
3. **Keys at test time:** `DEEPSEEK` set (len 35), `GEMINI` set (len 17), `selective_context_enabled=true`, `prompt_token_budget=8000`.

---

## Pre-flight

| ID | Result | Evidence |
|----|--------|----------|
| PF-1 | **PASS** | `GET /health` → `{"status":"ok","database":"connected"}` |
| PF-2 | **PASS** | UI loads login/register at `/?debug=1` |
| PF-3 | **PASS** | No console errors observed during flows |
| PF-4 | **PASS** | Register `qa_selective_1779305295@example.com` → chat UI |
| PF-5 | **PASS** | Admin login `admin@example.com` → 200 (API) |
| PF-6 | **PASS** | `GET /api/admin/stats` returns user/token aggregates |
| PF-7 | **PASS** | User B (`qa_userb_1779306000@example.com`) has 0 sessions; no User A leakage |

---

## Authentication (A)

| ID | Status | Notes |
|----|--------|-------|
| A-2 Register | **PASS** | Auto-login; sidebar shows email |
| A-5 Login | **PASS** | After logout, sign-in restores chat |
| A-7 Logout | **PASS** | Confirm dialog → auth panel |
| A-8 Quota | **PASS** | Header: “V4 Flash · 1.0M left today” |

---

## Chat & SSE (B, C)

| ID | Status | Notes |
|----|--------|-------|
| B-2 Send | **PASS** | User bubble + streaming assistant |
| B-3 Streaming | **PASS** | “Stop generating” while in flight |
| C-1–C-3 | **PASS** | API driver: `token` events + `done` with `assistant_message_id`, `tier: primary`, `model: deepseek-v4-flash` |

**API stress session:** `54e32ab1-6f98-4070-838c-11f043ad1113` — 24 turns, 48 episodes, all `embed_status=ready`.

---

## Session management (D)

| ID | Status | Notes |
|----|--------|-------|
| D-1 New chat | **PASS** | Empty state “How can I help you today?” |
| D-2 Switch | **PASS** | Long API session appears in sidebar; messages load |
| D-5 Sidebar | **PASS** | Context monitor visible with `?debug=1` |

---

## Settings — Memory (G)

| ID | Status | Notes |
|----|--------|-------|
| G-1 | **PASS** | `/settings` — Memory tab, facts list |
| G-2–G-5 | **PASS** | 5 auto-extracted facts after Orion thread: `name: Alex`, `project: Project Orion`, plus telemetry/reconciliation/Jira keys |
| G-6 | **PARTIAL** | Episodic section: “Stored memories (0)” — no compression summaries yet |
| G-8 | Not run | Clear-all not exercised (avoid destructive test on shared DB) |

---

## Message attribution & Phase 5 (I, J)

### Sources (facts / memories) — **PASS**

- Multiple assistant replies show **“Sources used for this reply”**.
- Expanded panel lists **Profile facts** (project, name, Orion telemetry facts).
- `GET /api/history/attribution` returns 5 facts with `selection_reason: similarity`.

### “Selected from history” — **FAIL**

**Expected:** UI section “Selected from history” with scored episode ids + floor ids (`MessageAttribution.tsx`).

**Actual:** Section never appears. API response omits Phase 5 fields.

**Root cause:** `build_attribution_metadata()` writes `active_turns_selected`, `active_turns_floor`, and `packer` into `episodes.metadata_json`, but `GET /api/history/attribution` returns `AttributionOut` with only `facts`, `memories`, and `retrieval` (`server/app/schemas.py`, `server/app/api/history.py`).

**DB proof (assistant message 4482):**

```json
"packer": {"budget": 8000, "tokens_used": 2080, "evictions": 0, "selective_context_enabled": true}
"active_turns_selected": 16 items
"active_turns_floor": 6 items
```

**Fix:** Extend `AttributionOut` + `get_attribution()` to pass through Phase 5 fields (UI already typed).

---

## Selective context (server) — **PASS**

| Check | Result |
|-------|--------|
| Config | `selective_context_enabled=true`, budget 8000 |
| Embeddings | 48/48 messages `embed_status=ready` (Gemini) |
| Packer on late turns | `tokens_used` 2080–2952; 16–21 retrieved turns + floor 6 |
| Kill switch | Not re-tested in browser (`SELECTIVE_CONTEXT_ENABLED=false`) — covered by unit test |

---

## Compression (Phase 2) — **PARTIAL**

| Check | Result |
|-------|--------|
| Context monitor | Always “No compression yet”, offloaded msgs **0** |
| `compression_started` SSE | Not seen in 24-turn API run |
| DB | `is_offloaded=0` for all 48 messages in stress session |

**Likely cause:** `count_active_prompt_tokens()` calls `build_completion_messages()` **with selective packing enabled**, so the counted size stays small (~258 in `GET /context`) even when raw history is large. `reduce_until_under()` compares packed size to `CONTEXT_THRESHOLD_TOKENS` (4000) → threshold never exceeded → compression never runs.

**Implication:** Phase 5 may unintentionally disable Phase 2 compression for long threads. Recommend counting **raw active transcript tokens** (or pre-pack size) for compression decisions, while still **sending** the packed prompt to the LLM.

---

## Developer tools (K)

| ID | Status | Notes |
|----|--------|-------|
| K-1 | **PASS** | Context panel: Active tokens, Offloaded msgs, Memory chunks |
| K-2 | **PARTIAL** | Active tokens showed ~74–258 during browser session; does not reflect packer `tokens_used` (~2–3k) on same session |
| K-3 | **BLOCKED** | No offloaded archive (compression did not run) |

---

## Admin (F)

| ID | Status | Notes |
|----|--------|-------|
| F-1 | **PASS** (API) | User token cannot access admin routes |
| F-2 | **PASS** (API) | `/api/admin/stats` returns JSON |

Admin UI not fully walked in browser this run; API contract verified.

---

## Logs

- Routine: `GET /health`, CORS `OPTIONS`, auth/history 200.
- **No** `compression_started`, embed failures, or stack traces during stress run.
- Passlib/bcrypt warning on migrate (non-fatal): `AttributeError: module 'bcrypt' has no attribute '__about__'`.

---

## Automated tests (reference)

- Docker-mounted pytest: **7 passed** (`test_context_packer`, `test_turn_embedding`, `test_active_turn_retrieval`) in ~6 min.
- API driver script: `server/scripts/qa_browser_api_driver.py` (24-turn Orion scenario).

---

## Recommended follow-ups

1. **P0:** Extend `AttributionOut` + `get_attribution` for `active_turns_selected`, `active_turns_floor`, `packer`.
2. **P1:** Separate compression threshold input from selective-packed prompt size.
3. **P2:** Document in README/docker-compose that `DEEPSEEK_API_KEY` must be exported or use `env_file` without empty compose overrides.
4. **P3:** Re-run compression test with `SELECTIVE_CONTEXT_ENABLED=false` or fixed threshold logic.

---

## Test artifacts

- Long session id: `54e32ab1-6f98-4070-838c-11f043ad1113`
- Driver log: `/tmp/qa_driver.log` (local)
- Driver script: `server/scripts/qa_browser_api_driver.py`
