"""Several API keys for one provider, used in turn.

Groq's free tier is capped per key **and** per model: a daily token budget for
`openai/gpt-oss-120b` on one key says nothing about the same model on another
key, and nothing about `qwen/qwen3.8-27b` on the same one. Three keys therefore
multiply the budget, but only if requests actually spread across them.

Two mechanisms, and they are not the same thing:

    rotation   every call starts at the next key, so a steady stream of
               questions divides roughly evenly rather than burning key 1 to
               its daily cap while keys 2 and 3 sit untouched.
    failover   a 429 on one key is tried immediately on the next, because a
               per-minute ceiling on one key is not a per-minute ceiling on
               another. Only when every key refuses is it worth sleeping.

Discovery is by convention: `GROQ_API_KEY`, then `GROQ_API_KEY_2`,
`GROQ_API_KEY_3`, and so on until a number is missing. Adding a fourth key is an
edit to `.env.local`, not to this file. A provider with one key — Inception,
OpenRouter — produces a single slot and behaves exactly as it did before.

The rotation counter is per prefix, in memory, and shared across threads. It is
not shared across workers, which only means two workers each start their own
cycle; the spread is still far better than one key taking everything.
"""

from __future__ import annotations

import itertools
import os
import pathlib
import threading
from dataclasses import dataclass

ENV_FILE = pathlib.Path(__file__).resolve().parents[2] / ".env.local"

# How many numbered suffixes to look for past the bare name. Bounded so a typo
# in .env.local cannot turn discovery into an unbounded scan.
MAX_SLOTS = 8

_counters: dict[str, itertools.count] = {}
_lock = threading.Lock()


@dataclass(frozen=True)
class Slot:
    """One API key: the variable it came from, and its value.

    `env` is the identity used everywhere else — logs, quota records, /health.
    The value never appears in any of them.
    """

    env: str
    value: str

    def __repr__(self) -> str:      # pragma: no cover - defensive
        # A key in a traceback ends up in logs, and logs get pasted into issues.
        return f"Slot({self.env})"


def _load() -> None:
    from dotenv import load_dotenv

    load_dotenv(ENV_FILE, override=True)


def names(prefix: str) -> list[str]:
    """Env var names for `prefix` that are actually set, in declared order.

    `GROQ_API_KEY` first, then `_2`, `_3`. Numbering stops at the first gap: a
    key named `_4` with no `_3` is almost certainly a mistake, and silently
    using it would hide the missing one.
    """
    _load()
    out = []
    if os.environ.get(prefix, "").strip():
        out.append(prefix)
    for n in range(2, MAX_SLOTS + 1):
        name = f"{prefix}_{n}"
        if not os.environ.get(name, "").strip():
            break
        out.append(name)
    return out


def slots(prefix: str) -> list[Slot]:
    """Every configured key for `prefix`, in declared order."""
    _load()
    return [Slot(n, os.environ[n].strip()) for n in names(prefix)]


def count(prefix: str) -> int:
    """How many keys this provider has. 0 when none is configured."""
    return len(names(prefix))


def rotation(prefix: str) -> list[Slot]:
    """Every key, starting one further along than the last call.

    Returns all of them rather than one, because the caller needs the rest to
    fail over to. With three keys, successive calls begin at key 1, 2, 3, 1 …
    so an even stream of questions spreads evenly, and a call that has to walk
    the whole list still gets every chance before it gives up.
    """
    found = slots(prefix)
    if len(found) <= 1:
        return found
    with _lock:
        counter = _counters.get(prefix)
        if counter is None:
            counter = itertools.count()
            _counters[prefix] = counter
        i = next(counter)
    start = i % len(found)
    return found[start:] + found[:start]
