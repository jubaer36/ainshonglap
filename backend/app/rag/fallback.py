"""Record every time the app falls back from Supabase to the local files.

Supabase is where the statute text is read from. The local files — the catalog,
the FAISS index — are kept only so a Supabase outage degrades the app instead of
stopping it. A fallback is therefore always news: an answer served from the
local copy was not served from the database of record, and if a corpus update
was loaded in the meantime, the local copy does not have it.

So it is logged at WARNING — visible even with `RAG_TRACE=0`, which silences
only the per-step info lines. Throttled per source, because the catalog is
consulted many times per question and an outage would otherwise print the same
line dozens of times a request; the first use always logs, then at most one line
per source every `EVERY` seconds, with a count of the uses in between.

`active()` feeds `/health`, so "running on the fallback" is visible without
reading logs.
"""

from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger("ainshonglap")

EVERY = 30.0

_lock = threading.Lock()
_seen: dict[str, dict] = {}     # source -> {"last": ts, "since": n, "reason": str, "at": ts}


def used(source: str, reason: str) -> None:
    """The local `source` answered instead of Supabase, because of `reason`."""
    now = time.time()
    with _lock:
        s = _seen.setdefault(source, {"last": 0.0, "since": 0, "reason": "", "at": now})
        s["since"] += 1
        s["reason"] = reason
        s["at"] = now
        if now - s["last"] < EVERY:
            return
        count, s["since"], s["last"] = s["since"], 0, now
    extra = f" ({count} uses since the last notice)" if count > 1 else ""
    log.warning("FALLBACK  %s served from LOCAL files — Supabase unavailable: %s%s",
                source, reason, extra)


def recovered(source: str) -> None:
    """Supabase answered again for `source`; say so once, and stop reporting it."""
    with _lock:
        was = _seen.pop(source, None)
    if was is not None:
        log.warning("RECOVERED %s back on Supabase", source)


def active() -> dict[str, dict]:
    """Sources currently on the fallback, for /health."""
    with _lock:
        return {k: {"reason": v["reason"], "last_used": v["at"]} for k, v in _seen.items()}
