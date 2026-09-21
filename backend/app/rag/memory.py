"""Conversation memory — a rolling summary, rewritten after every turn.

The shape is: answer the question through the normal pipeline, then hand
(previous summary + this question + this answer) to a small model and store the
short summary it returns. Next turn, that summary is the context. The
conversation stays one paragraph however long it runs.

Summarisation uses Gemini 3.5 Flash Lite on a **second** API key
(`GEMINI_API_KEY_2`), because it runs on every turn and should not compete for
quota with anything else.

**The risk this design has, and what is done about it.** A rolling summary is
lossy by construction and the loss compounds: turn 5's summary is a summary of a
summary of a summary. For ordinary chat that is fine. Here it is not, because
the thing most likely to be dropped or mangled is a **section number**, and a
follow-up that inherits "s.303" when the answer said "s.302" is a confidently
wrong citation with nothing downstream able to catch it.

So the memory carries two things, not one:

    summary   prose, from the model — good at fact patterns, "the user was
              asked about a night-time theft from a shop"
    sections  citation keys, copied by CODE from the answer — never written by
              the summariser, so they cannot be reworded, merged or invented

The prompt also tells the model to keep numbers verbatim, but that is a request.
The `sections` list is the guarantee.

Never raises: a summarisation outage costs the conversation its memory, which
degrades follow-ups to single-turn behaviour. It must not cost the user an
answer they already have.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

MODEL = os.getenv("RAG_MEMORY_MODEL", "gemini-3.5-flash-lite")
KEY_ENV = "GEMINI_API_KEY_2"

# Hard ceiling on what goes back into a prompt. The whole point of a rolling
# summary is a bounded context; without a cap the model slowly writes a longer
# summary each turn and the bound is fiction.
MAX_SUMMARY_CHARS = int(os.getenv("RAG_MEMORY_CHARS", "700"))
MAX_SECTIONS = 12

SYSTEM = (
    "You maintain a running summary of a conversation between a user and a "
    "Bangladesh criminal law assistant. You never answer legal questions and "
    "never add law of your own.\n\n"
    "You are given the previous summary (possibly empty), the newest question, "
    "and the assistant's newest answer. Return ONE updated summary that "
    "replaces the previous one.\n\n"
    "Rules:\n"
    "1. Keep it under 90 words. It is a memory aid, not a transcript.\n"
    "2. Copy every section number and Act name EXACTLY as written — '302' must "
    "never become '303', 'Penal Code' must never become 'Criminal Code'. These "
    "are the load-bearing details; prose can be lost, numbers cannot.\n"
    "3. Keep the FACTS of any scenario the user described (who did what, when, "
    "to whom). A later question may change one fact and ask again, and the "
    "facts are what makes that resolvable.\n"
    "4. Keep what the user is trying to find out, and anything they clarified.\n"
    "5. Drop greetings, disclaimers, and the assistant's reasoning. Keep the "
    "conclusion, not the argument.\n"
    "6. Write plain third-person prose: 'The user described... The assistant "
    "cited...'. No bullet points, no headings."
)

SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
}


@dataclass
class Memory:
    """What the next turn is allowed to know about the previous ones."""

    summary: str = ""
    # Citation keys from answers so far, newest last. Written by code from the
    # answer's own sources — never parsed back out of the summary prose.
    sections: list[str] = field(default_factory=list)
    turns: int = 0
    # Set when summarisation failed, so the caller can see that memory stopped
    # updating instead of silently going stale.
    error: str | None = None

    @property
    def empty(self) -> bool:
        return not self.summary and not self.sections

    def as_prompt(self) -> str:
        """The context block handed to the planner. Empty when there is none."""
        if self.empty:
            return ""
        parts = [f"Conversation so far: {self.summary}"] if self.summary else []
        if self.sections:
            parts.append("Sections already cited: " + ", ".join(self.sections))
        return "\n".join(parts)

    def to_dict(self) -> dict:
        return {"summary": self.summary, "sections": self.sections,
                "turns": self.turns, "error": self.error}

    @classmethod
    def from_dict(cls, raw: dict | None) -> "Memory":
        if not raw:
            return cls()
        return cls(
            summary=str(raw.get("summary") or "")[:MAX_SUMMARY_CHARS],
            sections=[str(s) for s in (raw.get("sections") or [])][-MAX_SECTIONS:],
            turns=int(raw.get("turns") or 0),
        )


def _llm():
    from app.rag.llm import GeminiLLM

    return GeminiLLM(MODEL, key_env=KEY_ENV)


def available() -> bool:
    try:
        _llm()
        return True
    except Exception:
        return False


def answer_text(answer) -> str:
    """What the assistant actually told the user, as plain text.

    Built from the structured answer rather than from rendered prose so it
    carries the claims and their sections — which is what a follow-up needs —
    and none of the presentation.
    """
    bits: list[str] = []
    if getattr(answer, "summary", ""):
        bits.append(answer.summary)
    for claim in (getattr(answer, "claims", None) or [])[:8]:
        sec = claim.get("section")
        text = claim.get("text", "")
        bits.append(f"[s.{sec}] {text}" if sec else text)
    return "\n".join(b for b in bits if b)[:4000]


def failed(answer) -> bool:
    """Did this turn produce no answer?

    A refusal, a clarification and an out-of-scope reply are all "the question
    was not answered". Their *reasons* are worth showing the reader once, and
    worth nothing afterwards.
    """
    return bool(getattr(answer, "refused", False))


def update(memory: Memory | None, question: str, answer) -> Memory:
    """Fold one turn into the rolling summary. Never raises.

    Returns the previous memory untouched on failure, with `error` set, rather
    than an empty one: stale memory is better than none, and the caller can see
    that it stopped moving.
    """
    prev = memory or Memory()

    # Section keys are copied here, by code, from the answer's own sources — the
    # part of the memory that is not allowed to be a paraphrase.
    #
    # Not on a failed turn, though. A refusal still carries `sources`: they are
    # the candidates that were considered and REJECTED. Folding those into a
    # list labelled "sections cited so far" made the memory assert the opposite
    # of what happened, and a later turn would inherit provisions the assistant
    # had explicitly declined to rely on.
    keys = list(prev.sections)
    if not failed(answer):
        for src in (getattr(answer, "sources", None) or []):
            key = getattr(src, "key", None)
            if key and key not in keys:
                keys.append(key)
    keys = keys[-MAX_SECTIONS:]

    said = answer_text(answer)
    if not said and not question.strip():
        return Memory(summary=prev.summary, sections=keys, turns=prev.turns)

    # On a failed turn the QUESTION is still recorded and the failure is not.
    #
    # Dropping the turn entirely would be wrong in a way that is easy to miss:
    # the facts a follow-up needs live in the question, not the answer. "Three
    # men beat a man and he died" was refused, and it is precisely that refused
    # turn which makes "what if he had survived?" resolvable one turn later.
    #
    # What is thrown away is the assistant's narration of its own failure.
    # Recorded, it crowds out the facts — three turns produced a summary that
    # was mostly "the assistant declined to answer" — and it steers later turns
    # toward declining again.
    payload = "\n\n".join(filter(None, [
        f"Previous summary:\n{prev.summary}" if prev.summary else
        "Previous summary:\n(none — this is the first turn)",
        f"New question:\n{question.strip()}",
        (f"Assistant's answer:\n{said}" if said else
         "Assistant's answer:\n(none — the question was asked but not answered. "
         "Record what the user asked and any facts they gave. Do NOT record that "
         "the assistant failed, declined or could not answer.)"),
    ]))

    from app.rag import trace

    trace.sent("memory", f"turn {prev.turns + 1}: prev {len(prev.summary)} chars "
                         f"+ Q«{question.strip()}» + A«{said[:40]}»")
    elapsed = trace.timer()
    try:
        raw = _llm().generate_json(SYSTEM, payload, SCHEMA)
        summary = (raw.get("summary") or "").strip()[:MAX_SUMMARY_CHARS]
    except Exception as exc:
        trace.step("memory", f"FAILED, kept stale  {type(exc).__name__}", elapsed())
        return Memory(summary=prev.summary, sections=keys, turns=prev.turns + 1,
                      error=f"{type(exc).__name__}: {exc}"[:160])

    if not summary:
        trace.step("memory", "summariser returned nothing", elapsed())
        return Memory(summary=prev.summary, sections=keys, turns=prev.turns + 1,
                      error="summariser returned nothing")
    trace.recv("memory", f"«{summary}»", elapsed())
    return Memory(summary=summary, sections=keys, turns=prev.turns + 1)
