"""Question -> grounded, cited answer, or a refusal (generation-plan.md step 12).

The design is shaped by one measured fact rather than by preference: served
retrieval is R@5 59.0%, so **41% of the time the correct section is not in the
context at all** — and what is there instead is five plausible, adjacent,
professionally drafted statutory sections. Nothing in the prompt looks broken.

That is the *misleading context* condition, which is the one prompt-based
abstention handles worst. GRAB-RAG measured models answering <=1% of the time
when evidence is simply absent but 13.6-74.3% when a fluent wrong passage is
present. So refusal here is not left to the prompt:

  ask nicely     the system prompt tells the model to refuse rather than
                 stretch a related section (necessary, not sufficient)
  make it cheap  the schema has a `sufficient` flag, so refusing is a field to
                 set rather than prose to compose
  check after    every claim must cite a retrieved section AND quote it
                 verbatim; claims that fail are dropped, and an answer with no
                 surviving claims becomes a refusal (verify.py)

The last one is the only part that does not depend on the model's own judgement,
which is why it is the part that is not optional.

The disclaimer is added by `to_message()` from a constant, never by the model.
A disclaimer the generator emits is one the generator can omit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.rag import config
from app.rag.llm import generate_with_fallback, get_llm
from app.rag.pipeline import Retrieved, retrieve
from app.rag.refine import Refined, extract_scope, parse_citation, refine
from app.rag.citations import strip_prefix
from app.rag.verify import VerifiedAnswer, check_claims

DISCLAIMER = (
    "This is legal information drawn from the text of the Acts, not legal "
    "advice. For advice on a specific situation, consult a qualified lawyer."
)

SYSTEM = (
    "You are a legal information assistant for Bangladesh criminal law. You are "
    "given numbered excerpts from the Acts it holds, each labelled with its Act "
    "and section.\n\n"
    "Rules:\n"
    "1. Answer ONLY from the provided sections. Do not use your own knowledge of "
    "Bangladeshi law, and do not reason from general legal principles.\n"
    "2. If the provided sections do not answer the question, set sufficient=false "
    "and leave claims empty. A section that is merely related is not an answer. "
    "Refusing is the correct outcome far more often than it feels like it is.\n"
    "3. Every claim must carry the section number it came from and a VERBATIM "
    "quote copied exactly from that section's text. Do not paraphrase inside the "
    "quote. Quotes are checked against the source automatically.\n"
    "4. Ambiguity is NOT insufficiency. If the question could mean two or more "
    "provisions that ARE present in the sections above, still set sufficient=true, "
    "answer every reading with its own claims, set ambiguous=true, and say in "
    "`clarification` what would pin it down. Only set sufficient=false when the "
    "answer is absent, never when it is merely split across several sections.\n"
    "5. Do not add a disclaimer; one is attached automatically.\n"
    "6. If the user asks you to explain, interpret or break down a provision - by "
    "number, or by pasting its text - do not just repeat it. Explain it element "
    "by element in plain words: what conduct it covers, each condition it "
    "requires (including any intent or knowledge), the punishment, and any "
    "Explanation, Exception or Illustration - one claim per element, each "
    "quoting the exact words that establish it.\n"
)

SCHEMA = {
    "type": "object",
    "properties": {
        "sufficient": {
            "type": "boolean",
            "description": "true only if the provided sections answer the question",
        },
        "ambiguous": {
            "type": "boolean",
            "description": "true if the question could mean two different provisions",
        },
        "clarification": {
            "type": "string",
            "description": "what to pin down, when ambiguous is true; otherwise empty",
        },
        "summary": {
            "type": "string",
            "description": "one sentence answering the question; empty if not sufficient",
        },
        "claims": {
            "type": "array",
            "description": "one per assertion; empty if not sufficient",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "the assertion, in plain English"},
                    "section": {
                        "type": "string",
                        "description": "the section number ALONE, digits only, "
                                       "e.g. '154' or '304B' — not the act name, "
                                       "not the word 'Section'",
                    },
                    "quote": {
                        "type": "string",
                        "description": "verbatim span copied from that section, checked automatically",
                    },
                },
                "required": ["text", "section", "quote"],
            },
        },
    },
    "required": ["sufficient", "ambiguous", "clarification", "summary", "claims"],
}


@dataclass
class Answer:
    question: str
    refused: bool
    reason: str | None                  # why, when refused
    # legal | meta | general | lookup | outline — and, from the planner path,
    # structural | out_of_scope | clarify. Only `legal`, `lookup` and `outline`
    # carry citations; the rest deliberately carry none.
    kind: str = "legal"
    ambiguous: bool = False
    clarification: str = ""
    summary: str = ""
    claims: list[dict] = field(default_factory=list)      # surviving claims only
    dropped: list[str] = field(default_factory=list)      # gate failures, for the log
    sources: list[Retrieved] = field(default_factory=list)
    verification: VerifiedAnswer | None = None
    model: str = ""
    refined: Refined | None = None      # None when routing is off
    # The plan this answer was executed from, when it came through the planner
    # path (app/rag/execute.py). None on the older router path.
    plan: object | None = None


def capability_answer() -> str:
    """What this assistant is, assembled from the index rather than written down.

    Deliberately not an LLM call and not a hardcoded blurb: both drift from what
    the system actually contains. Read from the corpus, the description cannot
    claim an act that is not indexed.
    """
    from app.rag import library

    try:
        acts = library.acts()
    except Exception:
        acts = []
    if not acts:
        return ("I answer questions about Bangladesh criminal law from the text of "
                "the Acts themselves. The index is currently unavailable.")
    listed = "; ".join(f"{a['name']} ({a['sections']} sections)" for a in acts)
    total = sum(a["sections"] for a in acts)
    return (
        "I answer questions about Bangladesh criminal law using only the text of "
        f"the Acts indexed here: {listed} — {total} sections in all, parsed "
        "directly from the source PDFs.\n\n"
        "Every claim I make cites a section and quotes it verbatim, and the quote "
        "is checked against the source before you see it; claims that fail that "
        "check are dropped. If the indexed Acts do not answer something, I say so "
        "rather than answering from memory. I cannot advise on your situation, "
        "cover case law or other statutes, or tell you what a court would decide."
    )


GENERAL_SYSTEM = (
    "You are the general-conversation side of a Bangladesh criminal law "
    "assistant. What you have been given is not a question about the indexed "
    "Acts, so answer it normally.\n\n"
    "If it is conversation rather than a question — an opener, small talk, "
    "thanks, 'are you up for it?' — reply the way a person would: briefly, "
    "warmly, and without ceremony. 'Yes, go ahead' is a complete answer. Do not "
    "recite what you can do, do not list the Acts you cover, and do not turn a "
    "greeting into a disclaimer.\n\n"
    "If it is an ordinary factual question, answer it briefly from your own "
    "knowledge.\n\n"
    "Either way: do NOT cite Bangladeshi statutes and do not claim the answer "
    "comes from them. The citation machinery does not run on this path, so "
    "anything cited here would be unverified."
)

GENERAL_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string", "description": "brief, direct"}},
    "required": ["answer"],
}


def general_answer(question: str, ref: Refined | None, *, backend: str | None = None,
                   model: str | None = None) -> Answer:
    """Answer an ordinary, non-legal question.

    Kept strictly apart from the legal path: no retrieval, no citations, and the
    result is marked `kind="general"` so the presenter can say where it came
    from. Refusing these was wrong — "what is 17 times 23" is not a failure of a
    legal assistant — but answering them *as though grounded* would be worse.
    """
    try:
        raw, _ = generate_with_fallback(backend, model, GENERAL_SYSTEM, question,
                                        GENERAL_SCHEMA, job="general answer")
        text = (raw.get("answer") or "").strip()
    except Exception as exc:
        return Answer(question, refused=True, refined=ref,
                      reason=f"general answer unavailable: {type(exc).__name__}")
    if not text:
        return Answer(question, refused=True, refined=ref,
                      reason="no answer produced")
    return Answer(question, refused=False, reason=None, kind="general",
                  summary=text, refined=ref)


def lookup_answer(question: str, ref: Refined | None) -> Answer | None:
    """Return a named section verbatim, or None if it cannot be resolved.

    No LLM and no retrieval: the reader supplied the citation, so this is a
    dictionary lookup. That also makes it the one path where the quoted text is
    guaranteed correct by construction.
    """
    from app.rag import library

    parsed = parse_citation(question)
    if not parsed:
        return None
    slug, number = parsed
    slugs = [slug] if slug else [a["slug"] for a in library.acts()]
    found = [rec for s in slugs if (rec := library.get(f"{s}-s{number}"))]
    if not found:
        return None
    if len(found) > 1:
        names = ", ".join(f"{r['act']} s.{number}" for r in found)
        return Answer(question, refused=True, refined=ref,
                      reason=f"section {number} exists in more than one Act "
                             f"({names}) — name the Act")
    rec = found[0]
    hit = Retrieved(act=rec["act"], section=rec["section"],
                    section_title=rec.get("section_title"), text=rec["text"],
                    score=0.0, dense_score=0.0, rank=1, metadata=rec)
    title = f"{rec['act']}, Section {rec['section']}"
    if rec.get("section_title"):
        title += f": {rec['section_title']}"
    return Answer(question, refused=False, reason=None, kind="lookup",
                  summary=title,
                  claims=[{"text": rec["text"], "section": rec["section"],
                           "quote": rec["text"]}],
                  sources=[hit], refined=ref)


SUMMARY_SYSTEM = (
    "You write a short plain-English overview of a statute. You are given its "
    "real chapter structure, taken from the parsed document. Describe what the "
    "Act covers, chapter by chapter, in at most 150 words.\n\n"
    "Use ONLY the chapter titles and counts provided. Do not name section "
    "numbers, do not state penalties, and do not add anything you know about "
    "this Act from elsewhere — the outline is the only thing that has been "
    "verified against the source document."
)
SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
}


def outline_answer(question: str, ref: Refined | None, *, prose: bool = True,
                   backend: str | None = None, model: str | None = None) -> Answer:
    """Summarise a whole Act from its parsed structure.

    The Act cannot be summarised by retrieval — 556 sections is not a top-5 — and
    feeding all of them to a model would be both expensive and pointless, since
    the answer wanted is structural. So the structure IS the answer: parts,
    chapters, chapter titles and counts, straight out of the parsed hierarchy.

    The optional prose pass sees only that outline, never the statutory text, so
    it can rephrase the document's own shape but cannot invent provisions.
    """
    from app.rag import library

    slug = library.resolve_act(question)
    if not slug:
        acts = library.acts()
        names = ", ".join(a["name"] for a in acts)
        return Answer(question, refused=True, refined=ref,
                      reason=f"name which Act to summarise ({names})")
    o = library.outline(slug)
    if not o:
        return Answer(question, refused=True, refined=ref,
                      reason=f"{slug} is not in the index")

    head = f"{o['act']}"
    if o["act_no"]:
        head += f" ({o['act_no']})"
    facts = (f"{head} — {o['sections']} sections in {len(o['chapters'])} chapters"
             + (f", pp. {o['pages'][0]}-{o['pages'][1]} of the source PDF."
                if o["pages"] else "."))
    lines = [f"{c['chapter']}. {c['title'] or '(untitled)'} — "
             f"{len(c['sections'])} sections" for c in o["chapters"]]

    text = facts
    if prose:
        payload = head + "\n" + "\n".join(lines)
        try:
            raw, _ = generate_with_fallback(backend, model, SUMMARY_SYSTEM, payload,
                                            SUMMARY_SCHEMA, job="Act overview")
            if (w := (raw.get("summary") or "").strip()):
                text = f"{facts}\n\n{w}"
        except Exception:
            pass          # the structural facts stand on their own
    return Answer(question, refused=False, reason=None, kind="outline",
                  summary=text, claims=[{"text": l, "section": "", "quote": ""}
                                        for l in lines],
                  refined=ref)


# "Chapter XVI", "chapter 16", "ch. V" — the scope of a listing request.
CHAPTER_RE = re.compile(r"\b(?:chapter|chap\.?|ch\.)\s*([IVXLC]+|\d{1,2})\b", re.I)

_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}


def _roman_to_int(r: str) -> int | None:
    total, prev = 0, 0
    for ch in reversed(r.upper()):
        v = _ROMAN.get(ch)
        if v is None:
            return None
        total, prev = (total - v if v < prev else total + v), max(v, prev)
    return total


def chapter_key(text: str) -> str | None:
    """Chapter named in the question, normalised to how the parser stores it."""
    m = CHAPTER_RE.search(text)
    if not m:
        return None
    raw = m.group(1).upper()
    return raw if _roman_to_int(raw) is not None and not raw.isdigit() else raw


def _chapter_matches(stored: str, asked: str) -> bool:
    """Compare "XVI" against "16" as well as against "XVI".

    Chapters are stored as the document writes them, which is roman for all
    three acts. A reader typing "chapter 16" means the same thing.
    """
    stored, asked = (stored or "").strip().upper(), asked.strip().upper()
    if stored == asked:
        return True
    si, ai = _roman_to_int(stored), (int(asked) if asked.isdigit()
                                     else _roman_to_int(asked))
    return si is not None and ai is not None and si == ai


def browse_answer(question: str, ref: Refined | None, limit: int = 60,
                  scope=None) -> Answer:
    """List an Act's sections, grouped by chapter.

    "State all the sections of the Penal Code" is a real request with a real
    answer; it was previously refused with a pointer to the sidebar, which is
    the system declining to do something it can trivially do. No model and no
    retrieval — this is the parsed table of contents.
    """
    from app.rag import library

    scope = scope or extract_scope(question)
    slug = scope.act or library.resolve_act(question)
    if not slug:
        acts = library.acts()
        listed = "; ".join(f"{a['name']} ({a['sections']} sections)" for a in acts)
        return Answer(question, refused=False, reason=None, kind="outline",
                      summary=f"Indexed Acts — {listed}. Name one to list its "
                              f"sections.", refined=ref)
    o = library.outline(slug)
    if not o:
        return Answer(question, refused=True, refined=ref,
                      reason=f"{slug} is not in the index")

    # A chapter-scoped request must not silently return the whole Act from the
    # top: asking for Chapter XVI and receiving Chapter I is wrong in a way the
    # reader cannot see. Scope comes from extract_scope, which resolves an
    # explicit "Chapter XVI" by regex and a described one ("the murder chapter")
    # by matching the Act's real titles.
    chapters = o["chapters"]
    asked = scope.chapter if scope and scope.chapter else chapter_key(question)
    scope = ""
    if asked:
        matched = [c for c in chapters if _chapter_matches(c["chapter"], asked)]
        if not matched:
            have = ", ".join(str(c["chapter"]) for c in chapters)
            return Answer(question, refused=True, refined=ref,
                          reason=f"{o['act']} has no chapter {asked} "
                                 f"(chapters: {have})")
        chapters = matched
        limit = max(limit, sum(len(c["sections"]) for c in matched))
        scope = f", Chapter {matched[0]['chapter']}"

    claims, shown = [], 0
    for c in chapters:
        claims.append({"text": f"Chapter {c['chapter']}"
                               f"{' — ' + c['title'] if c['title'] else ''}"
                               f" ({len(c['sections'])} sections)",
                       "section": "", "quote": ""})
        for sec in c["sections"]:
            if shown >= limit:
                break
            claims.append({"text": f"  s.{sec['section']} "
                                   f"{sec['title'] or '(untitled)'}",
                           "section": sec["section"], "quote": ""})
            shown += 1
        if shown >= limit:
            break
    total = sum(len(c["sections"]) for c in chapters)
    summary = (f"{o['act']}{scope} — {total} sections"
               if asked else
               f"{o['act']} — {o['sections']} sections in "
               f"{len(o['chapters'])} chapters")
    if shown < total:
        summary += f". Showing the first {shown}; browse the statute list for all."
    return Answer(question, refused=False, reason=None, kind="outline",
                  summary=summary, claims=claims, refined=ref)


def build_context(hits: list[Retrieved]) -> str:
    """Numbered excerpts for the prompt.

    The chunk text already opens with the hierarchy line that chunking prepends
    for the embedder, so the heading is stripped off the body before a fresh one
    is written — otherwise every section arrives with its title twice, which
    wastes context and reads like a transcription error to the model.
    """
    return "\n\n".join(
        f"[{i}] {h.act}, Section {h.section}"
        + (f": {h.section_title}" if h.section_title else "")
        + f"\n{strip_prefix(h.text)}"
        for i, h in enumerate(hits, start=1)
    )


def answer(question: str, k: int | None = None, *, backend: str | None = None,
           model: str | None = None, min_score: float | None = None,
           hits: list[Retrieved] | None = None,
           refine_query: bool | None = None) -> Answer:
    """Retrieve, generate, verify. Refusal is a normal outcome, not an error.

    `min_score` is decision D2: a reranker-score floor below which we do not
    bother the generator at all. It is deliberately NOT given a default value
    here — the threshold has to be fitted on the 608-question set and published
    as an operating point, and inventing one would be exactly the kind of
    unmeasured constant this project keeps out of the pipeline.
    """
    k = k or config.FINAL_K
    # Routing decides whether retrieval is the right tool; rewriting changes what
    # gets embedded. Separate switches: the first is a correctness fix, the
    # second is an unproven retrieval technique.
    use_route = config.ROUTE_QUERIES if refine_query is None else refine_query
    use_refine = config.REFINE_QUERIES if refine_query is None else refine_query

    ref = None
    if use_route and hits is None:
        # Deliberately not passing `model`: that is the answering model, and the
        # router is a separate, smaller one (config.DEFAULT_ROUTER).
        ref = refine(question, rewrite=use_refine)
        # Three of the four routes are served better by something other than
        # retrieval. All three previously arrived here as five loosely-matching
        # sections plus a wasted generation call.
        if ref.intent == "meta":
            return Answer(question, refused=False, kind="meta", reason=None,
                          summary=capability_answer(), refined=ref)
        if ref.intent == "browse":
            return browse_answer(question, ref)
        if ref.intent == "summarize":
            return outline_answer(question, ref, backend=backend, model=model)
        if ref.intent == "lookup":
            # The section is named, so fetch it. Semantic search ranks by
            # similarity to the question, which can put the section the reader
            # asked for *below* its neighbours — wrong, and confusingly so when
            # they typed the number themselves.
            direct = lookup_answer(question, ref)
            if direct is not None:
                return direct
            # Unresolvable citation falls through to retrieval rather than
            # failing: "section 999" may still be a real question.
        if ref.intent == "general":
            return general_answer(question, ref, backend=backend, model=model)

    # `hits` lets an experiment reuse a cached retrieval pass. Retrieval over the
    # eval set costs ~10 minutes of cross-encoder time, and re-paying it per run
    # would make measuring the generator needlessly expensive.
    hits = hits if hits is not None else retrieve(ref.query if ref else question, k=k)
    if not hits:
        return Answer(question, refused=True, reason="nothing retrieved", refined=ref)

    # D2: refuse before generating when the reranker says nothing here answers
    # the question. Fitted on the 608-question set — see scripts/fit_threshold.py
    # and the operating point recorded in generation-plan.md. Still no default:
    # the caller opts in with a number that has evidence behind it.
    if min_score is not None and hits[0].score < min_score:
        return Answer(question, refused=True,
                      reason=f"top reranker score {hits[0].score:.2f} below "
                             f"threshold {min_score:.2f}",
                      sources=hits, refined=ref)

    llm = get_llm(backend, model)
    raw = llm.generate_json(SYSTEM, f"Sections:\n\n{build_context(hits)}\n\n---\n\n"
                                    f"Question: {question}", SCHEMA)

    if not raw.get("sufficient"):
        return Answer(question, refused=True,
                      reason="the retrieved sections do not cover this question",
                      ambiguous=bool(raw.get("ambiguous")),
                      clarification=raw.get("clarification") or "",
                      sources=hits, model=llm.name, refined=ref)

    # C2 + C3. Claims that fail either gate are dropped rather than shown with a
    # warning: a citation the reader has to second-guess is worse than one fewer
    # sentence, and this is the only check here that does not trust the model.
    checked = check_claims(raw.get("claims") or [], hits)
    # Carry the RESOLVED section number, not whatever the model wrote. It may
    # have said "The Penal Code, 1860, Section 379"; downstream this key is what
    # links a claim to its citation, so the raw string would break the link and
    # render as "s.The Penal Code, 1860, Section 379".
    kept = [
        {"text": c.text, "section": c.resolved_section, "quote": c.quote}
        for c in checked.claims if c.ok
    ]
    dropped = [f"s.{c.section}: {c.failure}" for c in checked.failures]

    if not kept:
        return Answer(question, refused=True,
                      reason="no claim survived citation verification",
                      dropped=dropped, sources=hits, verification=checked,
                      model=llm.name, refined=ref)

    cited = {c["section"] for c in kept}
    return Answer(
        question=question, refused=False, reason=None,
        ambiguous=bool(raw.get("ambiguous")),
        clarification=raw.get("clarification") or "",
        summary=raw.get("summary") or "",
        claims=kept, dropped=dropped,
        # only sections that survived into the answer are offered as citations
        sources=[h for h in hits if str(h.section) in cited],
        verification=checked, model=llm.name, refined=ref,
    )
