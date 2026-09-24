"""GET /api/retrieve — the retrieval step on its own, with scores, so it can be
inspected without generating an answer.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.models.schemas import Citation
from app.rag import config
from app.rag.citations import to_citations
from app.rag.pipeline import retrieve

router = APIRouter()


class RetrievedSection(BaseModel):
    key: str
    act: str
    section: str
    section_title: str | None = None
    rank: int
    score: float
    dense_score: float


class RetrieveResponse(BaseModel):
    question: str
    reranked: bool
    results: list[RetrievedSection]
    citations: dict[str, Citation]


@router.get("/api/retrieve", response_model=RetrieveResponse)
def retrieve_sections(
    q: str = Query(..., min_length=1, description="the question"),
    k: int = Query(config.FINAL_K, ge=1, le=50),
    rerank: bool = Query(True, description="false gives the dense-only ordering"),
    profile: str | None = Query(None, description="retrieval profile (E21); "
                                "omit for the server default"),
) -> RetrieveResponse:
    try:
        hits = retrieve(q, k=k, rerank=rerank, profile=profile)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return RetrieveResponse(
        question=q,
        reranked=rerank,
        results=[
            RetrievedSection(
                key=r.key, act=r.act, section=r.section,
                section_title=r.section_title, rank=r.rank,
                score=r.score, dense_score=r.dense_score,
            )
            for r in hits
        ],
        citations=to_citations(hits),
    )
