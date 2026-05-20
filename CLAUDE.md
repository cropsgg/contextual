# Contextual Maestro — Claude Code guide

## Speed (read first)

This repo is **small** (~23 frontend files) if you ignore generated folders.

**Never read or search:** `client/node_modules/`, `client/.next/`, `server/.venv/`, `server/`, `postgres-data/`, `docs/qa-screenshots/`.

**Do not** run broad `find`, `ls -R`, or read `bun.lock` unless debugging deps.

For any task, open **only the files you will edit** — do not “survey the entire frontend” first.

## UI styling (Claude Code look)

Match **Claude Code terminal UI**: warm dark grays, subtle borders, coral accent, clean sans.

**Change styling only via these files first** (covers most of the app via Tailwind tokens):

1. `client/tailwind.config.ts` — `surface`, `accent`, optional `claude` palette
2. `client/app/globals.css` — CSS variables, base typography
3. `client/app/layout.tsx` — fonts only if needed

**Do not** move buttons, change routes, or alter React logic unless explicitly asked.

Component files (`client/components/*`) — touch **only** if a hardcoded color breaks after token updates. Prefer replacing `zinc-*` / `indigo-*` with `surface-*` / `accent` tokens, not layout changes.

## Stack

- Frontend: `client/` — Next.js 15, Tailwind, Bun
- Backend: `server/` — FastAPI (ignore for UI-only work)
- Run UI: `cd client && bun run dev` (port 3000)
