"""Integration tests for selective context packing."""

from unittest.mock import patch

from app.core.config import settings
from app.models.episode import Episode
from app.services.context_packer import pack_sync
from app.services.rls import set_bypass_rls, set_tenant_context
from app.services.token_counter import count_text_tokens


def test_pack_respects_token_budget(db, user_a):
    user, _token = user_a
    session_id = "selective-budget-session"
    set_bypass_rls(db)
    for i in range(12):
        db.add(
            Episode(
                user_id=user.id,
                session_id=session_id,
                episode_kind="message",
                role="user" if i % 2 == 0 else "assistant",
                content=(
                    f"Turn {i}: discussion about kubernetes deployment "
                    f"strategy number {i}."
                ),
                embed_status="pending",
            )
        )
    db.commit()
    set_tenant_context(db, user.id)

    with patch.object(settings, "selective_context_enabled", True):
        with patch.object(settings, "prompt_token_budget", 8000):
            with patch.object(settings, "active_retrieval_floor_turns", 6):
                result = pack_sync(
                    db,
                    user.id,
                    session_id,
                    "kubernetes deployment strategy",
                    enhanced=None,
                )
    tokens = count_text_tokens(result.messages)
    assert tokens <= settings.prompt_token_budget + 200
    assert len(result.attribution.active_turns_floor) == 6
