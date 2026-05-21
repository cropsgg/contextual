"""Budget-aware prompt assembly with selective active-turn retrieval (Phase 5)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.episode import Episode
from app.services.active_turn_retrieval import (
    ActiveRetrievalResult,
    RankedActiveTurn,
    retrieve_active_turns,
    load_active_timeline,
)
from app.services.retrieval_status import EnhancedContext
from app.services.token_counter import count_chat_messages_tokens, count_text_tokens

logger = logging.getLogger(__name__)

_TRUNC_MARKER = "\n...[truncated]..."


@dataclass
class PackAttribution:
    active_turns_selected: list[dict] = field(default_factory=list)
    active_turns_floor: list[int] = field(default_factory=list)
    packer: dict = field(default_factory=dict)
    active_retrieval_degraded: bool = False


@dataclass
class PackResult:
    messages: list[dict[str, str]]
    attribution: PackAttribution
    episode_by_id: dict[int, Episode] = field(default_factory=dict)


_last_pack_attribution: dict[str, PackAttribution] = {}


def _session_key(user_id: int, session_id: str) -> str:
    return f"{user_id}:{session_id}"


def get_last_pack_attribution(user_id: int, session_id: str) -> PackAttribution | None:
    return _last_pack_attribution.get(_session_key(user_id, session_id))


def _message_dict(role: str, content: str) -> dict[str, str]:
    return {"role": role, "content": content}


def _truncate_content(content: str, max_tokens: int) -> str:
    if count_text_tokens(content) <= max_tokens:
        return content
    from app.services.token_counter import _encoding

    enc = _encoding()
    tokens = enc.encode(content)
    if len(tokens) <= max_tokens:
        return content
    trimmed = enc.decode(tokens[: max(1, max_tokens - 20)])
    return trimmed + _TRUNC_MARKER


def _tokens_for_messages(messages: list[dict[str, str]]) -> int:
    return count_chat_messages_tokens(messages)


def _episode_content_for_turn(
    turn: RankedActiveTurn,
    episode_by_id: dict[int, Episode],
) -> str:
    if turn.is_chunk and turn.parent_episode_id:
        parent = episode_by_id.get(turn.parent_episode_id)
        if parent and (parent.token_count or 0) <= settings.active_turn_chunk_threshold_tokens:
            return parent.content
        return turn.content
    ep = episode_by_id.get(turn.display_episode_id)
    return ep.content if ep else turn.content


def _build_legacy_messages(
    db: Session,
    user_id: int,
    session_id: str,
    enhanced: EnhancedContext | None,
) -> list[dict[str, str]]:
    from app.services.context_manager import (
        format_enhanced_system_blocks,
        latest_memory_episode,
        load_active_message_episodes,
    )

    memory = latest_memory_episode(db, user_id, session_id)
    active = load_active_message_episodes(db, user_id, session_id)
    messages: list[dict[str, str]] = [
        _message_dict("system", settings.chat_system_prompt),
    ]
    if enhanced is not None:
        messages.extend(format_enhanced_system_blocks(enhanced))
    if memory:
        messages.append(
            _message_dict("system", f"Compressed context:\n{memory.content}")
        )
    for ep in active:
        role = ep.role if ep.role in ("user", "assistant", "system") else "user"
        messages.append(_message_dict(role, ep.content))
    return messages


def _chronological_sort_messages(
    messages: list[dict[str, str]],
    *,
    system_first: list[dict[str, str]],
    turn_messages: list[tuple[int, dict[str, str]]],
) -> list[dict[str, str]]:
    """System blocks first, then turns by chronological index."""
    out = list(system_first)
    for _, msg in sorted(turn_messages, key=lambda x: x[0]):
        out.append(msg)
    return out


async def pack(
    db: Session,
    user_id: int,
    session_id: str,
    current_query: str,
    enhanced: EnhancedContext | None = None,
) -> PackResult:
    """Assemble completion messages within PROMPT_TOKEN_BUDGET."""
    if not settings.selective_context_enabled:
        msgs = _build_legacy_messages(db, user_id, session_id, enhanced)
        attr = PackAttribution(
            packer={
                "budget": settings.prompt_token_budget,
                "tokens_used": _tokens_for_messages(msgs),
                "evictions": 0,
                "selective_context_enabled": False,
            }
        )
        _last_pack_attribution[_session_key(user_id, session_id)] = attr
        return PackResult(messages=msgs, attribution=attr)

    budget = settings.prompt_token_budget
    from app.services.context_manager import (
        format_enhanced_system_blocks,
        latest_memory_episode,
    )

    timeline = load_active_timeline(db, user_id, session_id)
    episode_by_id = {ep.id: ep for ep in timeline}

    retrieval: ActiveRetrievalResult = await retrieve_active_turns(
        db, user_id, session_id, current_query
    )

    system_blocks: list[dict[str, str]] = [
        _message_dict("system", settings.chat_system_prompt),
    ]
    if enhanced is not None:
        system_blocks.extend(format_enhanced_system_blocks(enhanced))

    memory = latest_memory_episode(db, user_id, session_id)
    summary_block: dict[str, str] | None = None
    if memory:
        summary_block = _message_dict(
            "system", f"Compressed context:\n{memory.content}"
        )

    floor_n = max(1, settings.active_retrieval_floor_turns)
    floor_eps = timeline[-floor_n:] if timeline else []
    floor_turns: list[tuple[int, dict[str, str]]] = []
    for i, ep in enumerate(timeline):
        if ep.id not in {e.id for e in floor_eps}:
            continue
        role = ep.role if ep.role in ("user", "assistant", "system") else "user"
        floor_turns.append((i, _message_dict(role, ep.content)))

    selected_turns: list[tuple[int, RankedActiveTurn, dict[str, str]]] = []
    for turn in retrieval.selected:
        content = _episode_content_for_turn(turn, episode_by_id)
        role = turn.role
        selected_turns.append(
            (turn.chronological_index, turn, _message_dict(role, content))
        )

    evictions = 0

    def assemble(
        *,
        include_summary: bool,
        selected: list[tuple[int, RankedActiveTurn, dict[str, str]]],
        floor: list[tuple[int, dict[str, str]]],
        truncate_floor_oldest_tokens: int | None = None,
    ) -> list[dict[str, str]]:
        sys_first = list(system_blocks)
        if include_summary and summary_block:
            sys_first.append(summary_block)
        turn_msgs: list[tuple[int, dict[str, str]]] = []
        for idx, _, msg in selected:
            turn_msgs.append((idx, msg))
        floor_copy = list(floor)
        if truncate_floor_oldest_tokens and floor_copy:
            oldest_j = min(range(len(floor_copy)), key=lambda j: floor_copy[j][0])
            fi, fm = floor_copy[oldest_j]
            floor_copy[oldest_j] = (
                fi,
                _message_dict(
                    fm["role"],
                    _truncate_content(fm["content"], truncate_floor_oldest_tokens),
                ),
            )
        for idx, msg in floor_copy:
            turn_msgs.append((idx, msg))
        return _chronological_sort_messages(
            [], system_first=sys_first, turn_messages=turn_msgs
        )

    messages = assemble(
        include_summary=True,
        selected=selected_turns,
        floor=floor_turns,
    )
    tokens_used = _tokens_for_messages(messages)

    # Eviction order: tier 4 (lowest score), tier 5 (in-session in enhanced - handled in system blocks), tier 1 summary
    while tokens_used > budget and selected_turns:
        selected_turns.sort(key=lambda x: x[1].score)
        selected_turns.pop(0)
        evictions += 1
        messages = assemble(
            include_summary=True,
            selected=selected_turns,
            floor=floor_turns,
        )
        tokens_used = _tokens_for_messages(messages)

    if tokens_used > budget and summary_block:
        messages = assemble(
            include_summary=False,
            selected=selected_turns,
            floor=floor_turns,
        )
        tokens_used = _tokens_for_messages(messages)
        evictions += 1

    if tokens_used > budget and floor_turns:
        # Truncate oldest floor message progressively
        for cap in (2000, 1500, 1000, 500, 300):
            messages = assemble(
                include_summary=summary_block is not None and tokens_used > budget,
                selected=selected_turns,
                floor=floor_turns,
                truncate_floor_oldest_tokens=cap,
            )
            tokens_used = _tokens_for_messages(messages)
            if tokens_used <= budget:
                break

    if tokens_used > budget:
        messages = assemble(
            include_summary=False,
            selected=[],
            floor=floor_turns,
            truncate_floor_oldest_tokens=400,
        )
        tokens_used = _tokens_for_messages(messages)

    attr = PackAttribution(
        active_turns_selected=[
            {
                "episode_id": t.display_episode_id,
                "score": round(t.score, 4),
                "reason": t.reason,
            }
            for _, t, _ in sorted(
                selected_turns, key=lambda x: (-x[1].score, x[0])
            )
        ],
        active_turns_floor=list(retrieval.floor_episode_ids),
        packer={
            "budget": budget,
            "tokens_used": tokens_used,
            "evictions": evictions,
            "selective_context_enabled": True,
        },
        active_retrieval_degraded=retrieval.degraded,
    )
    _last_pack_attribution[_session_key(user_id, session_id)] = attr
    return PackResult(
        messages=messages,
        attribution=attr,
        episode_by_id=episode_by_id,
    )


def pack_sync(
    db: Session,
    user_id: int,
    session_id: str,
    current_query: str,
    enhanced: EnhancedContext | None = None,
) -> PackResult:
    """Synchronous wrapper for pack (runs async retrieval in loop)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            pack(db, user_id, session_id, current_query, enhanced=enhanced)
        )
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            asyncio.run,
            pack(db, user_id, session_id, current_query, enhanced=enhanced),
        )
        return future.result()
