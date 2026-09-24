"""Use several API keys for one provider in turn: rotate per call, and move to
the next key at once on a 429.
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
