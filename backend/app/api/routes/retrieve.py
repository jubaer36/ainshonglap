"""Raw retrieval, exposed on its own so it can be inspected without an LLM.

`/api/query` still answers from the mock module: answer generation is step 11
of `plan.md` and does not exist yet, and synthesising an answer here would put
un-generated prose behind a real citation, which is the exact failure the whole
project is built to avoid. This endpoint returns what retrieval actually found,
nothing more.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
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
async def retrieve_sections(
    q: str = Query(..., min_length=1, description="the question"),
    k: int = Query(config.FINAL_K, ge=1, le=50),
    rerank: bool = Query(True, description="false gives the dense-only ordering"),
) -> RetrieveResponse:
    hits = retrieve(q, k=k, rerank=rerank)
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
