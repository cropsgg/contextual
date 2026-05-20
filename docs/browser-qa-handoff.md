# Browser QA Handoff — The Contextual Maestro

**Purpose:** End-to-end browser validation of every user-facing feature. Produce a structured report classifying each feature as **PASS**, **FAIL**, **PARTIAL**, or **BLOCKED**, with evidence (screenshots, console errors, network notes, repro steps).

**Product:** Full-stack AI chat (“Assistant”) with long-term memory, context compression, token quotas (primary + fallback tiers), admin console, and optional expert prompt preview.

---

## 1. Agent mission

You are a **browser QA agent**. Your job is to:

1. Stand up or confirm access to a running instance (UI + API + Postgres).
2. Execute **every checklist item** below in order (or in logical dependency order).
3. Capture **evidence** for failures and notable passes.
4. Deliver a **final report** using the template in Section 10.

Do **not** skip items because they “probably work.” Do **not** treat API-only behavior as PASS unless the UI reflects it correctly.

**Classification rules:**

| Status | Meaning |
|--------|---------|
| **PASS** | Behavior matches expected result; no console errors; UI/API aligned |
| **PARTIAL** | Core works but UX wrong, missing feedback, flaky, or minor contract drift |
| **FAIL** | Broken, wrong data, crash, 5xx, or wrong authorization |
| **BLOCKED** | Could not test (missing API key, env down, no second user, etc.) — explain why |

---

## 2. Environment prerequisites

### 2.1 URLs

| Service | Default URL |
|---------|-------------|
| Frontend | `http://localhost:3000` |
| Backend API | `http://localhost:8000` |
| Health check | `http://localhost:8000/health` |

### 2.2 Start stack (choose one)

**Option A — Docker Compose (recommended for QA):**

```sh
cd /path/to/contextual
cp server/.env.example server/.env
# Set: JWT_SECRET, DEEPSEEK_API_KEY, GEMINI_API_KEY (for memory/embeddings)
# Set: ADMIN_EMAIL, ADMIN_PASSWORD for seeded admin
docker compose up --build
```

**Option B — Local dev:**

```sh
docker compose up -d postgres
cd server && source .venv/bin/activate && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
cd client && bun install && bun run dev
```

### 2.3 Required secrets (blocking if missing)

| Variable | Location | Blocks |
|----------|----------|--------|
| `DEEPSEEK_API_KEY` | `server/.env` | Chat streaming, preview, summarization |
| `GEMINI_API_KEY` | `server/.env` | Embeddings / cross-session memory (degraded without it) |
| `JWT_SECRET` | `server/.env` | Auth |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | `server/.env` | Admin account (restart API after change) |

Frontend must call API at `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`). CORS: `CORS_ORIGINS` must include the UI origin.

### 2.4 Browser tooling

- Use a real browser automation tool (e.g. Cursor `cursor-ide-browser` MCP, Playwright, or GStack browse).
- **Before interactions:** `browser_navigate` → `browser_lock` → `browser_snapshot`.
- After navigation/clicks that change DOM: fresh `browser_snapshot`.
- Capture **screenshots** on FAIL/PARTIAL and for quota/admin tables.
- Open DevTools **Console** and **Network** where possible; note 4xx/5xx and SSE streams.

### 2.5 Optional dev mode

- `NEXT_PUBLIC_DEV_TOOLS=1` in `client/.env.local`, **or** open app with `?debug=1`.
- Enables **Context monitor** in sidebar and session UUID in header (when quota bar not shown).

---

## 3. Test accounts (create before functional tests)

| Account | How to obtain | Role | Purpose |
|---------|---------------|------|---------|
| **Admin** | Seeded from `ADMIN_EMAIL` / `ADMIN_PASSWORD` in `server/.env` (restart API) | `admin` | Admin console, unlimited tokens, expert preview |
| **User A** | Register via UI (unique email, password ≥ 8 chars) | `user` | Primary quota, memory, chat |
| **User B** | Second registration (different email) | `user` | RLS isolation spot-check (sessions/memory not visible across users) |

**Suggested credentials pattern (document what you actually use):**

- Admin: `admin@example.com` / (from `.env`)
- User A: `usera+<timestamp>@test.local` / `TestPass123!`
- User B: `userb+<timestamp>@test.local` / `TestPass123!`

**Logout between account switches** (sidebar Log out) or use separate browser profiles/incognito windows.

---

## 4. Application map

### 4.1 Routes (frontend)

| Route | Auth | Description |
|-------|------|-------------|
| `/` | Required | Main chat (`ChatShell`) |
| `/settings` | Required | Memory management + Expert preview (if enabled) |
| `/settings?tab=expert` | Expert only | Expert preview tab |
| `/admin` | Admin only | User list, stats, quota overrides |

Unauthenticated visitors to `/`, `/settings`, `/admin` see the **login/register** panel.

### 4.2 Main UI areas (chat `/`)

- **Auth panel:** Login / Register tabs, email + password, validation errors
- **Sidebar:** Email, Admin link (admin only), Settings, Logout, New chat, session history (title + preview), rename/delete on hover
- **Header:** Sidebar toggle, “Chat” title, **quota bar** (non-admin) or session id (dev tools)
- **Message area:** User (right) / assistant (left) bubbles, streaming ellipsis, **Sources** on assistant messages (when attribution exists)
- **Composer:** Message input, Send, **Stop** while streaming, **Preview before send** (expert only)
- **Error banner:** Red strip for stream/API errors (including quota)

### 4.3 API surface (for network verification)

| Method | Path | Auth |
|--------|------|------|
| GET | `/health` | No |
| POST | `/api/auth/register` | No |
| POST | `/api/auth/login` | No |
| GET | `/api/auth/me` | Bearer |
| POST | `/api/chat` | Bearer — **SSE** |
| POST | `/api/chat/preview` | Bearer — expert/admin |
| POST | `/api/history/sessions` | Bearer |
| GET | `/api/history/sessions` | Bearer |
| PATCH | `/api/history/sessions/{id}` | Bearer |
| DELETE | `/api/history/sessions/{id}` | Bearer |
| GET | `/api/history/messages?session_id=` | Bearer |
| GET | `/api/history/context?session_id=` | Bearer |
| GET | `/api/history/attribution?session_id=&message_id=` | Bearer |
| GET/PATCH/DELETE | `/api/memory/facts` … | Bearer |
| GET/DELETE | `/api/memory/episodes` … | Bearer |
| POST | `/api/memory/clear` | Bearer — body `{ "confirm": "DELETE_ALL_MEMORY" }` |
| GET | `/api/admin/users` | Admin |
| GET | `/api/admin/stats` | Admin |
| PATCH | `/api/admin/users/{id}` | Admin |

---

## 5. Pre-flight checklist (do first)

| ID | Step | Expected | Status |
|----|------|----------|--------|
| PF-1 | GET `http://localhost:8000/health` | 200 JSON healthy | |
| PF-2 | Open `http://localhost:3000` | Login/register UI loads, no blank page | |
| PF-3 | Console on load | No uncaught errors | |
| PF-4 | Register User A | Success → lands on chat UI | |
| PF-5 | Logout → Login Admin | Success → chat UI, **Admin** link visible | |
| PF-6 | Open `/admin` as Admin | Admin console loads with stats + user table | |
| PF-7 | Open `/admin` as User A (or logged out) | Redirect to `/` or no admin data | |

---

## 6. Master feature checklist

For each item record: **ID**, **Status**, **Steps taken**, **Expected**, **Actual**, **Evidence** (screenshot path / network status / console error).

---

### A. Authentication

| ID | Feature | Steps | Expected result |
|----|---------|-------|-----------------|
| A-1 | Register — validation | Register tab, password &lt; 8 chars | HTML validation or error; no account created |
| A-2 | Register — success | Valid email + password ≥ 8 | Auto-login, chat UI, sidebar shows email |
| A-3 | Register — duplicate email | Same email again | Clear error (409 or message), stay on auth panel |
| A-4 | Login — wrong password | Wrong password | Error message, no token |
| A-5 | Login — success | Correct credentials | Chat UI, session list loads |
| A-6 | Session persistence | Login, refresh page (F5) | Still logged in |
| A-7 | Logout | Click Log out | Auth panel; `/api/auth/me` without token fails |
| A-8 | `/api/auth/me` quota shape | As User A, after login check header quota text OR DevTools → Network → `me` response | `quota` object with limits, used today, `tier_in_use`, `resets_at` |
| A-9 | Protected routes | Logged out: visit `/settings`, `/admin` | Auth panel or redirect, no data leak |

---

### B. Chat — core UX

| ID | Feature | Steps | Expected result |
|----|---------|-------|-----------------|
| B-1 | Empty state | New user / new chat | “How can I help you today?” |
| B-2 | Send message | Type short message → Send | User bubble appears; assistant streams tokens; final text non-empty |
| B-3 | Streaming UI | During stream | Assistant shows `…` or growing text; Send disabled; **Stop** visible |
| B-4 | Stop generation | Start long reply → Stop | Stream aborts; partial assistant bubble **removed** (not left empty/garbage) |
| B-5 | Session title | First message in new chat | Sidebar session title updates from “New conversation” (may be async) |
| B-6 | Session preview | After exchange | Sidebar shows preview text or timestamp |
| B-7 | Quota bar update | After successful `done` event | Header quota text updates (e.g. “V4 Flash · X left today”) unless admin unlimited |
| B-8 | Error — no API key | If `DEEPSEEK_API_KEY` empty: send message | User-visible error (503 or banner), no silent hang |
| B-9 | Long message | Paste ~100 chars | Sends and streams (or validation if over `MESSAGE_MAX_LENGTH` ~8000) |

---

### C. Chat — SSE / network contract

Use Network tab: `POST /api/chat` → `text/event-stream`.

| ID | Feature | Steps | Expected result |
|----|---------|-------|-----------------|
| C-1 | SSE content type | Send message | Response `text/event-stream` |
| C-2 | `token` events | During stream | Multiple `event: token` with JSON `{ "text": "..." }` |
| C-3 | `done` event | After stream | `event: done` with `assistant_message_id`, `session_id`, optional `model`, `tier`, `quota` |
| C-4 | UI reflects `done` | After stream | Assistant message gets stable id; Sources may appear |
| C-5 | `compression_started` | Long context / many messages (optional) | May see `compression_started` event; chat still completes |
| C-6 | `error` event handling | Force error if possible (e.g. invalid session) | Red banner; partial assistant removed |

---

### D. Session management (sidebar)

| ID | Feature | Steps | Expected result |
|----|---------|-------|-----------------|
| D-1 | New chat | Click “New chat” | Empty messages; new session in list; new session selected |
| D-2 | Switch session | Create 2 chats with different content; click other session | Correct messages load |
| D-3 | Rename | Hover session → ✎ → new title | Title updates in list |
| D-4 | Delete | Hover → × → confirm | Session removed; if active, new chat started; alert mentions episodes deleted |
| D-5 | Sidebar toggle | Hide/show sidebar (desktop) | Layout adjusts; history accessible when open |
| D-6 | Mobile sidebar | Narrow viewport (&lt;768px) | Sidebar overlay; selecting session closes sidebar |

---

### E. Token quotas (regular user — User A)

**Setup tip:** As Admin, edit User A: set **Primary daily limit** to `500` and **Fallback daily limit** to `500` for faster exhaustion tests. Log back in as User A.

| ID | Feature | Steps | Expected result |
|----|---------|-------|-----------------|
| E-1 | Initial quota display | Login as User A | Header shows primary tier remaining (not “Unlimited”) |
| E-2 | Usage decreases | Send 2–3 chat messages | Remaining count decreases (may need refresh if only SSE updates — verify `done.quota` updates bar) |
| E-3 | Primary exhaustion → fallback | Lower primary to ~100, send until primary exhausted | Bar may show **V3.2 tier** / fallback remaining; chats still work on fallback model |
| E-4 | Full exhaustion — HTTP 429 | Set both limits very low; exhaust both | Send fails with visible “quota exhausted” / daily limit message |
| E-5 | 429 detail | Inspect network response body | `detail.error` = `quota_exceeded`, includes `resets_at`, limits |
| E-6 | Admin unlimited | Login as Admin | Header: “Unlimited tokens (admin)” or no restrictive bar |
| E-7 | `token_unlimited` flag | Admin: enable unlimited for User A, save | User A chats without 429 regardless of counters |
| E-8 | UTC reset display | When blocked, read quota message | Mentions reset time (UTC midnight / formatted `resets_at`) |

---

### F. Admin console (`/admin`)

**Login as Admin only.**

| ID | Feature | Steps | Expected result |
|----|---------|-------|-----------------|
| F-1 | Access control | User A navigates to `/admin` | Redirect to `/` |
| F-2 | Stats cards | Load `/admin` | Four cards: Users, Tokens today, Lifetime primary, Lifetime fallback — numeric, formatted (K/M) |
| F-3 | User table | Scroll table | Columns: Email, Role, Primary today, Fallback today, Lifetime, Limits, Actions |
| F-4 | Reflects usage | After User A chatted | User A row shows non-zero “today” usage |
| F-5 | Edit modal | Edit User A | Fields: primary limit, fallback limit, unlimited checkbox, expert preview checkbox |
| F-6 | Admin row — unlimited disabled | Edit admin user | “Unlimited tokens (admin role)” checkbox **disabled** |
| F-7 | Save valid limits | Set limits → Save | Modal closes; table updates |
| F-8 | Save invalid limits | Clear limit field → Save | Error: “Daily limits must be valid numbers” (client-side) |
| F-9 | Expert preview toggle | Enable expert for User A → Save | User A sees Expert tab + preview button after re-login |
| F-10 | Back to chat | “Back to chat” link | Returns to `/` |

---

### G. Settings — Memory (`/settings`)

**Login as User A.**

| ID | Feature | Steps | Expected result |
|----|---------|-------|-----------------|
| G-1 | Navigation | Sidebar Settings → `/settings` | Memory tab default; “Chat” back link works |
| G-2 | Facts — list | Open Memory tab | Facts list loads (may be empty) |
| G-3 | Facts — add | Add fact (key + value) | Appears in list |
| G-4 | Facts — edit | Edit fact value | Persists after refresh |
| G-5 | Facts — delete | Delete fact | Removed from list |
| G-6 | Episodes — list | Scroll to episodic section | Episodes load (after enough chat + compression); pagination info if many |
| G-7 | Episodes — delete | Delete one episode | Confirm → removed |
| G-8 | Clear all memory | Clear → confirm | Facts + episodes cleared; **chat messages on `/` unchanged** |
| G-9 | Unauthorized | Logout mid-page | Auth panel |

---

### H. Expert preview

**Requires:** User with `expert_preview_enabled` OR admin.

| ID | Feature | Steps | Expected result |
|----|---------|-------|-----------------|
| H-1 | Tab visibility | User A without flag | Only Memory tab on `/settings` |
| H-2 | Tab visibility | User A with flag (admin enabled) | “Expert preview” tab visible |
| H-3 | Chat — preview button | On `/`, type message → “Preview before send” | Modal opens; loading then prompt breakdown |
| H-4 | Preview — no persist | Preview then close without send | No new user/assistant messages in thread |
| H-5 | Preview — send from modal | Preview → Send from modal | Message sends; stream works |
| H-6 | Settings expert flow | `/settings?tab=expert` → preview → Send anyway | Redirects to `/`; pending message sends (sessionStorage handoff) |
| H-7 | Forbidden user | Disable expert for User A | `POST /api/chat/preview` → 403; button hidden |

---

### I. Message attribution (“Sources”)

| ID | Feature | Steps | Expected result |
|----|---------|-------|-----------------|
| I-1 | Sources button | After reply with memory/facts (multi-turn + profile facts help) | “Sources” or brain icon under assistant message |
| I-2 | Expand sources | Click Sources | Facts and/or memories listed; retrieval mode notes if degraded |
| I-3 | No sources | Simple reply with no injection | No Sources control OR empty state handled gracefully |
| I-4 | Wrong message id | N/A — UI only | No crash when expanding |

---

### J. Context engineering (background — observable)

These run server-side; validate **indirectly** via UI/dev tools.

| ID | Feature | Steps | Expected result |
|----|---------|-------|-----------------|
| J-1 | Compression | 15+ turn conversation OR lower `CONTEXT_THRESHOLD_TOKENS` in env | Context monitor (debug) shows offload count / last summary; chat still works |
| J-2 | Cross-session memory | User A: chat in Session 1 about unique fact; new Session 2: ask about it | Assistant may use past context; Sources may cite memory |
| J-3 | Retrieval degraded | Without `GEMINI_API_KEY` | Chat works; Sources/attribution may show degraded/unavailable (not silent wrong facts) |
| J-4 | Fact extraction | Several messages | New facts may appear in Settings (async, not instant) |

---

### K. Developer tools (`?debug=1` or `NEXT_PUBLIC_DEV_TOOLS=1`)

| ID | Feature | Steps | Expected result |
|----|---------|-------|-----------------|
| K-1 | Context monitor visible | Enable debug, open `/` | Monitor panel in sidebar |
| K-2 | Context metrics | Send messages | Token count, threshold, offload count update |
| K-3 | Offloaded archive | Expand archive in monitor | Lists offloaded message snippets |
| K-4 | Session UUID | Header (when no quota bar) | Shows session id |

---

### L. Security & isolation

| ID | Feature | Steps | Expected result |
|----|---------|-------|-----------------|
| L-1 | User B cannot see User A data | User A creates session + message; logout; User B login | User B history does not include User A’s sessions |
| L-2 | API without token | `curl` GET `/api/history/sessions` | 401 |
| L-3 | User A → admin API | Bearer User A token → GET `/api/admin/users` | 403 |
| L-4 | CORS | UI from localhost:3000 | API calls succeed (no CORS block in console) |

---

### M. Rate limits & edge cases (optional)

| ID | Feature | Steps | Expected result |
|----|---------|-------|-----------------|
| M-1 | Auth rate limit | Rapid register/login attempts | 429 after threshold (`AUTH_RATE_LIMIT`) |
| M-2 | Chat rate limit | Rapid sends | 429 or throttle message |
| M-3 | Double submit | Spam Send click | Single user message, no duplicate streams |
| M-4 | Empty send | Send with empty input | Button disabled, no request |

---

### N. Visual / responsive (optional)

| ID | Feature | Steps | Expected result |
|----|---------|-------|-----------------|
| N-1 | Desktop 1280px | Full layout | Sidebar + chat readable |
| N-2 | Mobile 390px | iPhone viewport | Usable composer; sidebar overlay |
| N-3 | Dark theme | Visual check | Consistent dark surfaces, readable contrast |

---

## 7. Evidence capture standards

For every **FAIL** or **PARTIAL**:

1. **Screenshot** — full viewport, include URL bar if possible  
2. **Console** — copy errors/warnings  
3. **Network** — method, path, status, relevant response body (truncate secrets)  
4. **Repro steps** — numbered, minimal  
5. **Account used** — Admin / User A / User B  
6. **Environment** — Docker vs local, API keys present Y/N  

For **PASS** on critical paths (auth, chat stream, admin save, quota 429), one screenshot or network HAR snippet is enough.

---

## 8. Known limitations (do not file as bugs without verification)

- Quota enforcement has a small **time-of-check/time-of-use** window under concurrency (document if reproduced with parallel tabs).
- **Provider usage metadata** required for token counting; if DeepSeek omits usage, counters may not increment (server logs warning).
- Admin list is **not paginated** — fine for small user counts.
- Expert preview and chat preview require **DeepSeek**; memory features strongly need **Gemini** embeddings.
- Session id in `localStorage` (`maestro_session_id`) may exist before server session row — server creates on first API call.

---

## 9. Suggested execution order

1. Pre-flight (Section 5)  
2. Authentication (A)  
3. Chat core + SSE (B, C)  
4. Sessions (D)  
5. Settings memory (G)  
6. Attribution (I) — may need multi-turn setup  
7. Expert preview (H) — configure via admin first  
8. Quotas (E) — use admin to tune limits  
9. Admin console (F)  
10. Security (L)  
11. Dev tools (K) — if time  
12. Optional M, N  

---

## 10. Final report template (required deliverable)

Copy this structure into your completion message or `docs/browser-qa-report-YYYY-MM-DD.md`.

```markdown
# Browser QA Report — The Contextual Maestro

**Date:**  
**Tester:**  
**Environment:** (Docker / local)  
**Commit / branch:**  
**API keys:** DeepSeek [Y/N], Gemini [Y/N]  

## Executive summary

- Total checklist items:  
- PASS: / PARTIAL: / FAIL: / BLOCKED:  
- **Ship recommendation:** Go / No-Go / Go with caveats  

## Critical failures

(Blocks release — auth broken, chat broken, data leak, admin exposed to users)

| ID | Feature | Summary | Evidence |

## Major failures

(Core feature wrong — quota, sessions, memory CRUD)

| ID | Feature | Summary | Evidence |

## Minor issues

(Polish, copy, flaky UI)

| ID | Feature | Summary | Evidence |

## Features working correctly

| ID | Feature | Notes |

## Blocked / not tested

| ID | Reason |

## Duplicate or redundant behavior observed

(If any)

## API / UI contract mismatches

| Endpoint | UI expectation | Actual |

## Console & network summary

- Recurring errors:  
- Failed requests:  

## Screenshots index

| File | Description |

## Appendix: test accounts used

| Role | Email | Notes |
```

---

## 11. Quick reference — expected quota UX copy

| User state | Header quota bar (approx.) |
|------------|----------------------------|
| Admin | “Unlimited tokens (admin)” |
| Primary tier | “V4 Flash · {N} left today” |
| Fallback tier | “V3.2 tier · {N} left today” |
| Exhausted | “Quota exhausted · resets {date}” |

---

## 12. Handoff completion criteria

You are done when:

- [ ] All Pre-flight items addressed  
- [ ] Every master checklist ID (A–N) has a status or explicit BLOCKED reason  
- [ ] Final report (Section 10) is filled in  
- [ ] At least one end-to-end chat stream verified with `done` + quota update (User A)  
- [ ] Admin console edit/save verified  
- [ ] Non-admin denied `/admin`  
- [ ] Two-user isolation spot-check performed  

---

*Document version: aligned with token quotas + admin console implementation. Update this file when routes or features change.*
