"""Naming a conversation — its own model, its own key, its own request.

The first question is a conversation's title until it stops being a good one.
Two turns in, "What is the punishment for theft?" describes the conversation.
Five turns in, having moved through bail and arrest procedure, it describes only
how the conversation opened.

**Deliberately separate from everything else.**

*From the summariser* (`memory.py`), which this could plausibly have lived in:
they share a provider and a shape, but not a schedule. Summarisation runs on
every turn and is load-bearing — a follow-up is unresolvable without it. Naming
runs once per conversation and is cosmetic. Sharing a key would let a sidebar
label eat the quota the next follow-up depends on.

*From the answer request*: naming is not on the path to an answer, so it is not
in `POST /api/query`. It has its own endpoint, called after the answer has
already been rendered. Folding it in would have put a second model call between
the reader and an answer they were already waiting for, once per conversation,
for a label they were not looking at yet.

Never raises. A conversation with a stale name is a much smaller problem than a
turn that failed to save, and every caller here treats "" as "keep what you
have".
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


def suggest(summary: str) -> str:
    """A short title for a conversation, or "" to keep the existing one."""
    summary = (summary or "").strip()
    if not summary:
        return ""

    from app.rag import trace

    trace.sent("name", f"{len(summary)} chars of summary")
    elapsed = trace.timer()
    try:
        raw = _llm().generate_json(SYSTEM, summary, SCHEMA)
    except Exception as exc:
        trace.step("name", f"FAILED, title unchanged  {type(exc).__name__}",
                   elapsed())
        return ""

    title = " ".join((raw.get("title") or "").split()).strip(TRIM).strip()
    if not title:
        trace.step("name", "nothing usable returned", elapsed())
        return ""
    trace.recv("name", f"«{title}»", elapsed())
    return title[:MAX_CHARS]
