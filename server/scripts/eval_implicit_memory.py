#!/usr/bin/env python3
"""Evaluate implicit memory extraction (no explicit 'remember' phrasing)."""

from __future__ import annotations

import json
import sys
import time
import uuid
import http.client
from urllib.parse import urlparse
from urllib.request import Request, urlopen

BASE = "http://localhost:8000"
WAIT_SECONDS = 25


def req(method: str, path: str, token: str | None = None, data: dict | None = None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode() if data else None
    r = Request(BASE + path, data=body, headers=headers, method=method)
    with urlopen(r, timeout=120) as resp:
        return resp.status, dict(resp.headers), resp.read()


def login(email: str, password: str) -> str:
    _, _, raw = req("POST", "/api/auth/login", data={"email": email, "password": password})
    return json.loads(raw)["access_token"]


def register(email: str, password: str) -> str:
    req("POST", "/api/auth/register", data={"email": email, "password": password})
    return login(email, password)


def create_session(token: str) -> str:
    _, _, raw = req("POST", "/api/history/sessions", token=token)
    return json.loads(raw)["session_id"]


def chat(token: str, session_id: str, message: str) -> tuple[int, dict, str]:
    u = urlparse(BASE)
    conn = http.client.HTTPConnection(u.hostname, u.port, timeout=120)
    conn.request(
        "POST",
        "/api/chat",
        json.dumps({"message": message, "session_id": session_id}),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    resp = conn.getresponse()
    hdrs = dict(resp.getheaders())
    text = resp.read().decode()
    conn.close()
    tokens: list[str] = []
    for block in text.split("\n\n"):
        ev, data = None, None
        for line in block.split("\n"):
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:])
        if ev == "token" and data:
            tokens.append(data.get("content", data.get("text", "")))
    return resp.status, hdrs, "".join(tokens)


def get_facts(token: str) -> dict[str, str]:
    _, _, raw = req("GET", "/api/memory/facts", token=token)
    facts = json.loads(raw)
    return {f["fact_key"]: f["fact_value"] for f in facts}


def get_extraction_runs(token: str) -> list[dict]:
    try:
        _, _, raw = req("GET", "/api/memory/extraction-runs?limit=5", token=token)
        return json.loads(raw)
    except Exception:
        return []


def trigger_extraction(token: str, session_id: str, user_msgs: list[str]) -> None:
    """Send filler + scenario messages until session extraction fires (every 4 user msgs)."""
    n = len(user_msgs)
    pad = (4 - (n % 4)) % 4
    for i in range(pad):
        chat(token, session_id, f"OK {i + 1}")
    for msg in user_msgs:
        chat(token, session_id, msg)


def eval_scenario(
    token: str,
    name: str,
    user_msgs: list[str],
    expect_keys: list[str],
    expect_values: list[str],
    expect_absent_keys: list[str] | None = None,
) -> dict:
    session = create_session(token)
    keys_before = set(get_facts(token))
    trigger_extraction(token, session, user_msgs)
    print(f"  waiting {WAIT_SECONDS}s for extraction...")
    time.sleep(WAIT_SECONDS)
    facts = get_facts(token)
    new_keys = set(facts) - keys_before

    hits: list[str] = []
    misses: list[str] = []
    if expect_keys:
        matched = [ek for ek in expect_keys if ek in facts]
        if matched:
            hits = matched
        else:
            misses = list(expect_keys)

    value_hits = [v for v in expect_values if any(v.lower() in str(fv).lower() for fv in facts.values())]
    value_misses = [v for v in expect_values if v not in value_hits]

    false_positives = []
    if expect_absent_keys:
        false_positives = [k for k in expect_absent_keys if k in new_keys]

    # Positive scenario: need key OR value match; negative: no new keys
    if expect_keys or expect_values:
        passed = (len(hits) > 0 or len(value_hits) > 0) and len(false_positives) == 0
    else:
        passed = len(new_keys) == 0 and len(false_positives) == 0

    return {
        "name": name,
        "passed": passed,
        "messages": user_msgs,
        "new_keys": sorted(new_keys),
        "expected_keys_found": hits,
        "expected_keys_missing": misses,
        "expected_values_found": value_hits,
        "expected_values_missing": value_misses,
        "false_positive_keys": false_positives,
        "all_facts": facts,
    }


def main() -> int:
    suffix = uuid.uuid4().hex[:8]
    email = f"implicit_mem_{suffix}@example.com"
    password = "TestPass123!"

    print(f"Registering test user {email}")
    token = register(email, password)

    scenarios = [
        {
            "name": "Location (I live in)",
            "msgs": ["I live in Portland, Oregon."],
            "keys": ["city_of_residence", "location", "home_city", "residence"],
            "values": ["Portland"],
            "absent": ["debug_port", "temp_port"],
        },
        {
            "name": "Job (I work as)",
            "msgs": ["I work as a platform engineer at a startup."],
            "keys": ["job_title", "occupation", "profession", "role"],
            "values": ["platform engineer"],
            "absent": ["debug_port"],
        },
        {
            "name": "Name (call me)",
            "msgs": ["Call me River — that's what friends use."],
            "keys": ["preferred_name", "name", "nickname"],
            "values": ["River"],
            "absent": [],
        },
        {
            "name": "Preference (I prefer)",
            "msgs": ["I prefer typescript over javascript for backend work."],
            "keys": ["language_preference", "preferred_language", "coding_preference"],
            "values": ["typescript"],
            "absent": [],
        },
        {
            "name": "Ephemeral task (should NOT store)",
            "msgs": ["Use port 8844 for debugging this API issue today only."],
            "keys": [],
            "values": [],
            "absent": ["debug_port", "temp_port", "port"],
        },
        {
            "name": "Small talk (should NOT store)",
            "msgs": ["Sounds good", "thanks", "OK"],
            "keys": [],
            "values": [],
            "absent": [],
        },
    ]

    results = []
    print("\n=== Implicit memory evaluation (no 'remember') ===\n")
    for sc in scenarios:
        print(f"Scenario: {sc['name']}")
        r = eval_scenario(
            token,
            sc["name"],
            sc["msgs"],
            sc["keys"],
            sc["values"],
            sc.get("absent"),
        )
        results.append(r)
        status = "PASS" if r["passed"] else "FAIL"
        print(f"  -> {status}")
        if r["new_keys"]:
            print(f"     new keys: {r['new_keys']}")
        if r["expected_keys_missing"]:
            print(f"     missing keys (any of): {r['expected_keys_missing']}")
        if r["expected_values_missing"]:
            print(f"     missing values: {r['expected_values_missing']}")
        if r["false_positive_keys"]:
            print(f"     false positives: {r['false_positive_keys']}")
        print()

    # Cross-session recall for location scenario if stored
    print("=== Cross-session recall check ===")
    facts_final = get_facts(token)
    recall_token = facts_final
    recall_session = create_session(token)
    _, hdrs, reply = chat(
        token,
        recall_session,
        "What city do I live in? Reply with only the city name.",
    )
    print(f"  retrieval mode: {hdrs.get('x-retrieval-mode', '?')}")
    print(f"  reply: {reply.strip()[:120]}")
    portland_recall = "portland" in reply.lower()
    print(f"  Portland recall: {'YES' if portland_recall else 'NO'}")

    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    print(f"\n=== Summary: {passed}/{total} scenarios passed ===")
    for r in results:
        mark = "✓" if r["passed"] else "✗"
        print(f"  {mark} {r['name']}")

    out_path = f"/tmp/implicit_memory_eval_{suffix}.json"
    with open(out_path, "w") as f:
        json.dump({"results": results, "recall": reply.strip(), "facts": facts_final}, f, indent=2)
    print(f"\nFull results: {out_path}")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
