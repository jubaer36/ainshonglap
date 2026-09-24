"""One short log line per pipeline step (RAG_TRACE=0 turns it off).
"""

from __future__ import annotations

import logging
import os
import time

log = logging.getLogger("ainshonglap")

ENABLED = os.getenv("RAG_TRACE", "1") == "1"

# Long enough for the labels, short enough that values line up on one screen.
_WIDTH = 9


def enabled() -> bool:
    return ENABLED and log.isEnabledFor(logging.INFO)


def question(text: str, *, user: bool = False) -> None:
    """Open a request. The marker makes one exchange findable in a busy log."""
    if not enabled():
        return
    who = " ·signed-in" if user else ""
    log.info("❯ %s%s", _clip(text, 88), who)


def sent(name: str, detail: str) -> None:
    """What went out to a model or a database."""
    if not enabled():
        return
    log.info("  %-*s → %s", _WIDTH, name, _clip(detail, 92))


def recv(name: str, detail: str, seconds: float | None = None) -> None:
    """What came back. Paired with `sent`, this is the exchange itself — the
    thing worth seeing when an answer is surprising."""
    if not enabled():
        return
    timing = f"  {seconds:.1f}s" if seconds is not None else ""
    log.info("  %-*s ← %s%s", _WIDTH, name, _clip(detail, 92), timing)


def step(name: str, detail: str = "", seconds: float | None = None) -> None:
    if not enabled():
        return
    timing = f"{seconds:>6.1f}s" if seconds is not None else ""
    log.info("  %-*s %-46s %s", _WIDTH, name, _clip(detail, 46), timing)


def timer():
    """`with timer() as t:` … then `t()` for elapsed seconds."""
    start = time.perf_counter()
    return lambda: time.perf_counter() - start


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def hits(results) -> str:
    """`5 hits  penal-s380 2.72 · crpc-s4 1.30` — enough to see what was found."""
    if not results:
        return "0 hits"
    top = " · ".join(f"{r.key} {r.score:.2f}" for r in results[:3])
    return f"{len(results)} hits  {top}"
