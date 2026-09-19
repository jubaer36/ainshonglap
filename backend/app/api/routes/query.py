"""The assistant endpoint: question in, cited answer or refusal out."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.models.schemas import AssistantMessage, QueryRequest
from app.rag.answer import answer
from app.rag.present import to_message

log = logging.getLogger(__name__)
router = APIRouter(tags=["assistant"])


@router.post("/api/query", response_model=AssistantMessage)
async def query(payload: QueryRequest) -> AssistantMessage:
    """Retrieve, generate, verify citations, present.

    A refusal is a 200 with `unsupported=true`, not an error: "the Acts do not
    cover this" is an answer, and the frontend renders it as one. A 502 here
    means the generator itself was unreachable — a different failure, and one
    the caller can retry.
    """
    q = payload.question.strip()
    if not q:
        raise HTTPException(422, "question is empty")
    try:
        return to_message(answer(q, model=payload.model,
                                 refine_query=payload.refine))
    except Exception as exc:
        log.exception("generation failed")
        raise HTTPException(502, f"generator unavailable: {type(exc).__name__}") from exc
