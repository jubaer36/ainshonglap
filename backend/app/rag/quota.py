"""Which generators are currently out of quota, and until when.

Free tiers run out. Groq's daily token cap is per model — a 429 naming
`qwen/qwen3.8-27b` says nothing about `openai/gpt-oss-20b`, which has its own
budget — so "the provider is down" is the wrong unit. This tracks exhaustion per
model and hands the answer to two places that need it:

    GET /api/models   marks a model unselectable, with the reason and the time
                      it comes back, so the picker can grey it out instead of
                      offering a choice that will fail
    POST /api/query   substitutes the configured fallback rather than returning
                      a 502, and says in the response which model actually
                      answered

Held in memory, deliberately. The state is a cache of something the provider
already knows and will tell us again on the next 429; persisting it would add a
store to keep correct across restarts for no gain. The cost is that it resets on
restart and is not shared between workers — the first request after either one
pays a wasted call and then the record is back.

Only **quota** failures are recorded. A 500, a timeout or a network blip says
nothing about a model's budget, and marking it unavailable for an hour over a
transient fault would be worse than the failure.
"""

from __future__ import annotations

import re
import threading
import time

# model id -> (unix time it becomes available again, why)
_blocked: dict[str, tuple[float, str]] = {}
_lock = threading.Lock()

# Used when the provider gives no retry hint. An hour is long enough to stop a
# retry storm and short enough that a misclassification costs little.
DEFAULT_COOLDOWN = 3600.0

# "Please try again in 12m40.32s" / "try again in 45s"
_RETRY_RE = re.compile(r"try again in\s+(?:(\d+)m)?([\d.]+)s", re.I)
_QUOTA_HINTS = ("rate limit", "tokens per day", "tpd", "quota", "429",
                "resource_exhausted", "insufficient_quota")


def looks_like_quota(error: str) -> bool:
    """Is this failure about a budget, rather than a bug or a blip?"""
    text = (error or "").lower()
    return any(h in text for h in _QUOTA_HINTS)


def parse_cooldown(error: str) -> float:
    """Seconds until the provider says to retry, or the default."""
    m = _RETRY_RE.search(error or "")
    if not m:
        return DEFAULT_COOLDOWN
    minutes = float(m.group(1) or 0)
    seconds = float(m.group(2) or 0)
    # A daily cap can report a wait of many hours; trust it, but never block
    # for more than a day on one message.
    return min(minutes * 60 + seconds, 86400.0)


def mark_exhausted(model: str, error: str) -> float:
    """Record that `model` is out of budget. Returns when it comes back."""
    if not model:
        return 0.0
    until = time.time() + parse_cooldown(error)
    with _lock:
        _blocked[model] = (until, _reason(error))
    return until


def _reason(error: str) -> str:
    text = (error or "").strip()
    if "tokens per day" in text.lower() or "tpd" in text.lower():
        return "daily token limit reached"
    if "rate limit" in text.lower():
        return "rate limit reached"
    return "quota exhausted"


def clear(model: str) -> None:
    """A successful call proves the block is stale — drop it."""
    with _lock:
        _blocked.pop(model, None)


def status(model: str) -> dict | None:
    """None when available; otherwise why, and for how much longer."""
    with _lock:
        entry = _blocked.get(model)
        if not entry:
            return None
        until, reason = entry
        if time.time() >= until:
            _blocked.pop(model, None)
            return None
    return {"reason": reason, "until": until,
            "seconds_remaining": int(until - time.time())}


def available(model: str) -> bool:
    return status(model) is None


def snapshot() -> dict[str, dict]:
    """Everything currently blocked, for /health."""
    return {m: s for m in list(_blocked) if (s := status(m))}
