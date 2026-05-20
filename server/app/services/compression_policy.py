"""Compression orchestration policy (Phase 2).

Sync vs background
------------------
* **Sync (pre-model)** — Runs inside ``compression_lock`` before the LLM call when
  active prompt tokens exceed ``context_threshold_tokens``. Must succeed or the
  chat request returns HTTP 503. This is the hard gate for unbounded context.
* **Background (post-assistant)** — Runs after the assistant message is persisted,
  only if tokens are *still* over threshold. If sync already brought the session
  under threshold and the new assistant reply did not push it over again, background
  work is a no-op (no second ``reduce_until_under``).

Both paths share the same per-session ``asyncio.Lock``; they never run concurrently.

Idempotency
-----------
* ``reduce_once`` returns ``False`` without writes when no messages are eligible
  to offload (guard band / min_recent_messages_to_keep).
* Concurrent requests on the same session serialize on the lock; at most one
  compression commit at a time.
* Re-running compression when already under threshold is a no-op.

Duplicate summary episodes
--------------------------
* The **prompt** includes the latest ``episode_kind=memory`` row as the
  ``Compressed context`` block (``latest_memory_episode``).
* Older same-session memory rows may also appear in ``<in_session_memory>`` when
  vector retrieval selects them (excluding the latest summary and deduped content).
* Each successful ``reduce_once`` may insert a new memory row (audit chain with
  ``prior_memory_id`` in metadata). All memory rows remain embeddable for retrieval.
* Background compression does not run when the session is already under threshold
  after the assistant turn, avoiding redundant summary rows from duplicate passes.

UI: compression_in_progress
---------------------------
True when the per-session lock is held (sync path) or a background compression
task is running for that session.
"""

from __future__ import annotations
