"""The old pattern router (used only when RAG_PLAN=0).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.rag.llm import get_llm

INTENTS = ("legal", "lookup", "summarize", "meta", "browse", "general")

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
    r"\b(list|enumerate|state (?:some|the|all)|give me (?:some|all)|show me (?:some|all)|"
    r"name some|what sections are|which sections are|"
    r"what are the (?:laws|offences|sections))\b",
    re.I,
)
# "section 302", "s.154", "CrPC 154", "penal code 379" — a citation, given.
# When the reader already names the section, semantic search is the wrong tool:
# it ranks by similarity to the *question*, which can rank the named section
# below its neighbours. Fetch it.
LOOKUP_RE = re.compile(
    r"\b(?:section|sec\.?|s\.)\s*(\d{1,3}[A-Z]{0,2})\b"
    r"|\b(?:crpc|cr\.?p\.?c\.?|penal code|evidence act)\s*(\d{1,3}[A-Z]{0,2})\b",
    re.I,
)
SUMMARIZE_RE = re.compile(
    r"\b(summari[sz]e|summary of|overview of|give me an overview|"
    r"what is (?:the )?.{0,24}(?:act|code|crpc) about|"
    r"what does (?:the )?.{0,24}(?:act|code|crpc)\s+cover)\b",
    re.I,
)
ACT_HINT_RE = re.compile(
    r"(criminal procedure|crpc|cr\.?p\.?c)|(penal)|(evidence)", re.I)

SYSTEM = (
    "You classify and rewrite questions for a Bangladesh criminal law assistant "
    "covering the Code of Criminal Procedure 1898, the Penal Code 1860, and the "
    "Evidence Act 1872.\n\n"
    "You never answer the question.\n\n"
    "intent:\n"
    "  'legal'   a specific provision of those three acts would answer it, but "
    "the user has not named one. 'What is the punishment for theft?', 'Can police "
    "arrest without a warrant?', 'Someone broke into my shop at night'\n"
    "  'lookup'  the user NAMES a section and wants its contents. 'What does "
    "section 302 of the Penal Code say?', 'show me Evidence Act s.25'\n"
    "  'meta'    about the assistant itself, or a greeting. 'Who are you?', "
    "'hello', 'what can you do', 'demonstrate your capabilities', 'where do your "
    "answers come from'\n"
    "  'browse'  asks to LIST or ENUMERATE provisions, where no particular one "
    "is the answer. 'State the penal code laws', 'what sections are in chapter 5', "
    "'state all the sections of the Penal Code'\n"
    "  'summarize' asks what a whole Act is about or for an overview of it. "
    "'Summarise the Evidence Act', 'what does the Penal Code cover'\n"
    "  'general' any other question, not about Bangladesh criminal law. 'What is "
    "the capital of France?', 'explain vector databases', 'what is 17 times 23'\n\n"
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


def parse_citation(question: str) -> tuple[str | None, str] | None:
    """(act slug or None, section number) when the question names a section."""
    m = LOOKUP_RE.search(question)
    if not m:
        return None
    num = m.group(1) or m.group(2)
    a = ACT_HINT_RE.search(question)
    slug = None
    if a:
        slug = "crpc" if a.group(1) else "penal" if a.group(2) else "evidence"
    return slug, num.upper()


def classify_pattern(question: str) -> str | None:
    """Intent for the unambiguous cases, or None to ask the model.

    Patterns run first because they are stable and free. The model is only
    consulted where wording is genuinely ambiguous — it has already flip-flopped
    on identical phrasings between two prompt revisions of the same 2.6B model.
    """
    if META_RE.search(question):
        return "meta"
    # Summarise is checked before browse: "give me an overview of the sections
    # in the Penal Code" is a summary request that also contains list wording.
    if SUMMARIZE_RE.search(question):
        return "summarize"
    if BROWSE_RE.search(question):
        return "browse"
    if parse_citation(question):
        return "lookup"
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
    if hinted in ("meta", "browse", "lookup", "summarize"):
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


# ---------------------------------------------------------------- scope
# Structured-query extraction: the model names WHERE to look, code fetches it.
#
# Regex handles explicit citations ("section 302", "Chapter XVI") deterministically
# and free. It cannot handle "the murder chapter" or "offences against the human
# body", which name a chapter by meaning rather than by number. So the model is
# asked for coordinates — never for content — and is shown the Act's real chapter
# list so it selects from what exists instead of inventing.
#
# A hallucinated coordinate fails closed: library.select() returns nothing for a
# chapter that is not there, which surfaces as "no such chapter" rather than as
# the wrong chapter's text.

SCOPE_SYSTEM = (
    "You identify WHICH PART of a statute a request refers to. You never answer "
    "the question and never quote the law.\n\n"
    "You are given the Act's real chapter list. Return:\n"
    "  act      — the act slug, from the list given\n"
    "  chapter  — the chapter number EXACTLY as it appears in that list, or "
    "empty if the request is not chapter-scoped\n"
    "  sections — section numbers explicitly named in the request (e.g. "
    "['302','379']), otherwise empty\n\n"
    "Pick a chapter by its title when the request describes a subject rather "
    "than a number: 'the murder chapter' -> the chapter whose title covers "
    "offences against the human body. If no chapter clearly matches, leave it "
    "empty — a wrong chapter is worse than none."
)

SCOPE_SCHEMA = {
    "type": "object",
    "properties": {
        "act": {"type": "string"},
        "chapter": {"type": "string"},
        "sections": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["act", "chapter", "sections"],
}


@dataclass
class Scope:
    act: str | None = None
    chapter: str | None = None
    sections: list[str] = field(default_factory=list)
    by: str = "pattern"
    error: str | None = None

    @property
    def empty(self) -> bool:
        return not (self.act or self.chapter or self.sections)


def extract_scope(question: str, *, backend: str | None = None,
                  model: str | None = None, use_llm: bool = True) -> Scope:
    """Structural coordinates for a listing request.

    Regex first (free, stable); the model only for what regex cannot name.
    """
    from app.rag import config, library

    act = library.resolve_act(question)
    cited = parse_citation(question)
    sections = [cited[1]] if cited else []
    if cited and cited[0] and not act:
        act = cited[0]

    m = re.search(r"\b(?:chapter|chap\.?|ch\.)\s*([IVXLC]+|\d{1,2})\b",
                  question, re.I)
    chapter = m.group(1).upper() if m else None

    if chapter or sections:
        return Scope(act=act, chapter=chapter, sections=sections, by="pattern")

    # No explicit citation. Try matching the question's wording against the real
    # chapter and section titles before spending a model call — it is free,
    # stable, and measured more accurate here than the 2.6B router, which picked
    # the wrong Act for "the murder chapter" and gave up on "documentary
    # evidence" (both of which this resolves).
    if (m := library.match_chapter(question, act)):
        return Scope(act=m[0], chapter=m[1], by="title-match")
    if not use_llm:
        return Scope(act=act, by="pattern")

    # Nothing matched — ask the model, grounded in the real chapter list.
    acts = library.acts()
    if act:
        listing = [(act, library.chapters_of(act))]
    else:
        listing = [(a["slug"], library.chapters_of(a["slug"])) for a in acts]
    payload = [question, "", "Chapters available:"]
    for slug, chs in listing:
        payload.append(f"[{slug}]")
        payload += [f"  {c['chapter']} — {c['title'] or '(untitled)'}" for c in chs]

    try:
        raw = get_llm(backend, model or config.DEFAULT_ROUTER).generate_json(
            SCOPE_SYSTEM, "\n".join(payload), SCOPE_SCHEMA)
    except Exception as exc:
        return Scope(act=act, by="fallback",
                     error=f"{type(exc).__name__}: {exc}"[:160])

    got_act = (raw.get("act") or "").strip().lower() or act
    if got_act not in {a["slug"] for a in acts}:
        got_act = act
    got_ch = (raw.get("chapter") or "").strip().upper() or None
    # Verify the chapter exists before it is used; the model is not trusted to
    # have picked from the list just because it was shown one.
    if got_ch and got_act:
        known = {str(c["chapter"]).upper() for c in library.chapters_of(got_act)}
        if got_ch not in known:
            got_ch = None
    secs = [str(x).strip().upper() for x in (raw.get("sections") or []) if str(x).strip()]
    return Scope(act=got_act, chapter=got_ch, sections=secs[:20], by="llm")
