"""Stage 2 of answering: run a validated plan.

`plan.py` decided what the question is and named where to look. Nothing here
re-decides that. Each executor takes coordinates the catalog has already
confirmed exist, or a semantic query, and produces an `Answer`.

    meta          capability facts, read from the catalog, no model call
    structural    exact SQL fetch — one section, a range, a chapter, an Act
    semantic      retrieve, rerank, generate, verify every claim
    out_of_scope  say the corpus does not hold this, cite nothing
    general       answer plainly, no citations, labelled ungrounded
    clarify       ask the question the planner could not answer for itself

Two rules that are easy to get wrong and expensive to get wrong:

**The Act is a filter on structural plans and a hint on semantic ones.** When a
reader writes "Penal Code section 302" they have named the Act and it bounds the
answer. When they describe a scenario, the planner's Act guess does not: asked
about men who beat someone to death and were arrested without a warrant, the
planner returns `act=crpc`, correct for the arrest and wrong for the homicide.
Filtering retrieval by it would drop the Penal Code sections the question needs.

**A structural fetch quotes; it never paraphrases.** The reader asked for the
provision, so the provision is the answer, verbatim from the catalog. That makes
it the one path where quoted text is correct by construction — no model sees it,
so no model can alter it.
"""

from __future__ import annotations

import re

from app.rag import acts, catalog, config, trace
from app.rag.answer import (
    SCHEMA, SYSTEM, Answer, build_context, general_answer, outline_answer,
)
from app.rag.llm import get_llm
from app.rag.pipeline import Retrieved, retrieve
from app.rag.plan import Plan, make_plan
from app.rag.verify import check_claims

# How many sections a listing shows before it says "and N more". Not a limit on
# what was found: `total` is always reported, so a truncated page is never
# presented as the whole chapter.
BROWSE_LIMIT = 60

# How many sections reach the generator on a semantic answer. Separate from
# relevance on purpose: relevance decides what is related, this decides what
# fits in a prompt. When it binds, the answer says so.
MAX_CONTEXT_SECTIONS = 10


def _short(model: str) -> str:
    """`qwen/qwen3.8-27b` -> `qwen3.8-2`, so the log column stays aligned."""
    return (model or "llm").split("/")[-1][:9]


def _as_retrieved(rec: dict, rank: int = 1) -> Retrieved:
    """A catalog row in the shape the citation machinery expects.

    Score 0.0 because nothing was ranked — this row was fetched by name. The
    presenter and the citation drawer read the same fields either way, so an
    exact fetch and a retrieved hit render identically.
    """
    return Retrieved(
        act=rec["act"], section=str(rec["number"]),
        section_title=rec.get("title"), text=rec["text"],
        score=0.0, dense_score=0.0, rank=rank,
        metadata={
            "key": rec["key"], "act": rec["act"], "act_slug": rec["act_slug"],
            "act_no": rec.get("act_no"), "section": str(rec["number"]),
            "section_title": rec.get("title"),
            # The printed chapter, not its row id — citations render this as
            # provenance, and an integer there reads as a defect.
            "chapter": rec.get("chapter"),
            "chapter_title": rec.get("chapter_title"), "part": rec.get("part"),
            "page_start": rec.get("page_start"), "page_end": rec.get("page_end"),
            "source_pdf": rec.get("source_pdf"),
        },
    )


# ---------------------------------------------------------------- meta

def meta_answer(plan: Plan) -> Answer:
    """What this assistant is, assembled from the catalog.

    Not a hardcoded blurb and not a model call: both drift from what the system
    actually contains. Read from the catalog, the description cannot claim an
    Act that is not indexed.
    """
    try:
        m = catalog.manifest()
        acts = m["acts"]
    except Exception:
        acts = []
    if not acts:
        return Answer(plan.question, refused=False, reason=None, kind="meta",
                      summary="I answer questions about Bangladesh criminal law "
                              "from the text of the Acts themselves. The corpus "
                              "is currently unavailable.",
                      plan=plan)
    listed = "; ".join(f"{a['name']} ({a['sections']} sections)" for a in acts)
    total = sum(a["sections"] for a in acts)
    return Answer(
        plan.question, refused=False, reason=None, kind="meta", plan=plan,
        summary=(
            "I answer questions about Bangladesh criminal law using only the "
            f"text of the Acts indexed here: {listed} — {total} sections in "
            "all, parsed directly from the source PDFs.\n\n"
            "I can quote a section you name, list a chapter or a range, "
            "summarise a whole Act, or find the provisions that apply to a "
            "situation you describe.\n\n"
            "Every claim I make cites a section and quotes it verbatim, and the "
            "quote is checked against the source before you see it; claims that "
            "fail that check are dropped. If these Acts do not answer something, "
            "I say so rather than answering from memory. I cannot advise on your "
            "situation, cover case law or other statutes, or tell you what a "
            "court would decide."),
    )


# ---------------------------------------------------------------- refusals

def out_of_scope_answer(plan: Plan) -> Answer:
    """A legal question about law this corpus does not hold.

    Kept apart from `general`, which answers from the model's own knowledge.
    Doing that here would be an ungrounded legal answer about a statute nobody
    has verified — the E1 failure, where this model family placed Evidence Act
    ss.25-27 in the CrPC. Naming what IS covered is more useful than a bare
    refusal and costs nothing, since the catalog knows.
    """
    try:
        names = ", ".join(a["name"] for a in catalog.manifest()["acts"])
    except Exception:
        names = "the indexed Acts"
    return Answer(
        plan.question, refused=True, kind="out_of_scope", plan=plan,
        reason=("That is a legal question, but it is outside the Acts I hold. I "
                f"can only answer from {names}. I am not able to answer it from "
                "general knowledge, because nothing I said would be checkable "
                "against a source — and for law that is worse than no answer. "
                "For this you need the governing statute or a qualified lawyer."),
    )


def clarify_answer(plan: Plan) -> Answer:
    """Ask, rather than guess.

    The planner sets this when a request cannot be executed as written: a
    section number three Acts share, or a subjective selection like "the most
    important laws", where picking silently would present one interpretation as
    the answer.
    """
    return Answer(plan.question, refused=True, kind="clarify", plan=plan,
                  reason=plan.clarification or "Could you be more specific?",
                  clarification=plan.clarification)


# ---------------------------------------------------------------- structural

def structural_answer(plan: Plan, *, backend: str | None = None,
                      model: str | None = None, limit: int = BROWSE_LIMIT) -> Answer:
    """Fetch exactly what the plan named.

    Deterministic end to end. The coordinates were validated before arriving
    here, so an empty result means the reader asked for something the Acts do
    not contain — reported as that, never softened into a nearby section.
    """
    if plan.operation == "overview" and plan.act and not plan.chapter:
        return _overview(plan, backend=backend, model=model)

    # One query per named Act, merged. "The first five sections of the Evidence
    # Act and the Code of Criminal Procedure" is two fetches and ten sections,
    # and answering it from one Act was answering a different question without
    # saying so. The catalog takes a single slug per call by design — it is the
    # exact-coordinate read model, and a query spanning Acts is a merge of exact
    # queries rather than a looser one.
    targets = plan.acts or [plan.act]
    total, rows = 0, []
    for slug in targets:
        found, page = catalog.sections(
            act_slug=slug, chapter_number=plan.chapter,
            numbers=plan.sections or None, number_from=plan.section_from,
            number_to=plan.section_to, limit=limit)
        total += found
        rows.extend(page)
    # Statutory order within each Act, Acts in the order the question named
    # them. Sorting across Acts would interleave two numbering schemes and read
    # as one list that is wrong in both.
    rows = rows[:limit] if limit else rows

    if not total:
        trace.step("fetch", "0 sections — coordinates matched nothing")
        return Answer(plan.question, refused=True, kind="structural", plan=plan,
                      reason=_nothing_found(plan))
    trace.step("fetch", f"{total} section(s) from the catalog")

    if plan.operation == "explain":
        return _explain(plan, total, rows, backend=backend, model=model)

    # One section, asked for by name: quote it in full. This is the path where
    # the text is correct by construction — it came from the catalog and no
    # model touched it.
    if total == 1 and len(rows) == 1:
        return _lookup(plan, rows[0])

    return _enumerate(plan, total, rows)


def _lookup(plan: Plan, rec: dict) -> Answer:
    """One named section, quoted in full from the catalog."""
    title = f"{rec['act']}, Section {rec['number']}"
    if rec.get("title"):
        title += f": {rec['title']}"
    return Answer(
        plan.question, refused=False, reason=None, kind="lookup", plan=plan,
        summary=title,
        claims=[{"text": rec["text"], "section": str(rec["number"]),
                 "quote": rec["text"]}],
        sources=[_as_retrieved(rec)],
    )


EXPLAIN_SYSTEM = (
    "You are a legal information assistant for Bangladesh criminal law. The user "
    "named one or more sections and asked you to EXPLAIN them. You are given "
    "exactly those sections, each labelled with its Act and section number.\n\n"
    "Do not just repeat the section. Break it down so a non-lawyer understands "
    "it, using only the words of the sections provided.\n\n"
    "Rules:\n"
    "1. summary: one or two plain sentences saying what the provision is about "
    "and what it does.\n"
    "2. claims: one per element, in the order a reader needs them - what "
    "conduct it covers; each condition that must be met (including any intent, "
    "knowledge or circumstance it requires); who it applies to; the "
    "punishment, if it states one; then each Explanation, Exception, proviso "
    "and Illustration it contains. Say each in plain words.\n"
    "3. Every claim must carry its section number and a VERBATIM quote - the "
    "exact words of that section that establish the point. Quotes are checked "
    "automatically; a paraphrase inside the quote fails.\n"
    "4. Explain only what the text says. Do not add case law, other statutes, "
    "or how courts apply it, and do not answer questions the user did not ask.\n"
    "5. Set sufficient=true: the sections are exactly the ones asked about.\n"
    "6. Do not add a disclaimer."
)


def _explain(plan: Plan, total: int, rows: list[dict], *,
             backend: str | None = None, model: str | None = None) -> Answer:
    """Named sections, fetched exactly, then explained element by element.

    The fetch is the same as `fetch` — by number, from the catalog, so the
    section explained is the one asked about, never a nearest match. Only the
    presentation differs: the generator breaks it down, and every point it makes
    carries a quote that is checked against the section like any other claim.
    If that produces nothing usable, the reader still gets the text they named
    rather than a refusal.
    """
    shown = rows[:MAX_CONTEXT_SECTIONS]
    hits = [_as_retrieved(rec, i) for i, rec in enumerate(shown, 1)]
    llm = get_llm(backend, model)
    trace.sent(_short(llm.name),
               f"explain {' '.join(h.key for h in hits[:5])}  «{plan.asked}»")
    generating = trace.timer()
    raw = llm.generate_json(
        EXPLAIN_SYSTEM, f"Sections:\n\n{build_context(hits)}\n\n---\n\n"
                        f"Question: {plan.asked}", SCHEMA)

    checked = check_claims(raw.get("claims") or [], hits)
    kept = [{"text": c.text, "section": c.resolved_section, "quote": c.quote}
            for c in checked.claims if c.ok]
    dropped = [f"s.{c.section}: {c.failure}" for c in checked.failures]
    trace.recv(_short(llm.name),
               f"{len(checked.claims)} points -> {len(kept)} kept, {len(dropped)} dropped",
               generating())

    if not kept:
        # Nothing survived the quote check: show what was named instead.
        trace.step("explain", "no point survived — showing the text instead")
        if total == 1 and len(rows) == 1:
            return _lookup(plan, rows[0])
        return _enumerate(plan, total, rows)

    cited = {c["section"] for c in kept}
    return Answer(
        plan.question, refused=False, reason=None, plan=plan,
        summary=raw.get("summary") or "", claims=kept, dropped=dropped,
        sources=[h for h in hits if str(h.section) in cited],
        verification=checked, model=llm.name,
    )


def _nothing_found(plan: Plan) -> str:
    """Say which coordinate came back empty, not just that something did."""
    where = []
    if plan.chapter:
        where.append(f"Chapter {plan.chapter}")
    if plan.sections:
        where.append("section " + ", ".join(plan.sections))
    if plan.section_from:
        where.append(f"sections {plan.section_from} to {plan.section_to}")
    named = [catalog.act(a)["name"] for a in (plan.acts or [plan.act])
             if a and catalog.act(a)]
    what = " and ".join(where) if where else "that"
    return (f"{what} is not in {' or '.join(named)}." if named
            else f"{what} is not in the indexed Acts.")


def _enumerate(plan: Plan, total: int, rows: list[dict]) -> Answer:
    """A listing: chapter, range, or a whole Act.

    Titles rather than full text. A chapter of 86 sections rendered in full is
    not an answer to "what is in Chapter XVI", and it would not fit a prompt or
    a screen. The reader can open any of them from the citation drawer.
    """
    head = f"{total} section"+ ("s" if total != 1 else "")
    # Every Act named, not just the first. A listing drawn from two Acts and
    # headed with one of them is a caption that contradicts its own contents.
    named = [a["name"] for slug in (plan.acts or [plan.act])
             if slug and (a := catalog.act(slug))]
    if named:
        head += " of " + " and ".join(named)
    if plan.chapter:
        ch = catalog.chapter(plan.act, plan.chapter)
        head += f", Chapter {plan.chapter}"
        if ch and ch["title"]:
            head += f" — {ch['title']}"
    elif plan.section_from:
        head += f", sections {plan.section_from} to {plan.section_to}"
    if total > len(rows):
        # Stated, never silent. A truncated page presented as a complete chapter
        # is the "five sections disguised as completeness" failure again.
        head += f". Showing the first {len(rows)}"

    # With one Act, "Section 1" is unambiguous. With two it is not — both have
    # a section 1, and an undifferentiated list reads as one Act's numbering.
    multi = len(plan.acts) > 1
    claims = [{"text": (f"{r['act']}, Section {r['number']}: " if multi
                        else f"Section {r['number']}: ")
                       + (r["title"] or "(untitled)"),
               "section": str(r["number"]), "quote": ""} for r in rows]
    return Answer(plan.question, refused=False, reason=None, kind="outline",
                  plan=plan, summary=head, claims=claims,
                  sources=[_as_retrieved(r, i) for i, r in enumerate(rows, 1)])


def _overview(plan: Plan, *, backend: str | None = None,
              model: str | None = None) -> Answer:
    """Summarise a whole Act from its structure.

    Retrieval cannot answer this — 557 sections is not a top-5 — and the answer
    wanted is structural anyway. The prose pass sees only the chapter outline,
    never statutory text, so it can rephrase the document's shape but cannot
    invent a provision. `outline_answer` already does exactly this; reused
    rather than reimplemented so both paths stay in step.
    """
    a = outline_answer(plan.question, None, backend=backend, model=model)
    a.plan = plan
    return a


# ---------------------------------------------------------------- semantic

def semantic_answer(plan: Plan, *, backend: str | None = None,
                    model: str | None = None, k: int | None = None,
                    min_score: float | None = None,
                    hits: list[Retrieved] | None = None) -> Answer:
    """Find the provisions, then answer only from them.

    The scenario path. `plan.query` is the question plus the planner's statutory
    restatement, concatenated rather than substituted — a rewrite that dropped a
    distinguishing detail would otherwise lose a provision the plain question
    would have found.

    `plan.act` is deliberately NOT used to filter. See the module docstring: on a
    multi-issue scenario the planner names one Act and the answer needs two.
    """
    k = k or config.FINAL_K
    if hits is None:
        elapsed = trace.timer()
        hits = retrieve(plan.query, k=k)
        trace.step("retrieve", trace.hits(hits), elapsed())
    if not hits:
        return Answer(plan.question, refused=True, plan=plan,
                      reason="nothing retrieved")

    if min_score is not None and hits[0].score < min_score:
        return Answer(plan.question, refused=True, plan=plan, sources=hits,
                      reason=f"top reranker score {hits[0].score:.2f} below "
                             f"threshold {min_score:.2f}")

    shown = hits[:MAX_CONTEXT_SECTIONS]
    llm = get_llm(backend, model)
    trace.sent(_short(llm.name),
               f"{len(shown)} sections [{' '.join(h.key for h in shown[:5])}]  «{plan.asked}»")
    generating = trace.timer()
    # `plan.asked` is the standalone form: identical to the question unless this
    # was a follow-up, in which case it is the rewrite that carries the earlier
    # facts. The generator must never see "what if he had survived" alone.
    raw = llm.generate_json(
        SYSTEM, f"Sections:\n\n{build_context(shown)}\n\n---\n\n"
                f"Question: {plan.asked}", SCHEMA)

    if not raw.get("sufficient"):
        trace.recv(_short(llm.name), "sufficient=false — declined", generating())
        trace.step("refused", "the retrieved sections do not cover this")
        return Answer(plan.question, refused=True, plan=plan,
                      reason="the retrieved sections do not cover this question",
                      ambiguous=bool(raw.get("ambiguous")),
                      clarification=raw.get("clarification") or "",
                      sources=shown, model=llm.name)

    # C2 + C3, unchanged: the cited section must have been retrieved, and the
    # quote must appear verbatim in it. The only check here that does not rely
    # on the model's own judgement, which is why it is the one that is not
    # optional.
    checked = check_claims(raw.get("claims") or [], shown)
    kept = [{"text": c.text, "section": c.resolved_section, "quote": c.quote}
            for c in checked.claims if c.ok]
    dropped = [f"s.{c.section}: {c.failure}" for c in checked.failures]
    trace.recv(_short(llm.name),
               f"{len(checked.claims)} claims -> {len(kept)} kept, {len(dropped)} dropped"
               + (f"  [{'; '.join(dropped[:2])}]" if dropped else ""),
               generating())

    if not kept:
        trace.step("refused", "no claim survived the citation gates")
        return Answer(plan.question, refused=True, plan=plan, dropped=dropped,
                      reason="no claim survived citation verification",
                      sources=shown, verification=checked, model=llm.name)

    cited = {c["section"] for c in kept}
    return Answer(
        plan.question, refused=False, reason=None, plan=plan,
        ambiguous=bool(raw.get("ambiguous")),
        clarification=raw.get("clarification") or "",
        summary=raw.get("summary") or "",
        claims=kept, dropped=dropped,
        sources=[h for h in shown if str(h.section) in cited],
        verification=checked, model=llm.name,
    )


# ---------------------------------------------------------------- dispatch

def execute(plan: Plan, *, backend: str | None = None, model: str | None = None,
            **kw) -> Answer:
    """Run a validated plan. One branch per category, no re-deciding."""
    if plan.needs_clarification:
        trace.step("clarify", plan.clarification)
        return clarify_answer(plan)
    if plan.category == "meta":
        trace.step("meta", "capability answer from the catalog")
        return meta_answer(plan)
    if plan.category == "out_of_scope":
        trace.step("scope", "legal, but outside the indexed Acts")
        return out_of_scope_answer(plan)
    if plan.category == "general":
        trace.step("general", "answered without citations")
        a = general_answer(plan.asked, None, backend=backend, model=model)
        a.plan = plan
        return a
    if plan.category == "document":
        return document_answer(plan, backend=backend, model=model)
    if plan.category == "structural":
        return structural_answer(plan, backend=backend, model=model)
    return semantic_answer(plan, backend=backend, model=model, **kw)


# Phrases that mark a pasted document rather than a typed question. Drawn from
# how law reports and briefs are actually laid out — a reader pasting one of
# these is asking "read this", not "answer this in a sentence".
DOCUMENT_MARKERS = (
    "case brief", "facts:", "argument:", "arguments:", "order:", "held:",
    "judgment:", "judgement:", "ratio:", "issue:", "issues:", "petitioner",
    "respondent", "writ petition", "appellant", "decided on",
)
# "55 DLR (2003) 363", "2003 BLD 45" — a law-report citation, which no ordinary
# question contains.
REPORT_CITATION = re.compile(
    r"\b\d{1,3}\s*(?:DLR|BLD|BLC|MLR|SCOB|CLC|PLD)\b|\bvs?\.\s+[A-Z]", re.I)


def document_like(text: str) -> bool:
    """Is this a pasted document rather than a question?

    Two independent signals, because either alone is wrong. Length alone would
    catch a long, careful question; markers alone would miss a transcribed page
    that happens to contain none of them. A document is long **and** shaped like
    one — or carries a law-report citation, which nothing else does.
    """
    body = (text or "").strip()
    if len(body) < config.LONG_INPUT_CHARS:
        return False
    lowered = body.lower()
    if any(marker in lowered for marker in DOCUMENT_MARKERS):
        return True
    if REPORT_CITATION.search(body):
        return True
    # A transcribed page with no legal furniture at all: still a document if it
    # is long and reads as prose rather than as one question.
    return len(body) > config.LONG_INPUT_CHARS * 2 and body.count(".") >= 6


DOCUMENT_SYSTEM = (
    "You are a legal information assistant for Bangladesh criminal law. The user "
    "has pasted a DOCUMENT — a case brief, a law-report extract, a transcribed "
    "page — and you are given the statutory sections it refers to, each labelled "
    "with the Act it comes from.\n\n"
    "Explain what the provisions the document relies on actually SAY, using only "
    "the sections provided.\n\n"
    "Rules:\n"
    "1. Answer ONLY from the provided sections. The document is context, NOT a "
    "source — it may summarise, paraphrase or misstate the law, and anything in "
    "it that is not in the sections below is unverified.\n"
    "2. Judgments, case law, guidelines laid down by a court, and the "
    "Constitution are NOT in this corpus. Do not restate them, even if the "
    "document quotes them, and say plainly that they are outside what you hold.\n"
    "3. Every claim must carry the section number it came from and a VERBATIM "
    "quote copied exactly from that section's text. Quotes are checked "
    "automatically.\n"
    "4. If the provided sections do not cover what is being asked, set "
    "sufficient=false.\n"
    "5. Do not add a disclaimer.\n"
    "6. If the user asks you to explain, interpret or break down a provision - by "
    "number, or by pasting its text - do not just repeat it. Explain it element "
    "by element in plain words: what conduct it covers, each condition it "
    "requires (including any intent or knowledge), the punishment, and any "
    "Explanation, Exception or Illustration - one claim per element, each "
    "quoting the exact words that establish it.\n"
)

# Every section number a passage cites: "Section 54", "sections 54 and 167",
# "s.302", "CrPC 154". Deliberately deterministic — the planner is asked for
# these too, but a 1,000-character case brief buries the instruction, and it
# returned an empty list for a text that names s.54 and s.167 four times.
CITED_RE = re.compile(
    r"\b(?:sections?|secs?\.?|ss?\.)\s*"
    r"(\d{1,3}[A-Z]{0,2}(?:\s*(?:,|and|&|to|-)\s*\d{1,3}[A-Z]{0,2})*)",
    re.I)
NUM_RE = re.compile(r"\d{1,3}[A-Z]{0,2}", re.I)


def cited_sections(text: str, default_act: str | None = None) -> list[dict]:
    """Sections a passage names, as catalog rows. Never invents one.

    Each number is resolved against the catalog and dropped if it does not
    exist, so a misread "s.999" contributes nothing rather than a near miss.
    Where the passage names an Act near the citation, that Act is used; where it
    does not, the plan's Act is, and failing that every Act is tried and only an
    unambiguous match is kept.
    """
    body = text or ""
    lowered = body.lower()
    rows: list[dict] = []
    seen: set[str] = set()

    for match in CITED_RE.finditer(body):
        # Which Act this citation belongs to, read from the words around it.
        lo = max(0, match.start() - 90)
        window = lowered[lo: match.end() + 90]
        # The Act named NEAREST the number, from the catalog's own list — so
        # "s.54 of the CrPC and s.25 of the Evidence Act" gives each number its
        # own Act, and an Act loaded later is recognised with no code change.
        act = acts.nearest(window, match.start() - lo) or default_act
        for number in NUM_RE.findall(match.group(1)):
            number = number.upper()
            candidates = ([catalog.section(act, number)] if act
                          else catalog.find_number(number))
            found = [c for c in candidates if c]
            # Ambiguous without an Act named: all three have a section 54, and
            # guessing which was meant is how a citation ends up pointing at the
            # wrong statute.
            if len(found) != 1:
                continue
            rec = found[0]
            if rec["key"] not in seen:
                seen.add(rec["key"])
                rows.append(rec)
    return rows


def document_answer(plan: Plan, *, backend: str | None = None,
                    model: str | None = None, k: int | None = None) -> Answer:
    """A pasted document: read it, and explain the provisions it cites.

    Two sources of context, and the first is the point of this path:

      cited     the sections the passage NAMES, fetched from the catalog by
                number. Guaranteed present, not merely likely — this is the
                failure that made the category necessary. A case brief about
                CrPC ss.54 and 167 retrieved s.58, s.66 and s.93C instead,
                because 1,000 characters of narrative ("BLAST, writ petition,
                custodial death") swamped the 60 characters of statutory
                language in the embedding, and the answer was refused with both
                provisions sitting in the corpus, named in the question.
      retrieved whatever the passage is *about* beyond what it cites.

    Cited sections come first and are never displaced by ranked ones.
    """
    cited = cited_sections(plan.question, plan.act)
    if cited:
        trace.step("cited", " ".join(r["key"] for r in cited[:5]))
    hits = [_as_retrieved(rec, i) for i, rec in enumerate(cited, 1)]

    room = MAX_CONTEXT_SECTIONS - len(hits)
    if room > 0:
        have = {h.key for h in hits}
        for hit in retrieve(plan.query, k=k or config.FINAL_K):
            if hit.key not in have:
                hits.append(hit)
                if len(hits) >= MAX_CONTEXT_SECTIONS:
                    break

    if not hits:
        return Answer(plan.question, refused=True, plan=plan,
                      reason="nothing in the indexed Acts matched this document")

    # Whatever model the caller chose, or the server default. A document is not
    # silently rerouted to a bigger one; the response suggests it instead.
    llm = get_llm(backend, model)
    raw = llm.generate_json(
        DOCUMENT_SYSTEM,
        f"Sections:\n\n{build_context(hits)}\n\n---\n\n"
        f"Document:\n{plan.asked}", SCHEMA)

    if not raw.get("sufficient"):
        return Answer(plan.question, refused=True, plan=plan, sources=hits,
                      reason="the indexed Acts do not cover what this document asks",
                      model=llm.name)

    checked = check_claims(raw.get("claims") or [], hits)
    kept = [{"text": c.text, "section": c.resolved_section, "quote": c.quote}
            for c in checked.claims if c.ok]
    dropped = [f"s.{c.section}: {c.failure}" for c in checked.failures]
    if not kept:
        return Answer(plan.question, refused=True, plan=plan, dropped=dropped,
                      reason="no claim survived citation verification",
                      sources=hits, verification=checked, model=llm.name)

    cited_keys = {c["section"] for c in kept}
    return Answer(
        plan.question, refused=False, reason=None, plan=plan,
        summary=raw.get("summary") or "", claims=kept, dropped=dropped,
        sources=[h for h in hits if str(h.section) in cited_keys],
        verification=checked, model=llm.name,
    )


def answer_question(question: str, *, backend: str | None = None,
                    model: str | None = None, planner: str | None = None,
                    memory=None, **kw) -> Answer:
    """Plan, then execute. The whole path, for a caller that has a question.

    `planner` is separate from `model` on purpose: planning is a small job that
    runs on every question, and the answering model may be slower and dearer.

    `memory` is the rolling conversation summary (`app/rag/memory.py`). It
    reaches only the planner: if the question turns out to be a follow-up, the
    planner rewrites it to stand alone and every executor downstream sees an
    ordinary self-contained question. Nothing below this line knows there is a
    conversation.
    """
    plan = make_plan(question, model=planner, memory=memory)
    return execute(plan, backend=backend, model=model, **kw)
