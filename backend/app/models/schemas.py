"""Pydantic schemas for the /api/query contract.

These mirror frontend/lib/types.ts field-for-field (snake_case here,
camelCase there) so the two sides can be kept in sync by hand until
codegen is worth the trouble.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class TextSegment(BaseModel):
    type: Literal["text"] = "text"
    text: str


class StrongSegment(BaseModel):
    type: Literal["strong"] = "strong"
    text: str


class CitationSegment(BaseModel):
    type: Literal["citation"] = "citation"
    key: str
    label: str


Segment = Annotated[
    Union[TextSegment, StrongSegment, CitationSegment],
    Field(discriminator="type"),
]


class ParagraphBlock(BaseModel):
    type: Literal["paragraph"] = "paragraph"
    segments: list[Segment]


class StepsBlock(BaseModel):
    """Numbered circle steps, e.g. the FIR filing walkthrough."""

    type: Literal["steps"] = "steps"
    items: list[list[Segment]]


class BulletsBlock(BaseModel):
    """Bullet list with a bold lead-in per item, e.g. the DAO answer."""

    type: Literal["bullets"] = "bullets"
    items: list[list[Segment]]


ContentBlock = Annotated[
    Union[ParagraphBlock, StepsBlock, BulletsBlock],
    Field(discriminator="type"),
]


class Citation(BaseModel):
    key: str
    title: str
    badge: str
    source: str
    text: str
    note: str


IconColorToken = Literal["amber", "blue", "emerald", "purple", "slate"]


class Reference(BaseModel):
    title: str
    subtitle: str
    url: str
    icon: str
    # A semantic token, not a raw Tailwind class: the frontend owns the
    # actual class string so Tailwind's content scanner can see it
    # statically (it can't see strings that only ever exist server-side).
    icon_color: IconColorToken


class CopyQuickAction(BaseModel):
    kind: Literal["copy"] = "copy"
    label: str
    copy_text: str


class PreviewQuickAction(BaseModel):
    kind: Literal["preview"] = "preview"
    label: str
    citation_key: str


class FollowupQuickAction(BaseModel):
    kind: Literal["followup"] = "followup"
    label: str
    question: str


QuickAction = Annotated[
    Union[CopyQuickAction, PreviewQuickAction, FollowupQuickAction],
    Field(discriminator="kind"),
]


class HistoryMessage(BaseModel):
    """A prior turn, as sent back by the client for context."""

    id: str
    role: Literal["user", "assistant"]
    text: str | None = None


class ConversationMemory(BaseModel):
    """Rolling conversation summary, held by the client and echoed each turn.

    Deliberately not stored server-side. These are personal legal questions, and
    keeping nothing is a privacy property rather than a shortcut — it also means
    no session store, no expiry, and nothing to lose on a restart.

    `sections` is written by code from the answer's own citations, never by the
    summariser, so a section number cannot be reworded out of existence by a
    paraphrase.
    """

    summary: str = ""
    sections: list[str] = Field(default_factory=list)
    turns: int = 0
    error: str | None = None


class QueryRequest(BaseModel):
    question: str = Field(examples=["What is the punishment for theft?"])
    history: list[HistoryMessage] = Field(default_factory=list)
    # Optional per-request generator: an alias from GET /api/models, a
    # "backend:model" pair, or a bare model id. Omitted uses the server default.
    #
    # `examples=[None]` is load-bearing: without it Swagger UI fills the field
    # with the literal string "string", which reaches the provider as a model id
    # and comes back as a 404 that reads like an outage rather than a typo.
    model: str | None = Field(
        default=None, examples=[None],
        description="Generator alias from GET /api/models (e.g. 'qwen'), a "
                    "'backend:model' pair, or a bare model id. Omit for the "
                    "server default.")
    refine: bool | None = Field(
        default=None, examples=[None],
        description="Override the query-refiner flag. Omit for the server default.")
    memory: ConversationMemory | None = Field(
        default=None, examples=[None],
        description="The memory returned by the previous answer. Omit to start "
                    "a fresh conversation.")


class Transcription(BaseModel):
    """What was heard, from POST /api/transcribe. Never an answer.

    `confidence` is Deepgram's, for the whole utterance. It is shown to the
    reader rather than acted on: a low score is a reason to look at the words
    before sending, not a threshold the server should silently enforce.
    """

    transcript: str
    confidence: float | None = None
    duration: float | None = None
    model: str | None = None
    language: str | None = None
    request_id: str | None = None


class AssistantMessage(BaseModel):
    id: str
    role: Literal["assistant"] = "assistant"
    heading: str
    subheading: str
    # Which machine produced this, so the client can render the outcomes
    # differently. `unsupported` says only that it is not a grounded answer; it
    # cannot distinguish "outside these Acts" from "which Act did you mean?",
    # and those deserve different treatment on screen.
    kind: Literal["legal", "lookup", "outline", "meta", "general",
                  "out_of_scope", "clarify", "refused"] = "legal"
    unsupported: bool = False
    blocks: list[ContentBlock]
    citations: dict[str, Citation] = Field(default_factory=dict)
    references: list[Reference] = Field(default_factory=list)
    quick_actions: list[QuickAction] = Field(default_factory=list)
    # Send this back with the next question to continue the conversation.
    memory: ConversationMemory | None = None
    # Which model actually answered, and — when it is not the one that was
    # asked for — the alias it replaced because that one was out of quota. A
    # substitution the reader cannot see is a silent change of behaviour.
    generator: str | None = None
    generator_substituted: str | None = None
    # Set only when this question was answered as a follow-up: the standalone
    # rewrite that was actually executed. Shown to the reader, because a wrong
    # rewrite is otherwise a confident answer to a question nobody asked.
    resolved_question: str | None = None
