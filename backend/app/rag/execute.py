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

from app.rag import catalog, config
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

    total, rows = catalog.sections(
        act_slug=plan.act, chapter_number=plan.chapter,
        numbers=plan.sections or None, number_from=plan.section_from,
        number_to=plan.section_to, limit=limit)

    if not total:
        return Answer(plan.question, refused=True, kind="structural", plan=plan,
                      reason=_nothing_found(plan))

    # One section, asked for by name: quote it in full. This is the path where
    # the text is correct by construction — it came from the catalog and no
    # model touched it.
    if total == 1 and len(rows) == 1:
        rec = rows[0]
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

    return _enumerate(plan, total, rows)


def _nothing_found(plan: Plan) -> str:
    """Say which coordinate came back empty, not just that something did."""
    where = []
    if plan.chapter:
        where.append(f"Chapter {plan.chapter}")
    if plan.sections:
        where.append("section " + ", ".join(plan.sections))
    if plan.section_from:
        where.append(f"sections {plan.section_from} to {plan.section_to}")
    act = catalog.act(plan.act)["name"] if plan.act and catalog.act(plan.act) else None
    what = " and ".join(where) if where else "that"
    return (f"{what} is not in {act}." if act
            else f"{what} is not in the indexed Acts.")


def _enumerate(plan: Plan, total: int, rows: list[dict]) -> Answer:
    """A listing: chapter, range, or a whole Act.

    Titles rather than full text. A chapter of 86 sections rendered in full is
    not an answer to "what is in Chapter XVI", and it would not fit a prompt or
    a screen. The reader can open any of them from the citation drawer.
    """
    head = f"{total} section"+ ("s" if total != 1 else "")
    if plan.act and (a := catalog.act(plan.act)):
        head += f" of {a['name']}"
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

    claims = [{"text": f"s.{r['number']} {r['title'] or '(untitled)'}",
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
    hits = hits if hits is not None else retrieve(plan.query, k=k)
    if not hits:
        return Answer(plan.question, refused=True, plan=plan,
                      reason="nothing retrieved")

    if min_score is not None and hits[0].score < min_score:
        return Answer(plan.question, refused=True, plan=plan, sources=hits,
                      reason=f"top reranker score {hits[0].score:.2f} below "
                             f"threshold {min_score:.2f}")

    shown = hits[:MAX_CONTEXT_SECTIONS]
    llm = get_llm(backend, model)
    raw = llm.generate_json(
        SYSTEM, f"Sections:\n\n{build_context(shown)}\n\n---\n\n"
                f"Question: {plan.question}", SCHEMA)

    if not raw.get("sufficient"):
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

    if not kept:
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
        return clarify_answer(plan)
    if plan.category == "meta":
        return meta_answer(plan)
    if plan.category == "out_of_scope":
        return out_of_scope_answer(plan)
    if plan.category == "general":
        a = general_answer(plan.question, None, backend=backend, model=model)
        a.plan = plan
        return a
    if plan.category == "structural":
        return structural_answer(plan, backend=backend, model=model)
    return semantic_answer(plan, backend=backend, model=model, **kw)


def answer_question(question: str, *, backend: str | None = None,
                    model: str | None = None, planner: str | None = None,
                    **kw) -> Answer:
    """Plan, then execute. The whole path, for a caller that has a question.

    `planner` is separate from `model` on purpose: planning is a small job that
    runs on every question, and the answering model may be slower and dearer.
    """
    plan = make_plan(question, model=planner)
    return execute(plan, backend=backend, model=model, **kw)
