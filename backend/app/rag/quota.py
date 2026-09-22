"""Which generators are currently out of quota, and until when.

Free tiers run out. Groq's daily token cap is per model AND per key — a 429
naming `qwen/qwen3.8-27b` says nothing about `openai/gpt-oss-20b`, which has its
own budget, and nothing about the same model on a second key — so "the provider
is down" is the wrong unit. Exhaustion is tracked per (model, key), and a model
counts as unavailable only once every key it has is blocked. That distinction is
the whole value of running more than one key: see `keyring.py`.

The answer goes to two places that need it:

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

# (model id, key slot) -> (unix time it becomes available again, why)
#
# Groq's cap is per key AND per model, so the unit of exhaustion is the pair.
# One key hitting its daily budget for gpt-oss-120b leaves the other keys' budget
# for that model untouched — recording it against the model alone would grey out
# a route that still has two thirds of its capacity left.
#
# `slot` is an env var NAME (`GROQ_API_KEY_2`), never a key value. It is None for
# providers with a single key, which keeps their records exactly as they were.
_blocked: dict[tuple[str, str | None], tuple[float, str]] = {}

# model id -> how many keys its provider has, as reported by the caller that
# last touched it. Needed because "is this model usable" means "does ANY key
# still have budget", and this module cannot see .env.local.
_fleet: dict[str, int] = {}
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


def mark_exhausted(model: str, error: str, *, slot: str | None = None,
                   fleet: int = 1) -> float:
    """Record that `model` is out of budget on `slot`. Returns when it returns.

    `fleet` is how many keys the provider has for this model. Passed in on every
    call rather than configured here, so that adding a key to `.env.local` takes
    effect without this module knowing the provider exists.
    """
    if not model:
        return 0.0
    until = time.time() + parse_cooldown(error)
    with _lock:
        _blocked[(model, slot)] = (until, _reason(error))
        _fleet[model] = max(fleet, _fleet.get(model, 1))
    return until


def _reason(error: str) -> str:
    text = (error or "").strip()
    if "tokens per day" in text.lower() or "tpd" in text.lower():
        return "daily token limit reached"
    if "rate limit" in text.lower():
        return "rate limit reached"
    return "quota exhausted"


def clear(model: str, *, slot: str | None = None) -> None:
    """A successful call proves the block is stale — drop it.

    Only for the key that succeeded: another key's daily cap is still spent, and
    clearing it here would send the next question straight back into a 429.
    """
    with _lock:
        _blocked.pop((model, slot), None)


def _live(model: str) -> dict[str | None, tuple[float, str]]:
    """Unexpired blocks for `model`, by slot. Prunes as it goes. Caller holds the lock."""
    now = time.time()
    out = {}
    for (m, slot), (until, reason) in list(_blocked.items()):
        if m != model:
            continue
        if now >= until:
            _blocked.pop((m, slot), None)
            continue
        out[slot] = (until, reason)
    return out


def status(model: str) -> dict | None:
    """None while ANY key still has budget; otherwise why, and for how long.

    A model is not unavailable because one of its keys ran dry — that is what
    the other keys are for. It is unavailable when every key it has is blocked,
    and then it comes back when the first of them does, so `seconds_remaining`
    is the soonest expiry rather than the longest.
    """
    with _lock:
        live = _live(model)
        if not live:
            return None
        fleet = max(_fleet.get(model, 1), 1)
        if len(live) < fleet:
            return None                 # at least one key is still usable
        until, reason = min(live.values(), key=lambda e: e[0])
    return {"reason": reason, "until": until,
            "seconds_remaining": int(until - time.time())}


def slot_status(model: str, slot: str | None) -> dict | None:
    """The same question for one specific key."""
    with _lock:
        entry = _live(model).get(slot)
        if not entry:
            return None
        until, reason = entry
    return {"reason": reason, "until": until,
            "seconds_remaining": int(until - time.time())}


def available(model: str) -> bool:
    return status(model) is None


def slot_available(model: str, slot: str | None) -> bool:
    return slot_status(model, slot) is None


def snapshot() -> dict[str, dict]:
    """Everything currently blocked, for /health.

    Reported per model, with the keys that are out named — one key down out of
    three is a very different operational picture from all three down, and the
    old model-keyed view could not tell them apart.
    """
    with _lock:
        models = {m for m, _ in _blocked}
    out: dict[str, dict] = {}
    for m in models:
        with _lock:
            live = _live(m)
            fleet = max(_fleet.get(m, 1), 1)
        if not live:
            continue
        until, reason = min(live.values(), key=lambda e: e[0])
        out[m] = {"reason": reason, "until": until,
                  "seconds_remaining": int(until - time.time()),
                  "keys_blocked": sorted(s or "default" for s in live),
                  "keys_total": fleet,
                  "usable": len(live) < fleet}
    return out
