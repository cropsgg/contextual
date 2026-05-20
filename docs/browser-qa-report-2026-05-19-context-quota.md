# Browser QA — Daily quota, model responses, and context management

**Date:** 2026-05-19  
**Environment:** `http://localhost:3000/?debug=1` (client), API `http://localhost:8000` (Docker `contextual-maestro-api`)  
**Tester:** Cursor agent (browser + API corroboration)

---

## Executive summary

| Area | Verdict |
|------|---------|
| **1M daily token quota** (header label + accounting) | **PASS** — new users start at 1.0M; usage decreases after chat |
| **Model responses** (accuracy + instruction following) | **PASS** — arithmetic, one-word replies, post-compression recall |
| **Session context management** (4k window, compression, memory) | **PASS** — compression triggers, counters update, summaries retain facts |
| **Expert prompt preview** (`?debug=1`) | **PASS** — token count + injected facts visible |
| **Offloaded archive UI** | **PARTIAL** — count shows 7; expand panel empty on first open (API has items) |

**Important:** “1 million tokens” in the product is the **daily DeepSeek API budget** (header text). It is **not** the green “Active tokens / 4,000” bar in the Context panel. That bar is the **per-session prompt window** before compression offloads older turns.

---

## Two token systems (do not conflate)

| UI surface | Default | Meaning |
|------------|---------|---------|
| Chat header — `V4 Flash · 1.0M left today` | 1,000,000 / day | Primary-tier DeepSeek usage quota; decrements on each completion |
| Context panel — `Active tokens 3,063 / 4,000` | 4,000 threshold | In-session prompt size; over threshold → summarize + offload |
| Admin header — `Unlimited tokens (admin)` | N/A | `token_unlimited` bypasses daily quota display |

Config: `server/app/core/config.py` — `default_quota_primary_daily`, `context_threshold_tokens`.

---

## Test matrix

### A. Daily quota (1M)

| ID | Test | Steps | Expected | Result |
|----|------|-------|----------|--------|
| Q-1 | Initial quota on register | Register `quota_browser_*@example.com` | Header shows **V4 Flash · 1.0M left today** | **PASS** (browser) |
| Q-2 | Quota decreases after chat | Send “Say hello in one word.” | Header drops (e.g. **1000K left today**); no error | **PASS** (browser: 1.0M → 1000K) |
| Q-3 | API accounting | `GET /api/auth/me` before/after one chat | `primary_remaining` 1_000_000 → lower; `primary_used_today` increases | **PASS** (API: −92 tokens on first message) |
| Q-4 | Admin unlimited | Login `admin@example.com` | **Unlimited tokens (admin)** | **PASS** (browser) |
| Q-5 | No quota fill bar | Inspect chat chrome | Text label only (no progress bar for daily quota) | **PASS** (by design) |

**Note:** Filling the entire 1M quota in-browser is impractical (~10k+ messages). Q-3 validates the metering pipeline.

---

### B. Model responses

| ID | Test | Steps | Expected | Result |
|----|------|-------|----------|--------|
| M-1 | Arithmetic | “What is 17 + 25? Reply with only the number.” | `42` | **PASS** (browser) |
| M-2 | One-word constraint | “Say hello in one word.” | Single word (e.g. `Hello`) | **PASS** (browser) |
| M-3 | Streaming UX | Send message | Stop button while streaming; assistant bubble fills | **PASS** (browser) |
| M-4 | Session title | After first message | Sidebar title updates (e.g. `42`, `Hello`) | **PASS** (browser) |
| M-5 | Post-compression recall | After 12 long turns, ask `MARKER_05_QUOKKA` | Exact marker returned | **PASS** (API: all 3 markers correct) |
| M-6 | Memory + codeword | Sessions with profile facts | Codeword recall uses stored facts when present | **PASS** (prior runs; LYNX33/PLUMBER42 paths) |

---

### C. Context management (`?debug=1`)

| ID | Test | Steps | Expected | Result |
|----|------|-------|----------|--------|
| C-1 | Token counter increases | Multiple turns in one session | Active tokens rise toward 4,000 | **PASS** (API: 354 → 3664 over 11 turns) |
| C-2 | Compression triggers | Continue past ~4k active tokens | Offloaded msgs > 0; memory chunks ≥ 1; active count drops | **PASS** (turn 12: 7 offloaded, 1 chunk, active 2961) |
| C-3 | Summary label | After compression | “7 messages summarized on …” | **PASS** (browser) |
| C-4 | Latest compressed summary | Expand in Context panel | Project Atlas / Quokka markers text | **PASS** (browser) |
| C-5 | Expert preview | “Preview before send” on loaded session | ~token count, model name, facts list | **PASS** (~3244 tokens · deepseek-chat; Atlas facts + LYNX33) |
| C-6 | Per-session isolation | New chat | Offloaded 0 / chunks 0 | **PASS** (browser new chat) |
| C-7 | Offloaded archive | Toggle “Offloaded archive” | List of archived snippets | **PARTIAL** — API `total: 7`, UI first open showed “No archived messages” |
| C-8 | Retrieval headers | Chat after compression | `x-retrieval-mode: full` | **PASS** (prior API verification) |
| C-9 | Fact extraction after compression | Check context status | `last_fact_extraction_at` set, no error | **PASS** (API: succeeded at 15:42:02 UTC) |

#### Compression stress test (API, session `Project Atlas Summary`)

| Turn | Active tokens | Offloaded | Chunks |
|------|---------------|-----------|--------|
| 1 | 354 | 0 | 0 |
| 6 | 2,009 | 0 | 0 |
| 11 | 3,664 | 0 | 0 |
| 12 | **2,961** | **7** | **1** |

Recall after compression:

- `MARKER_00_QUOKKA` → `MARKER_00_QUOKKA`
- `MARKER_05_QUOKKA` → `MARKER_05_QUOKKA`
- `MARKER_11_QUOKKA` → `MARKER_11_QUOKKA`

---

## Issues found

### Minor — Offloaded archive empty on first expand

- **Symptom:** Context shows **Offloaded msgs: 7**, but toggling **Offloaded archive** once showed “No archived messages.”
- **API:** `GET /api/history/offloaded?session_id=…` returns `total: 7`, 5 items on page 1.
- **Likely cause:** Race or stale `archive` state in `ContextMonitor.tsx` on first open (`loadArchive` async).
- **Severity:** Low — compression and counts work; transparency panel flaky.

### Informational — “1M memory” vs “4k context”

Users may expect a 1M-token **context fill bar**. The UI only shows 1M in the **daily quota header**; session fill is **4,000** in debug Context panel.

### Informational — No daily quota progress bar

Daily quota is text-only (`quotaBarLabel` in `client/lib/quota.ts`). There is no visual “% of 1M used” bar.

---

## How to reproduce

```bash
# Stack
docker compose up -d postgres backend
cd client && bun run dev

# Debug context panel
open 'http://localhost:3000/?debug=1'

# API compression stress (admin)
# See agent-handoff-memory-and-extraction.md §7 or run 12× long turns until offloaded > 0
```

**Test accounts:** See `docs/agent-handoff-memory-and-extraction.md`.

---

## Screenshots / evidence

| File | Content |
|------|---------|
| `docs/qa-screenshots/quota-1m-header.png` | New user: **V4 Flash · 1.0M left today** |
| Browser (admin, `Project Atlas Summary` session) | Context: 7 offloaded, 1 memory chunk, summary expanded |
| Prior | `docs/qa-screenshots/J-3-sources-fixed.png`, `J-2-cross-session.png` |

---

## Recommendations

1. **UX copy:** Clarify in UI or docs: “1M tokens/day” (quota) vs “4k active context” (compression).
2. **Offloaded archive:** Fix first-load race in `ContextMonitor` so archive list matches count.
3. **Optional:** Daily quota progress bar or “used today” in header for non-admin users.
4. **QA automation:** Port turn-1→12 API script into `server/tests/` as regression for compression + marker recall.

---

## Related docs

- `docs/agent-handoff-memory-and-extraction.md` — memory/Gemini/extraction handoff
- `docs/browser-qa-handoff.md` — full 94-item checklist
- `client/components/ContextMonitor.tsx` — debug context UI
- `server/app/services/context_manager.py` — compression implementation
