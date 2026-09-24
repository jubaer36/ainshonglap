"""Log every time the local files answer instead of Supabase.
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
