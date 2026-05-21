"""SSE chat stream tests (Phase C)."""

import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import get_settings, settings
from app.services.retrieval_status import AssembledContext, RetrievalMode, RetrievalOutcome
from tests.conftest import auth, user_a


def _parse_sse(raw: str) -> list[tuple[str, dict]]:
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


@pytest.fixture
def chat_stream_settings(monkeypatch):
    import app.api.chat as chat_module
    import app.core.config as app_config

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")
    get_settings.cache_clear()
    settings = get_settings()
    app_config.settings = settings
    chat_module.settings = settings
    yield settings
    get_settings.cache_clear()


async def _fake_stream(*_args, **_kwargs):
    yield "Hello "
    yield "world"


def test_chat_stream_sse_done_includes_message_id(client, user_a, chat_stream_settings):
    user, token = user_a
    session_id = str(uuid.uuid4())

    create = client.post("/api/history/sessions", headers=auth(token))
    assert create.status_code == 201
    session_id = create.json()["session_id"]

    enhanced = AssembledContext(
        injected_facts=[],
        cross_session_memories=[],
        in_session_memories=[],
        retrieval=RetrievalOutcome(
            mode=RetrievalMode.FULL,
            cross_session_memory_available=True,
            embed_succeeded=True,
            keyword_fallback_used=False,
            reranked=False,
            rerank_fallback=False,
        ),
    )

    with (
        patch(
            "app.api.chat.build_enhanced_context",
            new_callable=AsyncMock,
            return_value=enhanced,
        ),
        patch("app.api.chat.count_active_prompt_tokens", return_value=100),
        patch(
            "app.api.chat.stream_chat_completion",
            side_effect=_fake_stream,
        ),
        patch("app.api.chat.track_background_task"),
    ):
        with client.stream(
            "POST",
            "/api/chat",
            json={"message": "hi there", "session_id": session_id},
            headers=auth(token),
        ) as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers.get("content-type", "")
            raw = "".join(resp.iter_text())

    events = _parse_sse(raw)
    event_names = [e[0] for e in events]
    assert "token" in event_names
    assert "done" in event_names

    done_payload = next(p for name, p in events if name == "done")
    assert isinstance(done_payload["assistant_message_id"], int)
    assert done_payload["session_id"] == session_id
    assert "model" in done_payload
    assert "tier" in done_payload
    assert done_payload.get("quota") is not None

    msgs = client.get(
        "/api/history/messages",
        params={"session_id": session_id},
        headers=auth(token),
    )
    assert msgs.status_code == 200
    assistant = [m for m in msgs.json() if m["role"] == "assistant"]
    assert any(m["id"] == done_payload["assistant_message_id"] for m in assistant)


def test_chat_stream_error_shape(client, user_a, chat_stream_settings):
    _, token = user_a
    session_id = str(uuid.uuid4())

    create = client.post("/api/history/sessions", headers=auth(token))
    session_id = create.json()["session_id"]

    enhanced = AssembledContext(
        injected_facts=[],
        cross_session_memories=[],
        in_session_memories=[],
        retrieval=RetrievalOutcome(
            mode=RetrievalMode.FULL,
            cross_session_memory_available=True,
            embed_succeeded=True,
            keyword_fallback_used=False,
            reranked=False,
            rerank_fallback=False,
        ),
    )

    async def empty_stream(*_args, **_kwargs):
        if False:
            yield ""

    with (
        patch(
            "app.api.chat.build_enhanced_context",
            new_callable=AsyncMock,
            return_value=enhanced,
        ),
        patch("app.api.chat.count_active_prompt_tokens", return_value=100),
        patch("app.api.chat.stream_chat_completion", side_effect=empty_stream),
        patch("app.api.chat.track_background_task"),
    ):
        with client.stream(
            "POST",
            "/api/chat",
            json={"message": "empty please", "session_id": session_id},
            headers=auth(token),
        ) as resp:
            raw = "".join(resp.iter_text())

    events = _parse_sse(raw)
    err = next((p for name, p in events if name == "error"), None)
    assert err is not None
    assert "message" in err
    assert "code" in err


def test_selective_context_disabled_matches_legacy_pack(db, user_a):
    from app.models.episode import Episode
    from app.services.context_manager import _build_completion_messages_uncached
    from app.services.context_packer import _build_legacy_messages
    from app.services.rls import set_bypass_rls, set_tenant_context

    user, _token = user_a
    session_id = str(uuid.uuid4())
    set_bypass_rls(db)
    for i in range(4):
        db.add(
            Episode(
                user_id=user.id,
                session_id=session_id,
                episode_kind="message",
                role="user" if i % 2 == 0 else "assistant",
                content=f"turn {i} content",
                embed_status="pending",
            )
        )
    db.commit()
    set_tenant_context(db, user.id)

    with patch.object(settings, "selective_context_enabled", False):
        legacy = _build_legacy_messages(db, user.id, session_id, None)
        packed = _build_completion_messages_uncached(
            db, user.id, session_id, None, current_query="turn 0"
        )
    assert legacy == packed
