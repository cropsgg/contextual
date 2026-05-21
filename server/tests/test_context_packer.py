"""Tests for budget-aware context packing."""

from unittest.mock import patch

from app.core.config import settings
from app.services.context_packer import _truncate_content, pack_sync
from app.services.token_counter import count_text_tokens


def test_truncate_content_caps_tokens():
    long_text = "x " * 5000
    out = _truncate_content(long_text, max_tokens=50)
    assert "...[truncated]..." in out
    assert count_text_tokens(out) <= 55


def test_pack_legacy_when_selective_disabled(db, user_a):
    from app.models.episode import Episode
    from app.services.context_packer import _build_legacy_messages
    from app.services.rls import set_bypass_rls, set_tenant_context

    user, _token = user_a
    session_id = "pack-legacy-session"
    set_bypass_rls(db)
    for i in range(3):
        db.add(
            Episode(
                user_id=user.id,
                session_id=session_id,
                episode_kind="message",
                role="user" if i % 2 == 0 else "assistant",
                content=f"message {i}",
                embed_status="pending",
            )
        )
    db.commit()
    set_tenant_context(db, user.id)

    with patch.object(settings, "selective_context_enabled", False):
        legacy = _build_legacy_messages(db, user.id, session_id, None)
        packed = pack_sync(db, user.id, session_id, "query about message 1", None)
    assert len(legacy) == len(packed.messages)
    assert legacy == packed.messages
