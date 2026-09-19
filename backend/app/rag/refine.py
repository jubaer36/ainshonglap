"""Route the question before retrieving, and rewrite it into statutory register.

Two jobs, one LLM call, because paying twice for one round trip is waste.

**Routing.** Not every question is a retrieval question. "So what are you" was
answered with five unrelated sections and a refusal, which is the wrong answer
to a reasonable question. Four destinations:

  legal         a provision should answer it -> rewrite, retrieve, generate
  meta          about the assistant itself -> answer directly, no retrieval
  browse        enumerate provisions -> the corpus browser, /api/sections
  out_of_scope  a real question, but not Bangladesh criminal law -> refuse

`browse` exists because "state some laws of the penal code" is an instruction to
list, not a proposition to look up: no section means "some laws", so all 556 are
equally relevant and retrieval has nothing to rank. `out_of_scope` stays a
refusal — a legal assistant answering "what is the capital of France" from
parametric memory is the failure this whole project is built to avoid.

**Rewriting.** The largest remaining retrieval failure is 28.6% of gold sections
never retrieved at all, which no reranker can reach because reranking only
reorders what was already found. The misses share a shape:

    asked     "someone broke into my shop at night and took my cash box"
    drafted   "Whoever commits theft in any building ... used as a human
               dwelling, or for the custody of property"

Rewriting attacks that mismatch from the query side. It is gated behind
`config.REFINE_QUERIES` until it moves a number on the 608-question set
(`scripts/experiment_refine.py`) — routing ships, rewriting has to earn it.

**Reliability.** Intent classification by a small model is not dependable: the
same query has been labelled `question` and `browse` by two prompt versions of
the same 2.6B model. Obvious cases are therefore matched deterministically
before any model is consulted, and the model only decides what the patterns do
not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.rag.llm import get_llm

INTENTS = ("legal", "meta", "browse", "out_of_scope")

# Deterministic fast paths. Cheaper than a model, and — more to the point —
# stable, which the model is not on exactly these phrasings.
META_RE = re.compile(
    r"^\s*(hi|hello|hey|thanks?|thank you|who are you|what are you|"
    r"what can you do|what do you do|how do you work|what is this|"
    r"who made you|what are your (sources|limits|capabilities))\b"
    r"|^\s*(so\s+)?(what|who)\s+(are|r)\s+(you|u)\b",
    re.I,
)
BROWSE_RE = re.compile(
    r"\b(list|enumerate|state some|give me some|show me some|name some|"
    r"what sections are|which sections are)\b",
    re.I,
)

SYSTEM = (
    "You classify and rewrite questions for a Bangladesh criminal law assistant "
    "covering the Code of Criminal Procedure 1898, the Penal Code 1860, and the "
    "Evidence Act 1872.\n\n"
    "You never answer the question.\n\n"
    "intent:\n"
    "  'legal'        a specific provision of those three acts would answer it. "
    "'What is the punishment for theft?', 'Can police arrest without a warrant?', "
    "'My neighbour forged my signature, what offence is that?'\n"
    "  'meta'         about the assistant itself, or a greeting. 'What are you?', "
    "'hello', 'what can you do', 'where do your answers come from'\n"
    "  'browse'       asks to LIST or ENUMERATE provisions, where no particular "
    "one is the answer. 'State some laws of the penal code', 'what sections are "
    "in chapter 5'\n"
    "  'out_of_scope' a genuine question, but not about Bangladesh criminal law. "
    "'What is the capital of France?', 'How do I register a company?'\n\n"
    "For intent 'legal' only:\n"
    "  refined — the question restated in statutory vocabulary, so a search over "
    "statutory text can find the right section. Replace everyday words with the "
    "drafting term: 'broke into a house' -> 'house-breaking', 'lied under oath' "
    "-> 'giving false evidence', 'took without permission' -> 'theft'. Keep the "
    "legal substance exactly; add no element the question did not contain.\n"
    "  terms — 2-6 further statutory phrases a relevant section would contain.\n"
    "  Never guess a section number, in either field. Retrieval finds the "
    "section; a wrong guess poisons the search.\n\n"
    "For every other intent, leave refined empty and terms empty."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": list(INTENTS)},
        "refined": {"type": "string",
                    "description": "statutory restatement; empty unless intent is 'legal'"},
        "terms": {"type": "array", "items": {"type": "string"},
                  "description": "2-6 statutory phrases; empty unless intent is 'legal'"},
    },
    "required": ["intent", "refined", "terms"],
}

# A "term" that is really a citation. The model is told not to guess section
# numbers and does it anyway; a guessed number embedded into the query is noise
# at best and drags retrieval toward the wrong section at worst.
CITATION_RE = re.compile(
    r"\b(?:s\.?|sec\.?|section|art\.?|article)\s*\d|"
    r"\b(?:crpc|cr\.?p\.?c|penal code|evidence act)\b.*\d",
    re.I,
)


def clean_terms(terms: list[str]) -> list[str]:
    return [t for t in terms if not CITATION_RE.search(t)]


@dataclass
class Refined:
    original: str
    intent: str = "legal"
    refined: str = ""
    terms: list[str] = field(default_factory=list)
    failed: bool = False
    error: str | None = None
    by: str = "pattern"          # "pattern" | "llm" | "fallback"

    @property
    def query(self) -> str:
        """What to embed.

        The original is kept alongside the rewrite rather than replaced: a
        rewrite that drops a distinguishing detail would otherwise lose a
        section the plain question would have found, so this way rewriting can
        only add recall. Whether concatenation beats the rewrite alone is a
        question for the experiment, not a conviction.
        """
        if self.intent != "legal" or not self.refined:
            return self.original
        return f"{self.original}\n{self.refined}\n{' '.join(self.terms)}".strip()


def classify_pattern(question: str) -> str | None:
    """Intent for the unambiguous cases, or None to ask the model."""
    if META_RE.search(question):
        return "meta"
    if BROWSE_RE.search(question):
        return "browse"
    return None


def refine(question: str, *, backend: str | None = None, model: str | None = None,
           rewrite: bool = True) -> Refined:
    """Route, and rewrite when the route is `legal`.

    `model` defaults to `config.DEFAULT_ROUTER`, not to the answering model.
    Classifying and rewriting is a small job that runs on every question; making
    it wait on a 60-second reasoning model would add that cost to queries the
    router is about to decline to retrieve for.

    Never raises: a router outage must degrade to plain retrieval, not take the
    assistant down with it.
    """
    from app.rag import config
    q = question.strip()
    if not q:
        return Refined(original=q, intent="meta")

    hinted = classify_pattern(q)
    if hinted in ("meta", "browse"):
        # No rewrite is needed for these, so no model call is made at all.
        return Refined(original=q, intent=hinted, by="pattern")

    try:
        raw = get_llm(backend, model or config.DEFAULT_ROUTER).generate_json(
            SYSTEM, q, SCHEMA)
    except Exception as exc:
        # Fall through to plain retrieval rather than refusing: a rate-limited
        # router should not turn an answerable legal question into "not covered".
        return Refined(original=q, intent="legal", failed=True, by="fallback",
                       error=f"{type(exc).__name__}: {exc}"[:200])

    intent = raw.get("intent")
    if intent not in INTENTS:
        intent = "legal"

    refined_text = (raw.get("refined") or "").strip()
    # A restatement identical to the input is the model declining to rewrite;
    # treat it as no rewrite so `query` does not embed the question twice.
    if refined_text.strip().lower() == q.lower():
        refined_text = ""
    terms = clean_terms([str(t).strip() for t in (raw.get("terms") or []) if str(t).strip()])

    if not rewrite:
        refined_text, terms = "", []
    return Refined(original=q, intent=intent, refined=refined_text,
                   terms=terms[:6], by="llm")
