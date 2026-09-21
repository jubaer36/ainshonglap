"""The assistant endpoint: question in, cited answer or refusal out."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from app.models.schemas import AssistantMessage, QueryRequest
from app.rag import config
from app.rag.present import to_message

log = logging.getLogger(__name__)
router = APIRouter(tags=["assistant"])


@router.post("/api/query", response_model=AssistantMessage)
async def query(payload: QueryRequest) -> AssistantMessage:
    """Plan, execute, verify citations, present.

    Two paths, chosen by `RAG_PLAN`:

      on   a model plans the question into a category and a set of coordinates,
           and validated code executes it (`app/rag/plan.py`, `execute.py`)
      off  the older pattern-first router (`app/rag/refine.py`)

    The switch exists so the planner can be turned off in one place if a Groq
    outage makes planning unavailable — not as a permanent fork. The old router
    goes once the planner has run in production for a while.

    A refusal is a 200 with `unsupported=true`, not an error: "the Acts do not
    cover this" is an answer, and the frontend renders it as one. A 502 means
    the generator itself was unreachable — a different failure, and retryable.
    """
    q = payload.question.strip()
    if not q:
        raise HTTPException(422, "question is empty")
    model = (payload.model or "").strip() or None
    try:
        if config.PLAN_QUERIES:
            from app.rag.execute import answer_question

            return to_message(answer_question(q, model=model))

        from app.rag.answer import answer

        return to_message(answer(q, model=model, refine_query=payload.refine))
    except Exception as exc:
        # A model the provider does not have is the caller's mistake, not an
        # outage, and 502 "generator unavailable" sends whoever sees it looking
        # for a broken service. Unknown ids are allowed through to the provider
        # on purpose (config.resolve_generator), so this is where a typo
        # surfaces — including Swagger UI's own "string" placeholder.
        if _unknown_model(exc):
            known = ", ".join(sorted(config.GENERATORS))
            raise HTTPException(
                422, f"unknown model {model!r} — use an alias from "
                     f"GET /api/models ({known}), a 'backend:model' pair, or "
                     f"omit the field for the server default") from exc
        log.exception("generation failed")
        raise HTTPException(502, f"generator unavailable: {type(exc).__name__}") from exc


def _unknown_model(exc: Exception) -> bool:
    """Did the provider reject the model id itself?

    Matched on the provider's own wording rather than on the HTTP status: a 404
    from these endpoints is always about the model, but the message is what
    distinguishes "no such model" from "no access to this model", and both are
    the caller's problem rather than ours.
    """
    text = str(exc).lower()
    return "model_not_found" in text or "does not exist or you do not have access" in text


@router.get("/api/plan", tags=["assistant"])
async def plan_only(q: str = Query(..., min_length=1),
                    model: str | None = None) -> dict:
    """What the planner decided, without answering.

    The planner picks the machine that answers every question, so when an answer
    looks wrong the first thing worth seeing is whether it was *planned* wrong.
    This returns that, and `notes` records everything validation changed — a
    chapter renumbered to the Act's own spelling, a coordinate rejected for not
    existing. Read-only and cheap: one small model call, no retrieval, no
    generation.
    """
    from app.rag.plan import make_plan

    model = (model or "").strip() or None
    try:
        p = make_plan(q.strip(), model=model)
    except Exception as exc:                        # pragma: no cover - defensive
        if _unknown_model(exc):
            known = ", ".join(sorted(config.GENERATORS))
            raise HTTPException(422, f"unknown model {model!r} — use one of "
                                     f"{known}, or omit the field") from exc
        log.exception("planning failed")
        raise HTTPException(502, f"planner unavailable: {type(exc).__name__}") from exc

    # make_plan never raises — a planner outage degrades to semantic retrieval
    # and records why in `error`. So a bad model id arrives here as a fallback
    # plan rather than an exception, and has to be caught on the way out.
    if p.error and _unknown_model(Exception(p.error)):
        known = ", ".join(sorted(config.GENERATORS))
        raise HTTPException(422, f"unknown model {model!r} — use one of "
                                 f"{known}, or omit the field")

    return {
        "question": p.question,
        "category": p.category,
        "operation": p.operation or None,
        "act": p.act,
        "chapter": p.chapter,
        "sections": p.sections,
        "range": ([p.section_from, p.section_to]
                  if p.section_from and p.section_to else None),
        "search_query": p.search_query or None,
        "terms": p.terms,
        "needs_clarification": p.needs_clarification,
        "clarification": p.clarification or None,
        # How the plan was produced and what validation did to it. `by` is
        # "fallback" when the planner call failed and the question degraded to
        # plain semantic retrieval.
        "by": p.by,
        "notes": p.notes,
        "error": p.error,
    }
