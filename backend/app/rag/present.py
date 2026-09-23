"""Answer -> the AssistantMessage the frontend already renders.

Kept separate from `answer.py` so the pipeline has no opinion about presentation
and the API contract has no opinion about retrieval.

The disclaimer is appended here, from a constant, on every path including
refusals. That placement is deliberate: the brief requires a disclaimer, and a
disclaimer produced by the model is one the model can forget to produce.
"""

from __future__ import annotations

import uuid

from app.models.schemas import (AssistantMessage, BulletsBlock, Citation,
                                CitationSegment, ParagraphBlock, StrongSegment,
                                TextSegment)
from app.rag.answer import DISCLAIMER, Answer
from app.rag import acts
from app.rag.citations import to_citations


def _para(*segments) -> ParagraphBlock:
    return ParagraphBlock(segments=list(segments))


def to_message(a: Answer) -> AssistantMessage:
    blocks = []
    citations: dict[str, Citation] = {}

    if a.kind == "general":
        # Explicitly labelled as ungrounded. The reader must be able to tell a
        # sentence backed by a quoted statute from one that is not, and on this
        # path nothing was retrieved or verified.
        heading = a.summary.split("\n")[0][:120]
        subheading = "General answer — not from the indexed Acts"
        for para in a.summary.split("\n\n"):
            if para.strip():
                blocks.append(_para(TextSegment(text=para.strip())))
        blocks.append(_para(StrongSegment(text="Note. "), TextSegment(
            text=f"This question is outside {acts.listing()}, so this answer "
                 "is not drawn from them and carries no citations.")))
    elif a.kind == "lookup":
        # The reader named the section, so show it verbatim.
        top = a.sources[0]
        heading = a.summary
        subheading = f"{top.act} — quoted in full"
        citations = to_citations(a.sources)
        blocks.append(_para(TextSegment(text=a.claims[0]["text"])))
        blocks.append(_para(
            TextSegment(text="Source: "),
            CitationSegment(key=top.key, label=f"s.{top.section}")))
    elif a.kind == "outline":
        # Structure, not statutory claims: the lines are titles from the parsed
        # hierarchy, so there is nothing to quote. An enumerated listing does
        # carry sources, though — each listed section is a real record, and the
        # reader should be able to open any of them rather than retype it as a
        # new question. A whole-Act summary carries none, and renders as before.
        heading = a.summary.split("\n")[0]
        subheading = "From the parsed document structure"
        citations = to_citations(a.sources) if a.sources else {}
        rest = a.summary.split("\n\n", 1)
        if len(rest) > 1 and rest[1].strip():
            blocks.append(_para(TextSegment(text=rest[1].strip())))
        if a.claims:
            by_key = {str(h.section): h.key for h in a.sources}
            items = []
            for c in a.claims[:80]:
                key = by_key.get(str(c["section"]))
                items.append([CitationSegment(key=key, label=c["text"])] if key
                             else [TextSegment(text=c["text"])])
            blocks.append(BulletsBlock(items=items))
    elif a.kind == "meta":
        # No citations, and `unsupported` stays false: this is an answer about
        # the assistant, not a claim about the law, so there is nothing to cite
        # and nothing was refused.
        heading = "About this assistant"
        subheading = "Not a legal question"
        for para in a.summary.split("\n\n"):
            if para.strip():
                blocks.append(_para(TextSegment(text=para.strip())))
    elif a.kind == "out_of_scope":
        # A legal question about law this corpus does not hold. Distinct from a
        # retrieval refusal: nothing was searched, because searching three Acts
        # for a provision of a fourth would only turn up plausible neighbours.
        # Distinct from `general` too, which answers from the model's knowledge —
        # doing that here would be an unverifiable legal answer.
        heading = "Outside the Acts I hold"
        subheading = "No answer given"
        blocks.append(_para(TextSegment(text=a.reason or "")))
    elif a.kind == "clarify":
        # A question back, not a refusal to help. `unsupported` is still true so
        # the client does not render it as an answer, but the wording has to read
        # as a request for one more detail.
        heading = "Which did you mean?"
        subheading = "Need one more detail"
        blocks.append(_para(TextSegment(
            text=a.clarification or a.reason or "Could you be more specific?")))
    elif a.refused:
        heading = "Not covered by the indexed Acts"
        subheading = "No answer given"
        blocks.append(_para(TextSegment(
            text=f"I could not answer this from {acts.listing('or')} as indexed. "
                 f"Reason: {a.reason}.")))
        if a.ambiguous and a.clarification:
            blocks.append(_para(StrongSegment(text="To narrow it down: "),
                                TextSegment(text=a.clarification)))
        # The sections that were considered are still shown. A refusal that
        # explains what it looked at is reviewable; a bare "I don't know" is not.
        # Each one is named with its act: "s.5" identifies nothing when all three
        # acts have a section 5.
        if a.sources:
            considered = ", ".join(
                f"{acts.short_for_name(h.act)} s.{h.section}"
                for h in a.sources[:5]
            )
            blocks.append(_para(TextSegment(
                text=f"Sections considered and rejected: {considered}.")))
    else:
        top = a.sources[0] if a.sources else None
        heading = a.summary or "Answer"
        subheading = (f"{top.act}, Section {top.section}" if top else "")
        citations = to_citations(a.sources)

        if a.ambiguous and a.clarification:
            blocks.append(_para(StrongSegment(text="This could mean more than one thing. "),
                                TextSegment(text=a.clarification)))

        by_key = {str(h.section): h.key for h in a.sources}
        for c in a.claims:
            key = by_key.get(c["section"])
            segs = [TextSegment(text=c["text"] + " ")]
            if key:
                segs.append(CitationSegment(key=key, label=f"s.{c['section']}"))
            blocks.append(_para(*segs))

    blocks.append(_para(StrongSegment(text="Disclaimer. "),
                        TextSegment(text=DISCLAIMER)))

    # A refused `legal`/`structural` answer is reported as `refused` rather than
    # by the machine that failed: the reader's question was not answered, and
    # which executor declined it is our concern, not theirs. `out_of_scope` and
    # `clarify` keep their own kind because they are different outcomes, not
    # variations of the same one.
    kind = a.kind
    if a.refused and kind not in ("out_of_scope", "clarify"):
        kind = "refused"

    return AssistantMessage(
        id=f"a-{uuid.uuid4().hex[:12]}",
        heading=heading,
        subheading=subheading,
        kind=kind,
        unsupported=a.refused,
        blocks=blocks,
        citations=citations,
    )
