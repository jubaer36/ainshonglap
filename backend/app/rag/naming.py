"""Give a conversation a short title after three answered turns.
Runs on its own model and key; a title the user typed is never overwritten.
"""

from __future__ import annotations

import os

# Its own key. `GEMINI_API_KEY_3` is naming's alone — not the planner's
# (`_2`, with the summariser), not the optional generator's (`GEMINI_API_KEY`).
MODEL = os.getenv("RAG_NAME_MODEL", "gemini-3.5-flash-lite")
KEY_ENV = "GEMINI_API_KEY_3"

SYSTEM = (
    "You name conversations with a Bangladesh criminal law assistant. You are "
    "given a summary of one. Return a short title for it.\n\n"
    "Rules:\n"
    "1. Two to five words. It is a label in a sidebar, not a sentence.\n"
    "2. Name the SUBJECT, not the conversation. 'Bail for house theft', not "
    "'A discussion about bail'. No 'Conversation about', no 'Questions on'.\n"
    "   Name the WHOLE conversation, not its last turn. The summary describes "
    "the turns oldest first, and the last sentence is only the most recent "
    "question. Name what most of the summary is about; when it covers several "
    "topics, name the main LEGAL one - a conversation about prosecution that "
    "drifted to coffee is about prosecution.\n"
    "3. Describe what was ASKED ABOUT. Do not classify the offence yourself — "
    "if the user described a death, say 'death after assault', not 'murder'. "
    "Naming an offence is a legal conclusion and this is a label.\n"
    "4. No quotation marks, no trailing full stop.\n"
    "5. Plain words a person would recognise at a glance. Keep an Act or "
    "section number only when it is what the conversation was actually about.\n"
    "6. If the summary is too thin to name, return an empty string."
)

SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string"}},
    "required": ["title"],
}

# Naming waits for this many turns that actually produced an answer. Refusals do
# not count: a conversation of three refusals has no subject to name, and naming
# it would describe the questions rather than the law.
AFTER_ANSWERS = int(os.getenv("RAG_NAME_AFTER", "3"))

MAX_CHARS = 60

# Stripped off whatever comes back. The model is asked for no quotes and no
# trailing stop, but a title is a label and a prompt rule is a request.
TRIM = "\"'“”. "


def _clean(raw: dict | None) -> str:
    return " ".join(((raw or {}).get("title") or "").split()).strip(TRIM).strip()


def _llm():
    from app.rag.llm import GeminiLLM

    return GeminiLLM(MODEL, key_env=KEY_ENV)


def available() -> bool:
    """Is the naming key configured? Absent, conversations keep their first question."""
    try:
        _llm()
        return True
    except Exception:
        return False


def earned(memory) -> bool:
    """Has this conversation had enough answered turns to deserve a name?

    `>=` rather than `==`: the caller decides *when* to ask, and a client that
    asks a turn late should still get a name rather than having missed its one
    chance. What stops it running twice is the title guard in the store, not a
    count that has to be hit exactly.
    """
    return bool(memory) and getattr(memory, "answered", 0) >= AFTER_ANSWERS


class Unavailable(RuntimeError):
    """The naming model could not be reached — try again on a later turn."""


# Gemini answers 503 "high demand" and 429 in bursts. A couple of short waits
# ride out a spike; beyond that the caller simply tries again next turn.
RETRIES = 2
RETRY_WAIT = 2.0


def _busy(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(t in text for t in ("503", "unavailable", "429", "resource_exhausted",
                                   "high demand", "overloaded"))


def suggest(summary: str) -> str:
    """A short title for a conversation, or "" when there is nothing to name.

    Raises `Unavailable` when the model could not be reached. That used to come
    back as "" too — indistinguishable from "nothing to name" — so a busy model
    silently cost a conversation its one chance at a name.
    """
    import time

    summary = (summary or "").strip()
    if not summary:
        return ""

    from app.rag import trace

    trace.sent("name", f"{len(summary)} chars of summary")
    elapsed = trace.timer()
    raw, failure = None, None
    for attempt in range(RETRIES + 1):
        try:
            raw = _llm().generate_json(SYSTEM, summary, SCHEMA)
            break
        except Exception as exc:
            failure = exc
            if _busy(exc) and attempt < RETRIES:
                trace.step("name", f"model busy, retrying ({type(exc).__name__})")
                time.sleep(RETRY_WAIT * (attempt + 1))
                continue
            break

    title = _clean(raw)
    if not title:
        # Gemini failed, or answered with nothing — both seen on real
        # conversations, the second after 60-90s. The system fallback gets one
        # try before the conversation waits for its next turn.
        import logging

        from app.rag import config
        from app.rag.llm import get_llm

        why = type(failure).__name__ if failure else "empty title"
        logging.getLogger("ainshonglap").warning(
            "FALLBACK  naming: %s gave no name (%s); using %s",
            MODEL, why, config.FALLBACK_GENERATOR)
        try:
            title = _clean(get_llm(None, config.FALLBACK_GENERATOR)
                           .generate_json(SYSTEM, summary, SCHEMA))
        except Exception as exc:
            trace.step("name", f"FAILED, will retry next turn  {type(exc).__name__}", elapsed())
            raise Unavailable(f"{type(exc).__name__}: {str(exc)[:160]}") from exc
    if not title:
        trace.step("name", "nothing usable returned", elapsed())
        return ""
    trace.recv("name", f"«{title}»", elapsed())
    return title[:MAX_CHARS]
