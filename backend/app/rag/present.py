"""Answer -> the AssistantMessage the frontend already renders.

Kept separate from `answer.py` so the pipeline has no opinion about presentation
and the API contract has no opinion about retrieval.

The disclaimer is appended here, from a constant, on every path including
refusals. That placement is deliberate: the brief requires a disclaimer, and a
disclaimer produced by the model is one the model can forget to produce.
"""

from __future__ import annotations

import uuid

from app.models.schemas import (AssistantMessage, Citation, CitationSegment,
                                ParagraphBlock, StrongSegment, TextSegment)
from app.rag.answer import DISCLAIMER, Answer
from app.rag.citations import SHORT_NAMES, to_citations


def _para(*segments) -> ParagraphBlock:
    return ParagraphBlock(segments=list(segments))


def to_message(a: Answer) -> AssistantMessage:
    blocks = []
    citations: dict[str, Citation] = {}

    if a.kind == "meta":
        # No citations, and `unsupported` stays false: this is an answer about
        # the assistant, not a claim about the law, so there is nothing to cite
        # and nothing was refused.
        heading = "About this assistant"
        subheading = "Not a legal question"
        for para in a.summary.split("\n\n"):
            if para.strip():
                blocks.append(_para(TextSegment(text=para.strip())))
    elif a.refused:
        heading = "Not covered by the indexed Acts"
        subheading = "No answer given"
        blocks.append(_para(TextSegment(
            text="I could not answer this from the Code of Criminal Procedure 1898, "
                 "the Penal Code 1860 or the Evidence Act 1872 as indexed. "
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
                f"{SHORT_NAMES.get(h.act.strip().lower(), h.act)} s.{h.section}"
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

    return AssistantMessage(
        id=f"a-{uuid.uuid4().hex[:12]}",
        heading=heading,
        subheading=subheading,
        unsupported=a.refused,
        blocks=blocks,
        citations=citations,
    )
