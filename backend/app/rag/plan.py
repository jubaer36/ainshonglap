"""Stage 1 of answering: a model decides what is being asked, and names where.

One small strict-JSON call. It returns a **plan**, never an answer, never
statutory text, never SQL. Code then validates every coordinate against the
catalog and executes it. The split is the point:

    model   judgement — which category, which chapter did they mean
    code    facts — what that chapter actually contains

This replaces the regex router (`refine.classify_pattern`). Patterns were fast
and stable but could only recognise phrasings someone had already thought of:
"show me the sections about documentary evidence" matched nothing and fell
through to semantic search, which answered a listing request with five ranked
guesses. A model reads intent instead of spelling.

Four categories, because they need four different machines:

    meta        about the assistant -> answered from the catalog manifest,
                no retrieval, no second model call
    structural  a named part of a statute -> SQL by coordinates. Exact, ordered,
                complete. Semantic ranking is the wrong tool once the reader has
                named what they want.
    semantic    a situation needing provisions found -> dense retrieval, rerank,
                threshold. "Someone broke into my shop at night."
    out_of_scope  a legal question about law this corpus does not contain ->
                say so, name what is covered, cite nothing
    general     not a legal question at all -> answered plainly, with no
                citations and no pretence of grounding

`out_of_scope` is separate from `general` because collapsing them is dangerous
in one direction only. "What does the Dowry Prohibition Act say?" answered
plainly is an ungrounded legal answer from parametric memory — the failure E1
recorded, where the same model placed Evidence Act ss.25-27 in the CrPC. "What
is the capital of France?" answered plainly is fine. Same category, opposite
consequences, so they are not the same category.

Reliability comes from the schema and the validator, not from trusting the
model. A hallucinated chapter is caught because the catalog is asked whether it
exists; a section number belonging to three Acts becomes a clarification rather
than a guess. The planner cannot cause a wrong answer to be *cited* — it can
only cause the wrong records to be fetched, and every coordinate it names is
checked first.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass, field

from app.rag import catalog, config, trace
from app.rag.llm import generate_with_fallback, get_llm

CATEGORIES = ("meta", "structural", "semantic", "document", "out_of_scope",
              "general")

# What a structural request wants done with the coordinates it named.
OPERATIONS = ("fetch", "enumerate", "overview", "explain")

# Asking for a named section to be EXPLAINED rather than shown. Checked in code
# as well as asked of the planner: "explain section 302" planned as `fetch`
# returns the statute verbatim, which is exactly the complaint this fixes.
EXPLAIN_RE = re.compile(
    r"\b(explain\w*|break\s*(?:it|this|that)?\s*down|breakdown|interpret\w*|"
    r"mean|meaning|simplif\w*|in\s+(?:simple|plain|easy)\s+(?:terms|words|language|english)|"
    r"elaborate|elements?\s+of|ingredients?\s+of|what\s+does\s+(?:it|this|that)\s+imply)\b",
    re.I)

SYSTEM = """\
You plan how a Bangladesh criminal law assistant should answer a question. You never answer it, never quote law, and never write SQL.

Scope: ONLY the Acts in the "Acts:" list sent with each question. That list is complete and current - a statute not on it is not held, however well known, and a statute on it is held even if you have never heard of it.

## category
- meta: asks what this assistant is or can do. "Who are you?", "what can you do", "what are your sources", "where do your answers come from", "demonstrate your capabilities".
- structural: names a PART of a statute to return - a section, a range, a chapter (by number OR by subject), or a whole Act. "What does section 302 say", "explain section 379 of the Penal Code", "list Chapter XVI", "show me the sections about documentary evidence", "summarise the Evidence Act", "state the penal code laws".
- semantic: a legal question or situation where the relevant provisions must be FOUND because the user has not named them. "What is the punishment for theft?", "Someone broke into my shop at night", "Can police arrest without a warrant?". Use for ANY scenario the listed Acts could plausibly govern - crimes, police procedure, evidence, trials, or whatever else the listed Acts cover - even when no section is named and the facts are messy.
- document: the user PASTED a document rather than asked a question - a case brief, law-report extract, judgment summary, or transcribed page from a book or screen. Signs: headings like "Facts:", "Argument:", "Held:", "Order:"; party names; a citation like "55 DLR (2003) 363"; or simply a long passage of legal prose with a short question tacked on. The work is reading the passage and explaining the provisions it refers to.
- out_of_scope: a LEGAL question about law the listed Acts do not contain - a statute that is not on the Acts list (for example the Dowry Prohibition Act or the Digital Security Act, unless the list includes them), labour, family, tax or constitutional law, another country's law, or legal advice needing a lawyer. A question that NAMES an Act on the list is never out_of_scope. "What does the Dowry Prohibition Act say?", "My employer has not paid me for three months".
- general: NOT a legal question. Two kinds:
  - factual: "What is the capital of France?", "explain vector databases", "what is 17 times 23"
  - conversation: openers, small talk, thanks, anything addressed to you as a person rather than as a search over statutes - "I need to talk to you about something, are you up for it?", "can I ask you something?", "thanks, that helped", "good morning", "how are you". These want a short natural reply. A message with no question in it is almost always this.
  Contrast with meta: "what can you do" is meta; "are you up for a question?" is general.

The semantic / out_of_scope / general line matters more than any other choice. Answering about a statute we do not have, from memory, is the worst thing this system can do. If it is legal but not plainly inside the listed Acts, choose out_of_scope.

## Fields
operation (structural only; "" otherwise):
- fetch: specific sections named, to be SHOWN ("section 302", "what does section 302 say", "sections 378 to 382")
- explain: specific sections named, to be EXPLAINED - "explain section 302", "what does section 300 mean", "break down section 420", "section 54 of the CrPC in simple terms", "what are the elements of theft under section 378"
- enumerate: list the sections of a chapter or Act
- overview: describe what a whole Act covers

acts: every Act the question names, as a list of slugs from the Acts list (the short code before each name) - one slug for one Act, two when two are named ("the first five sections of the Evidence Act AND the Code of Criminal Procedure"), [] if none is named. Never drop one because the request mentions two: listing both is exactly what was asked for.

chapter: the chapter number given ("XVI", "16"), or, if the chapter was described by subject, those subject words copied from the question ("the murder chapter" -> "murder"; "sections about documentary evidence" -> "documentary evidence"). "" if not chapter-scoped. Never invent a chapter number.

sections: section numbers explicitly named, e.g. ["302","379"]; empty otherwise. NEVER guess - leave it empty and let retrieval find it. This matters MOST for document: a pasted brief names its provisions ("Section 54 of the CrPC", "Sections 54 and 167"); read the whole passage and put every one here.

section_from / section_to: both set only for a range like "378 to 382".

search_query (semantic only; "" otherwise): the question restated in statutory vocabulary so a search over statutory text finds it. "broke into a house" -> "house-breaking"; "lied under oath" -> "giving false evidence". Keep the substance exactly, add no element the question did not contain, never include a section number.

terms (semantic only; empty otherwise): 2-6 further statutory phrases a relevant section would contain.

follow_up: true ONLY when the question cannot stand alone and refers back to the conversation: "that section", "what if he had survived instead", "and for a minor?", "the next chapter". A self-contained question is NOT a follow-up, even on the same topic. When in doubt, false - inheriting the wrong context answers a question the user did not ask.

resolved_question (only when follow_up is true; "" otherwise): the question rewritten to stand alone using the conversation summary. Carry the facts over; change only what the new question changes. E.g. "what if he had survived", with a summary about men who beat a man to death -> "what offence is committed if a group beats a man who survives with injuries".
IMPORTANT: when follow_up is true, fill every other field from resolved_question, NOT from the words the user typed. "the section before it", with a summary about section 302, resolves to section 301 -> sections ["301"], not empty. Coordinates still describing the original wording get executed as the wrong request.

needs_clarification: true when the request cannot be executed as asked - a subjective selection ("the most important laws": importance depends on purpose), or a reference to something earlier that the conversation summary does not contain. If the summary DOES resolve it, set follow_up instead and leave this false. Put the question to ask in clarification. Otherwise false and "". Do NOT set it merely because a question is broad; breadth is what semantic handles.
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": list(CATEGORIES)},
        # "none" rather than "": Gemini rejects an empty string as an enum
        # member outright ("enum[3]: cannot be empty"), while Groq and OpenAI
        # accept it. One schema has to satisfy every provider, so the absent
        # case is spelled.
        "operation": {"type": "string", "enum": [*OPERATIONS, "none"]},
        "acts": {"type": "array", "items": {"type": "string"}},
        "chapter": {"type": "string"},
        "sections": {"type": "array", "items": {"type": "string"}},
        "section_from": {"type": "string"},
        "section_to": {"type": "string"},
        "search_query": {"type": "string"},
        "terms": {"type": "array", "items": {"type": "string"}},
        "needs_clarification": {"type": "boolean"},
        "clarification": {"type": "string"},
        "follow_up": {"type": "boolean"},
        "resolved_question": {"type": "string"},
    },
    "required": ["category", "operation", "acts", "chapter", "sections",
                 "section_from", "section_to", "search_query", "terms",
                 "needs_clarification", "clarification",
                 "follow_up", "resolved_question"],
}


@dataclass
class Plan:
    """A validated instruction to the executors. Coordinates only."""

    question: str
    category: str = "semantic"
    operation: str = ""
    # Every Act the question named. `act` is the first of them, kept because
    # most coordinates concern one Act and every consumer but the structural
    # fetch reads it: on a semantic plan it is a hint, on an overview it is the
    # subject. `acts` is what a request naming two Acts needs, and dropping the
    # second was answering a different question — quietly, which is worse.
    acts: list[str] = field(default_factory=list)
    act: str | None = None
    chapter: str | None = None
    sections: list[str] = field(default_factory=list)
    section_from: str | None = None
    section_to: str | None = None
    search_query: str = ""
    terms: list[str] = field(default_factory=list)
    needs_clarification: bool = False
    clarification: str = ""
    # Conversation memory. `follow_up` is the planner's explicit judgement that
    # the question leans on earlier turns; `resolved_question` is it rewritten
    # to stand alone. Everything downstream consumes the resolved form, so no
    # executor needs to know a conversation happened.
    follow_up: bool = False
    resolved_question: str = ""
    by: str = "llm"                       # "llm" | "fallback"
    error: str | None = None
    notes: list[str] = field(default_factory=list)   # what validation changed

    @property
    def query(self) -> str:
        """What to embed for a semantic plan.

        The original question is kept alongside the restatement rather than
        replaced: a rewrite that drops a distinguishing detail would otherwise
        lose a provision the plain question would have found.
        """
        base = self.asked
        if self.category != "semantic" or not self.search_query:
            return base
        return "\n".join([base, self.search_query, " ".join(self.terms)]).strip()

    @property
    def asked(self) -> str:
        """The question to actually execute.

        A follow-up is executed as its standalone rewrite; everything else is
        executed as typed. `question` always stays what the user really said,
        so the answer can be shown against their words rather than ours.
        """
        if self.follow_up and self.resolved_question:
            return self.resolved_question
        return self.question

    @property
    def has_scope(self) -> bool:
        return bool(self.act or self.chapter or self.sections or self.section_from)


@functools.lru_cache(maxsize=1)
def _manifest_prompt() -> str:
    """The Act list, shown to the planner on every call.

    Deliberately small. The full chapter listing is 76 lines — about 1160 of the
    1870 tokens an early version sent per question, and it exhausted Groq's
    input-tokens-per-minute allowance after three calls. It is now fetched only
    for the minority of questions that describe a chapter by subject, by
    `resolve_chapter()` below.
    """
    m = catalog.manifest()
    head = "\n".join(
        f"  {a['slug']:9} {a['name']} — {a['chapters']} chapters, "
        f"{a['sections']} sections" for a in m["acts"])
    return f"Acts:\n{head}"


# Second tier: turning "documentary evidence" into "V". Only runs when the first
# call returned a chapter that is not a number, so most questions never pay for
# it. The model picks from the Act's real chapter list and the validator checks
# the answer against the catalog afterwards regardless.
CHAPTER_SYSTEM = (
    "You match a description to one chapter of a statute. You never answer the "
    "question and never quote law.\n\n"
    "Given a description and a list of real chapters, return the chapter number "
    "EXACTLY as the list prints it. Judge by what the chapter is about: "
    "'murder' -> the chapter on offences affecting the human body.\n\n"
    "If no chapter clearly matches, return '' — a wrong chapter is worse than "
    "none, because the reader is shown the wrong law with no sign anything went "
    "wrong."
)

CHAPTER_SCHEMA = {
    "type": "object",
    "properties": {"act": {"type": "string"}, "chapter": {"type": "string"}},
    "required": ["act", "chapter"],
}


def _is_number(text: str) -> bool:
    """'XVI' or '16' — something the catalog can look up without a model."""
    s = str(text or "").strip().upper()
    return bool(s) and (s.isdigit() or catalog.roman_to_int(s) is not None)


def resolve_chapter(description: str, act: str | None, *,
                    backend: str | None = None, model: str | None = None
                    ) -> tuple[str | None, str | None, str]:
    """(act, chapter, how) for a chapter named by subject rather than number."""
    from app.rag import config

    listing = catalog.chapter_listing(act)
    payload = (f"Description: {description}\n\nChapters:\n{listing}")
    try:
        raw, _ = generate_with_fallback(backend, model or config.PLANNER_MODEL,
                                        CHAPTER_SYSTEM, payload, CHAPTER_SCHEMA,
                                        job="chapter lookup")
    except Exception:
        return act, None, "chapter-lookup-failed"
    got_act = _clean(raw.get("act")).lower() or act
    if got_act not in catalog.act_slugs():
        got_act = act
    return got_act, (_clean(raw.get("chapter")).upper() or None), "chapter-lookup"


def _clean(s: object) -> str:
    return str(s or "").strip()


def validate(plan: Plan) -> Plan:
    """Check every coordinate against the catalog. Mutates and returns `plan`.

    Rules, in the order they matter:

    1. An Act the catalog does not have is dropped, not corrected.
    2. A chapter that does not exist under the named Act is dropped, and a note
       records it. Executing it would return nothing, which is the right
       outcome, but the reader deserves to be told the chapter was not found.
    3. A section number present in more than one Act, with no Act named, is a
       clarification — not a guess. All three Acts have a section 25.
    4. A named section that exists in no Act at all is dropped with a note.
    5. A structural plan left with no coordinates cannot be executed; it becomes
       a clarification rather than silently falling through to semantic search,
       which is how "list Chapter XVI" used to return five ranked guesses.
    """
    slugs = catalog.act_slugs()

    if plan.category not in CATEGORIES:
        plan.notes.append(f"unknown category {plan.category!r} -> semantic")
        plan.category = "semantic"
    if plan.operation not in OPERATIONS:
        plan.operation = ""
    # Named sections plus "explain / what does it mean / break down": explain,
    # whatever the planner chose. Showing the text verbatim to someone who
    # asked what it means answers a different question.
    if (plan.category == "structural" and plan.operation in ("fetch", "")
            and (plan.sections or plan.section_from) and EXPLAIN_RE.search(plan.asked)):
        plan.operation = "explain"
        plan.notes.append("asked to explain named sections -> explain")

    # A question that NAMES an Act we hold is never out of scope. The prompt
    # says so, but a prompt is a request: the fallback planner (gpt-oss-20b)
    # sent "What does section 13 of the Arms Act say?" to out_of_scope, which
    # answers "that Act is not held" about an Act that is. Enforced here, from
    # the catalog's own Act list, whichever planner produced the plan.
    if plan.category == "out_of_scope":
        from app.rag import acts as held

        named = [a for a in held.mentioned(plan.asked) if a in slugs]
        if named:
            number = _section_number(plan.asked)
            plan.acts = plan.acts or named
            if number:
                plan.category, plan.operation = "structural", "fetch"
                plan.sections = plan.sections or [number]
            else:
                plan.category = "semantic"
                plan.search_query = plan.search_query or plan.asked
            plan.notes.append(f"out_of_scope overruled: names held Act "
                              f"{', '.join(named)} -> {plan.category}")

    unknown = [a for a in plan.acts if a not in slugs]
    if unknown:
        plan.notes.append("no such act " + ", ".join(repr(a) for a in unknown))
    # Deduplicated, because "the Evidence Act and the Evidence Act 1872" is one
    # Act asked for twice and would otherwise be fetched twice.
    seen: list[str] = []
    for a in plan.acts:
        if a in slugs and a not in seen:
            seen.append(a)
    plan.acts = seen
    plan.act = plan.acts[0] if plan.acts else None

    if plan.chapter:
        if not plan.act:
            # A chapter number without an Act is ambiguous: every Act has a
            # Chapter I. Resolve only if exactly one Act has a chapter by that
            # number AND the model named no act — otherwise ask.
            hits = [a for a in slugs if catalog.chapter(a, plan.chapter)]
            if len(hits) == 1:
                plan.act = hits[0]
                plan.acts = [hits[0]]
                plan.notes.append(f"chapter {plan.chapter} only in {hits[0]}")
            elif len(hits) > 1:
                plan.needs_clarification = True
                plan.clarification = (
                    f"Chapter {plan.chapter} exists in "
                    f"{', '.join(catalog.act(a)['name'] for a in sorted(hits))}"
                    " — which Act did you mean?")
                return plan
            else:
                plan.notes.append(f"no chapter {plan.chapter!r} in any act")
                plan.chapter = None
        elif not catalog.chapter(plan.act, plan.chapter):
            plan.notes.append(
                f"no chapter {plan.chapter!r} in {plan.act}")
            plan.chapter = None

    # Canonicalise to the Act's own numbering. Readers write "chapter 16"; the
    # Acts print "XVI". The catalog matches either, so this changes no result —
    # but a plan that carries the reader's spelling leaks it into the answer
    # heading and into every comparison downstream, where 'XVI' != '16'.
    if plan.chapter and plan.act:
        found = catalog.chapter(plan.act, plan.chapter)
        if found and found["number"] and found["number"] != plan.chapter:
            plan.notes.append(f"chapter {plan.chapter} -> {found['number']}")
            plan.chapter = found["number"]

    kept: list[str] = []
    for num in plan.sections:
        found = catalog.find_number(num)
        if plan.act:
            if any(r["act_slug"] == plan.act for r in found):
                kept.append(num)
            else:
                plan.notes.append(f"no section {num} in {plan.act}")
            continue
        acts_with = {r["act_slug"] for r in found}
        if not acts_with:
            plan.notes.append(f"no section {num} in any act")
        elif len(acts_with) == 1:
            plan.act = next(iter(acts_with))
            kept.append(num)
        else:
            plan.needs_clarification = True
            plan.clarification = (
                f"Section {num} exists in "
                + ", ".join(sorted(r["act"] for r in found))
                + " — which Act did you mean?")
            return plan
    plan.sections = kept

    if plan.section_from or plan.section_to:
        if not (plan.section_from and plan.section_to):
            plan.notes.append("half a range given; ignoring it")
            plan.section_from = plan.section_to = None
        elif not plan.act:
            plan.needs_clarification = True
            plan.clarification = (
                f"Sections {plan.section_from} to {plan.section_to} — which Act?")
            return plan

    if plan.category == "structural" and not plan.has_scope:
        plan.needs_clarification = True
        plan.clarification = plan.clarification or (
            "Which Act, chapter or section would you like?")

    # A section number in the search query drags retrieval toward whatever that
    # number happens to be. The model is told not to; it sometimes does anyway.
    plan.terms = [t for t in plan.terms if not _looks_like_citation(t)][:6]
    return plan


def _section_number(text: str) -> str | None:
    """'section 13', 's.13', 'sec 302A' -> the number; None if none is named."""
    import re
    m = re.search(r"\b(?:sections?|secs?\.?|s\.)\s*(\d{1,3}[A-Za-z]?)\b", text or "", re.I)
    return m.group(1).upper() if m else None


def _looks_like_citation(text: str) -> bool:
    import re
    return bool(re.search(r"\b(?:s\.?|sec\.?|section)\s*\d", text, re.I))


def _schema() -> dict:
    """The planner's JSON schema, with `acts` limited to the Acts actually held.

    Built per call from the catalog, so a loaded Act is a valid answer and a
    removed one is not — the model cannot name an Act that does not exist,
    rather than the validator catching it afterwards. Falls back to the plain
    schema if the catalog cannot be read, and the validator still checks.
    """
    try:
        slugs = sorted(catalog.act_slugs())
    except Exception:                                # pragma: no cover - defensive
        return SCHEMA
    if not slugs:
        return SCHEMA
    props = dict(SCHEMA["properties"])
    props["acts"] = {"type": "array", "items": {"type": "string", "enum": slugs}}
    return {**SCHEMA, "properties": props}


def _ask_planner(backend: str | None, model: str | None,
                 payload: str) -> tuple[dict, str]:
    """Plan on the configured model, or the fallback. Returns (raw, alias used).

    A second PROVIDER rather than a second model: Groq's per-minute and daily
    limits are per-account, so retrying there waits behind the same wall. The
    planner runs before everything else on every question, so a stall here is a
    stall for the whole system — and a planner *failure* is worse than slow,
    because the question degrades to plain semantic retrieval and "who are you"
    goes to the statute index.
    """
    primary = model or config.PLANNER_MODEL
    try:
        return get_llm(backend, primary).generate_json(SYSTEM, payload, _schema()), primary
    except Exception as exc:
        fallback = config.PLANNER_FALLBACK
        if not fallback or fallback == primary:
            raise
        trace.step("planner", f"{primary} failed ({type(exc).__name__}) -> {fallback}")
        import logging
        logging.getLogger("ainshonglap").warning(
            "FALLBACK  planner: %s failed (%s); using %s", primary, type(exc).__name__, fallback)
        return get_llm(None, fallback).generate_json(SYSTEM, payload, _schema()), fallback


def make_plan(question: str, *, backend: str | None = None,
              model: str | None = None, memory=None) -> Plan:
    """Plan one question. Never raises.

    A planner outage degrades to semantic retrieval on the raw question — the
    behaviour the system had before planning existed — rather than taking the
    assistant down or refusing an answerable question.
    """
    from app.rag import config

    q = question.strip()
    if not q:
        return Plan(question=q, category="meta", by="fallback")

    # The conversation summary, when there is one. Placed before the question
    # so the model reads the history first and the question in its light.
    context = memory.as_prompt() if memory is not None else ""
    payload = "\n\n".join(filter(None, [
        _manifest_prompt(),
        f"[conversation so far]\n{context}" if context else "",
        f"Question: {q}",
    ]))

    trace.sent("planner", f"{q}"
               + (f"   [+memory {len(context)} chars]" if context else ""))
    elapsed = trace.timer()
    try:
        raw, used = _ask_planner(backend, model, payload)
        if used != (model or config.PLANNER_MODEL):
            trace.step("planner", f"fell back to {used}")
    except Exception as exc:
        # The message, not just the class. "RuntimeError" says nothing; the
        # provider's own text says whether this was a quota wall, a bad model
        # id, or a timeout — and those need different fixes.
        trace.recv("planner", f"FAILED -> semantic fallback: {exc}", elapsed())
        return Plan(question=q, category="semantic", by="fallback",
                    error=f"{type(exc).__name__}: {exc}"[:200])

    trace.recv("planner", " ".join(
        f"{k}={v}" for k, v in raw.items()
        if v not in ("", [], None, False)), elapsed())

    plan = Plan(
        question=q,
        category=_clean(raw.get("category")).lower() or "semantic",
        # "none" is the schema's way of saying "not applicable"; downstream
        # code checks for an empty operation.
        operation=("" if _clean(raw.get("operation")).lower() in ("none", "")
                   else _clean(raw.get("operation")).lower()),
        acts=[_clean(a).lower() for a in (raw.get("acts") or []) if _clean(a)][:3],
        chapter=_clean(raw.get("chapter")).upper() or None,
        sections=[_clean(s).upper() for s in (raw.get("sections") or []) if _clean(s)][:20],
        section_from=_clean(raw.get("section_from")).upper() or None,
        section_to=_clean(raw.get("section_to")).upper() or None,
        search_query=_clean(raw.get("search_query")),
        terms=[_clean(t) for t in (raw.get("terms") or []) if _clean(t)],
        needs_clarification=bool(raw.get("needs_clarification")),
        clarification=_clean(raw.get("clarification")),
        follow_up=bool(raw.get("follow_up")),
        resolved_question=_clean(raw.get("resolved_question")),
    )
    # `act` is the first named Act, so every existing consumer keeps working
    # unchanged while `acts` carries the rest.
    plan.act = plan.acts[0] if plan.acts else None

    # A follow-up with nothing to follow is a hallucinated referent: the model
    # inventing context it was never given. Demote it rather than executing a
    # rewrite built out of nothing.
    if plan.follow_up and (memory is None or memory.empty):
        plan.notes.append("follow_up claimed with no conversation memory")
        plan.follow_up, plan.resolved_question = False, ""
    elif plan.follow_up and not plan.resolved_question:
        plan.notes.append("follow_up with no rewrite; executing as asked")
        plan.follow_up = False
    elif plan.follow_up:
        plan.notes.append(f"resolved: {plan.resolved_question}")
        # The model sometimes rewrites the question correctly and then leaves
        # the coordinates describing the ORIGINAL wording — "the section before
        # it" resolves to section 301 but `sections` comes back empty, and a
        # structural plan with no scope enumerates the whole Act instead of
        # fetching one provision. Re-plan the rewrite on its own when that
        # happens: one extra call, only in the case that is otherwise wrong.
        if plan.category == "structural" and not plan.has_scope:
            plan.notes.append("no scope from the rewrite; re-planning it alone")
            again = make_plan(plan.resolved_question, backend=backend,
                              model=model, memory=None)
            if again.has_scope or again.category != "structural":
                again.question = plan.question
                again.follow_up = True
                again.resolved_question = plan.resolved_question
                again.notes = plan.notes + again.notes
                return again
    # A restatement identical to the question is the model declining to rewrite.
    if plan.search_query.lower() == q.lower():
        plan.search_query = ""

    # The chapter came back as subject words, not a number: spend the second
    # call to turn them into one. Only then is it validated, so a description
    # that resolves to nothing leaves the plan chapter-less rather than wrong.
    if plan.chapter and not _is_number(plan.chapter):
        described = plan.chapter
        plan.act, plan.chapter, how = resolve_chapter(
            described, plan.act, backend=backend, model=model)
        plan.notes.append(
            f"{how}: {described!r} -> "
            f"{plan.chapter or 'no match'}{' in ' + plan.act if plan.act else ''}")

    plan = validate(plan)
    bits = [plan.category]
    if plan.operation:
        bits.append(plan.operation)
    for key in ("act", "chapter"):
        if getattr(plan, key, None):
            bits.append(f"{key}={getattr(plan, key)}")
    if plan.sections:
        bits.append(f"s={','.join(plan.sections[:4])}")
    if plan.follow_up:
        bits.append("follow-up")
    if plan.needs_clarification:
        bits.append("needs-clarification")
    if plan.notes:
        trace.step("validated", "; ".join(plan.notes))
    trace.step("plan", " ".join(bits))
    return plan
