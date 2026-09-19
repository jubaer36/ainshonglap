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

from dataclasses import dataclass, field

from app.rag import config
from app.rag.llm import get_llm
from app.rag.pipeline import Retrieved, retrieve
from app.rag.refine import Refined, refine
from app.rag.citations import strip_prefix
from app.rag.verify import VerifiedAnswer, check_claims

DISCLAIMER = (
    "This is legal information drawn from the text of the Acts, not legal "
    "advice. For advice on a specific situation, consult a qualified lawyer."
)

SYSTEM = (
    "You are a legal information assistant for Bangladesh criminal law. You are "
    "given numbered excerpts from the Code of Criminal Procedure 1898, the Penal "
    "Code 1860 and the Evidence Act 1872.\n\n"
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
    "5. Do not add a disclaimer; one is attached automatically."
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
    kind: str = "legal"          # "legal" | "meta" — meta answers carry no citations
    ambiguous: bool = False
    clarification: str = ""
    summary: str = ""
    claims: list[dict] = field(default_factory=list)      # surviving claims only
    dropped: list[str] = field(default_factory=list)      # gate failures, for the log
    sources: list[Retrieved] = field(default_factory=list)
    verification: VerifiedAnswer | None = None
    model: str = ""
    refined: Refined | None = None      # None when routing is off


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
            return Answer(question, refused=True, refined=ref,
                          reason="this asks to list provisions rather than answer a "
                                 "question — browse the statute list instead")
        if ref.intent == "out_of_scope":
            return Answer(question, refused=True, refined=ref,
                          reason="not a question about Bangladesh criminal law")

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
