#!/usr/bin/env python3
"""Exercise long-chat context engineering: compression, offload, facts, retrieval."""

from __future__ import annotations

import json
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = "http://127.0.0.1:8000"
DELAY_S = 2.2  # stay under 30/min rate limit


def http(
    method: str,
    path: str,
    *,
    token: str | None = None,
    data: dict | None = None,
    params: dict | None = None,
) -> tuple[int, object]:
    url = BASE + path
    if params:
        url += "?" + urlencode(params)
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode() if data is not None else None
    req = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(req, timeout=120) as resp:
            raw = resp.read()
            if not raw:
                return resp.status, None
            return resp.status, json.loads(raw)
    except HTTPError as e:
        err_body = e.read().decode()
        try:
            detail = json.loads(err_body)
        except json.JSONDecodeError:
            detail = err_body
        return e.code, detail


def _parse_sse_events(raw: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in raw.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event = "message"
        data = ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
        if data:
            events.append((event, json.loads(data)))
    return events


def stream_chat(token: str, session_id: str, message: str) -> str:
    """POST /api/chat returns SSE (token + done events)."""
    url = BASE + "/api/chat"
    payload = json.dumps({"message": message, "session_id": session_id}).encode()
    req = Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    with urlopen(req, timeout=180) as resp:
        raw = resp.read().decode(errors="replace")
    parts: list[str] = []
    for name, payload in _parse_sse_events(raw):
        if name == "token":
            parts.append(str(payload.get("text", "")))
        if name == "error":
            raise RuntimeError(payload.get("message", "stream error"))
    return "".join(parts)


def create_session(token: str) -> str:
    code, body = http("POST", "/api/history/sessions", token=token)
    if code != 201:
        raise RuntimeError(f"create session failed: {code} {body}")
    return body["session_id"]  # type: ignore[index]


def register_and_login(email: str, password: str) -> str:
    code, _ = http("POST", "/api/auth/register", data={"email": email, "password": password})
    if code not in (200, 201, 409):
        raise RuntimeError(f"register failed: {code}")
    code, body = http("POST", "/api/auth/login", data={"email": email, "password": password})
    if code != 200:
        raise RuntimeError(f"login failed: {code} {body}")
    return body["access_token"]  # type: ignore[index]


def context_status(token: str, session_id: str) -> dict:
    code, body = http(
        "GET",
        "/api/history/context",
        token=token,
        params={"session_id": session_id},
    )
    if code != 200:
        raise RuntimeError(f"context failed: {code} {body}")
    return body  # type: ignore[return-value]


def list_messages(token: str, session_id: str) -> list:
    code, body = http(
        "GET",
        "/api/history/messages",
        token=token,
        params={"session_id": session_id},
    )
    if code != 200:
        raise RuntimeError(f"messages failed: {code} {body}")
    return body  # type: ignore[return-value]


def list_facts(token: str) -> list:
    code, body = http("GET", "/api/memory/facts", token=token)
    if code != 200:
        raise RuntimeError(f"facts failed: {code} {body}")
    return body  # type: ignore[return-value]


def memory_episodes(token: str) -> list:
    code, body = http(
        "GET",
        "/api/memory/episodes",
        token=token,
        params={"page": 1, "limit": 50},
    )
    if code != 200:
        raise RuntimeError(f"episodes failed: {code} {body}")
    return body.get("items", body) if isinstance(body, dict) else body  # type: ignore[union-attr]


def last_assistant_id(token: str, session_id: str) -> int | None:
    msgs = list_messages(token, session_id)
    for m in reversed(msgs):
        if m.get("role") == "assistant":
            return int(m["id"])
    return None


def attribution(token: str, session_id: str, message_id: int) -> dict:
    code, body = http(
        "GET",
        "/api/history/attribution",
        token=token,
        params={"session_id": session_id, "message_id": message_id},
    )
    if code != 200:
        return {}
    return body  # type: ignore[return-value]


def long_user_message(turn: int, extra: str = "") -> str:
    pad = " ".join(["context-padding"] * 80)
    return (
        f"[Turn {turn}] I am working on **Project Nebula Cart**, a grocery delivery app. "
        f"My secret codeword for tests is ZEBRA-42. {extra}\n\n"
        f"Explain CAP theorem, PACELC, and how Postgres replication relates to "
        f"eventual consistency. Give concrete examples. {pad}"
    )


def main() -> int:
    email = f"longchat_{int(time.time())}@example.com"
    password = "testpass123"
    print(f"=== Long-chat E2E ({email}) ===\n")

    token = register_and_login(email, password)
    session_a = create_session(token)
    session_b = create_session(token)

    ctx_log: list[dict] = []
    turns = 14

    print(f"--- Phase 1: {turns} turns in session A (target: compression + offload) ---")
    for i in range(1, turns + 1):
        msg = long_user_message(i)
        if i == 1:
            msg += " Remember: my favorite color is teal and the app codename is Nebula Cart."
        print(f"  turn {i}/{turns}: sending…", flush=True)
        reply = stream_chat(token, session_a, msg)
        print(f"    assistant chars: {len(reply)}", flush=True)
        time.sleep(DELAY_S)
        # allow background compression
        if i % 3 == 0:
            time.sleep(4)
        ctx = context_status(token, session_a)
        ctx_log.append({"turn": i, **ctx})
        print(
            f"    tokens={ctx['active_token_count']}/{ctx['context_threshold']} "
            f"offloaded={ctx['offloaded_message_count']} "
            f"memory_chunks={ctx['memory_chunk_count']} "
            f"compressing={ctx['compression_in_progress']}",
            flush=True,
        )
        if ctx.get("last_summary"):
            print(f"    summary preview: {ctx['last_summary'][:120]}…", flush=True)

    print("\n--- Phase 2: facts (auto-extraction) ---")
    time.sleep(6)
    facts = list_facts(token)
    keys = {f["fact_key"]: f["fact_value"] for f in facts}
    print(f"  fact count: {len(facts)}")
    for k, v in list(keys.items())[:12]:
        print(f"    {k}: {v[:80]}")

    print("\n--- Phase 3: active messages vs offload ---")
    active_msgs = list_messages(token, session_a)
    print(f"  active (non-offloaded) messages in API: {len(active_msgs)}")
    mem_eps = memory_episodes(token)
    print(f"  memory episodes (global): {len(mem_eps)}")
    final_ctx = context_status(token, session_a)
    print(f"  final tokens: {final_ctx['active_token_count']}")
    print(f"  final offloaded: {final_ctx['offloaded_message_count']}")
    print(f"  final memory chunks (session): {final_ctx['memory_chunk_count']}")

    print("\n--- Phase 4: cross-session retrieval (session B) ---")
    retrieval_q = (
        "In my other chats I mentioned Project Nebula Cart and codeword ZEBRA-42. "
        "What was the project name and codeword? Answer briefly."
    )
    stream_chat(token, session_b, retrieval_q)
    time.sleep(DELAY_S)
    aid = last_assistant_id(token, session_b)
    attr = attribution(token, session_b, aid) if aid else {}
    print(f"  assistant message id: {aid}")
    print(f"  attribution facts: {len(attr.get('facts', []))}")
    print(f"  attribution memories: {len(attr.get('memories', []))}")
    for m in attr.get("memories", [])[:3]:
        print(f"    memory from session {m.get('session_id', '')[:8]}… snippet: {m.get('snippet', '')[:100]}…")

    print("\n--- Phase 5: same-session recall after compression ---")
    recall_q = "What is my secret codeword and favorite color from earlier in THIS chat?"
    stream_chat(token, session_a, recall_q)
    time.sleep(DELAY_S)
    aid2 = last_assistant_id(token, session_a)
    attr2 = attribution(token, session_a, aid2) if aid2 else {}
    print(f"  attribution facts: {len(attr2.get('facts', []))}")
    for f in attr2.get("facts", []):
        print(f"    fact {f.get('fact_key')}: {f.get('fact_value')}")

    # Pass/fail heuristics
    print("\n=== RESULTS ===")
    max_offloaded = max(c["offloaded_message_count"] for c in ctx_log)
    max_memory = max(c["memory_chunk_count"] for c in ctx_log)
    compressed = max_offloaded > 0 and max_memory > 0
    under_threshold = final_ctx["active_token_count"] <= final_ctx["context_threshold"]
    has_facts = len(facts) > 0
    has_retrieval = len(attr.get("memories", [])) > 0 or any(
        "nebula" in str(f).lower() or "zebra" in str(f).lower() for f in attr.get("facts", [])
    )

    checks = [
        ("compression/offload ran", compressed),
        ("active tokens at or below threshold after compression", under_threshold),
        ("auto fact extraction produced facts", has_facts),
        ("cross-session attribution has memories or relevant facts", has_retrieval),
    ]
    ok = True
    for name, passed in checks:
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}")
        ok = ok and passed

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
