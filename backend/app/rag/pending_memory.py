"""Answers waiting for their memory update. The answer is sent at once; the
update runs later through POST /api/memory. Held in memory for 10 minutes.
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
