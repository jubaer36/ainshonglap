"""Answers waiting for their context update.

The context summariser used to run inside `POST /api/query`, between the answer
being ready and the answer being sent — so every reader waited for it, and when
Gemini was slow (60-90s was observed) an answer finished in two seconds sat
behind a minute of summarising. Now the answer goes out at once and the update
is a separate request (`POST /api/memory`) that the client makes afterwards.

That request needs what the summariser needs: the question, the structured
answer (its claims, its sources, whether it was refused), and the memory the
question was asked against. The client holds a rendered message, not those, so
the query handler parks them here under the message id, and the memory request
collects them.

Held in memory, for minutes, and dropped once collected. This is processing
state, not storage: nothing here outlives a few minutes or a restart, which is
why a guest's questions can pass through it without breaking the promise that
nothing of theirs is kept. A miss — expired, or lost to a restart — is reported,
and the client carries on with the context it already had.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

TTL_SECONDS = 600
MAX_ENTRIES = 500

_lock = threading.Lock()
_pending: dict[str, "Pending"] = {}


@dataclass
class Pending:
    question: str
    answer: object            # the executors' Answer
    before: object            # the Memory the question was asked against
    created: float


def park(message_id: str, question: str, answer, before) -> None:
    """Hold one answer until its context update is asked for."""
    now = time.time()
    with _lock:
        for key in [k for k, v in _pending.items() if now - v.created > TTL_SECONDS]:
            _pending.pop(key, None)
        if len(_pending) >= MAX_ENTRIES:            # bounded, oldest out first
            oldest = min(_pending, key=lambda k: _pending[k].created)
            _pending.pop(oldest, None)
        _pending[message_id] = Pending(question, answer, before, now)


def collect(message_id: str) -> Pending | None:
    """Take the parked answer, once. None when it expired or was never parked."""
    with _lock:
        entry = _pending.pop(message_id, None)
    if entry and time.time() - entry.created > TTL_SECONDS:
        return None
    return entry
