#!/usr/bin/env python3
"""Drive chat API for compression/selective-context QA (not for production)."""

from __future__ import annotations

import json
import os
import sys
import uuid

import httpx

API = os.environ.get("API_URL", "http://localhost:8000")
EMAIL = os.environ.get("QA_EMAIL", "qa_selective_1779305295@example.com")
PASSWORD = os.environ.get("QA_PASSWORD", "TestPass123!")


def login(client: httpx.Client) -> str:
    r = client.post(f"{API}/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
    r.raise_for_status()
    return r.json()["access_token"]


def stream_chat(client: httpx.Client, token: str, session_id: str, message: str) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"message": message, "session_id": session_id}
    events: list[tuple[str, dict]] = []
    with client.stream(
        "POST",
        f"{API}/api/chat",
        json=payload,
        headers=headers,
        timeout=120.0,
    ) as resp:
        resp.raise_for_status()
        event = "message"
        data_buf = ""
        for line in resp.iter_lines():
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data_buf = line.split(":", 1)[1].strip()
            elif line == "" and data_buf:
                try:
                    events.append((event, json.loads(data_buf)))
                except json.JSONDecodeError:
                    events.append((event, {"raw": data_buf}))
                data_buf = ""
                event = "message"
    done = next((d for e, d in reversed(events) if e == "done"), {})
    return {"events": events, "done": done}


def main() -> int:
    session_id = str(uuid.uuid4())
    filler = (
        "Project Orion uses distributed telemetry, nightly batch reconciliation, "
        "and a risk register tracked in Jira. "
    )
    prompts = [
        "My name is Alex and I work on Project Orion. Remember this.",
        "List three risks for Project Orion in one sentence each.",
    ]
    for i in range(3, 22):
        prompts.append(filler * 8 + f" Turn {i}: note item {i} for Orion.")
    prompts.extend(
        [
            "What is my name and which project did I mention?",
            "What was risk number three from earlier?",
            "Summarize Orion discussion in two bullets.",
        ]
    )
    with httpx.Client() as client:
        token = login(client)
        headers = {"Authorization": f"Bearer {token}"}
        print(f"session_id={session_id}")
        for i, msg in enumerate(prompts, 1):
            print(f"\n--- turn {i} ---")
            print(f"user: {msg[:80]}...")
            try:
                out = stream_chat(client, token, session_id, msg)
            except httpx.HTTPStatusError as exc:
                print(f"HTTP {exc.response.status_code}: {exc.response.text[:500]}")
                return 1
            ev_types = [e for e, _ in out["events"]]
            print("events:", ev_types)
            if "compression_started" in ev_types:
                print("  >> compression_started")
            done = out.get("done") or {}
            print(
                "done:",
                {
                    k: done.get(k)
                    for k in ("assistant_message_id", "session_id", "tier", "model")
                    if k in done
                },
            )
            mid = done.get("assistant_message_id")
            if mid:
                ar = client.get(
                    f"{API}/api/history/attribution",
                    params={"session_id": session_id, "message_id": mid},
                    headers=headers,
                )
                if ar.status_code == 200:
                    attr = ar.json()
                    packer = (attr.get("attribution") or {}).get("packer")
                    sel = (attr.get("attribution") or {}).get("active_turns_selected")
                    floor = (attr.get("attribution") or {}).get("active_turns_floor")
                    print(
                        "attribution:",
                        {
                            "packer": packer,
                            "selected_count": len(sel or []),
                            "floor_count": len(floor or []),
                        },
                    )
        ctx = client.get(
            f"{API}/api/history/context",
            params={"session_id": session_id},
            headers=headers,
        )
        print("\ncontext:", ctx.status_code)
        if ctx.status_code == 200:
            print(json.dumps(ctx.json(), indent=2)[:2000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
