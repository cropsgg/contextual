"""Offloaded message archive and compression summary APIs (RLS-scoped)."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.models.episode import Episode
from app.services.database import SessionLocal
from app.services.rls import set_tenant_context
from tests.conftest import auth


def test_offloaded_list_and_compression_summary(client: TestClient, user_a):
    user, token = user_a
    session_id = f"archive-{uuid.uuid4().hex[:8]}"

    with SessionLocal() as db:
        set_tenant_context(db, user.id)
        db.add(
            Episode(
                user_id=user.id,
                session_id=session_id,
                episode_kind="message",
                role="user",
                content="old user message content",
                is_offloaded=True,
            )
        )
        db.add(
            Episode(
                user_id=user.id,
                session_id=session_id,
                episode_kind="memory",
                role="system",
                content="Session summary text",
                summary="Session summary text",
                is_offloaded=False,
            )
        )
        db.commit()

    offloaded = client.get(
        "/api/history/offloaded",
        params={"session_id": session_id},
        headers=auth(token),
    )
    assert offloaded.status_code == 200
    body = offloaded.json()
    assert body["total"] == 1
    assert body["items"][0]["snippet"].startswith("old user")

    summary = client.get(
        "/api/history/compression-summary",
        params={"session_id": session_id},
        headers=auth(token),
    )
    assert summary.status_code == 200
    assert summary.json()["summary"] == "Session summary text"
    assert summary.json()["offloaded_message_count"] == 1

    ctx = client.get(
        "/api/history/context",
        params={"session_id": session_id},
        headers=auth(token),
    )
    assert ctx.status_code == 200
    assert ctx.json()["offloaded_message_count"] == 1
    assert ctx.json()["offloaded_summary_label"] is not None


def test_offloaded_hidden_from_other_user(client: TestClient, user_a, user_b):
    user_a_obj, token_a = user_a
    _user_b, token_b = user_b
    session_id = f"private-{uuid.uuid4().hex[:8]}"

    with SessionLocal() as db:
        set_tenant_context(db, user_a_obj.id)
        db.add(
            Episode(
                user_id=user_a_obj.id,
                session_id=session_id,
                episode_kind="message",
                role="user",
                content="secret offloaded",
                is_offloaded=True,
            )
        )
        db.commit()

    res = client.get(
        "/api/history/offloaded",
        params={"session_id": session_id},
        headers=auth(token_b),
    )
    assert res.status_code == 200
    assert res.json()["total"] == 0
