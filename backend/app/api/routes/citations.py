"""Statute browsing and citation lookup — the corpus, not a conversation.

Served from the same chunks the index was built from, so a section the browser
shows is a section retrieval can return.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.rag import library

router = APIRouter(prefix="/api", tags=["corpus"])


class ActSummary(BaseModel):
    slug: str
    name: str
    act_no: str | None = None
    sections: int
    source_pdf: str | None = None


class SectionSummary(BaseModel):
    key: str
    act: str
    act_slug: str
    section: str
    section_title: str | None = None
    chapter: str | None = None
    page_start: int | None = None


class SectionDetail(SectionSummary):
    act_no: str | None = None
    part: str | None = None
    chapter_title: str | None = None
    page_end: int | None = None
    source_pdf: str | None = None
    text: str


class SectionPage(BaseModel):
    total: int
    limit: int
    offset: int
    results: list[SectionSummary]


@router.get("/acts", response_model=list[ActSummary])
def list_acts() -> list[ActSummary]:
    """The indexed acts and how many sections of each are in the index."""
    return [ActSummary(**a) for a in library.acts()]


@router.get("/sections", response_model=SectionPage)
def list_sections(
    act: str | None = Query(None, description="act slug: crpc, penal, evidence"),
    q: str | None = Query(None, description="substring of section number or title"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> SectionPage:
    total, rows = library.sections(act=act, q=q, limit=limit, offset=offset)
    return SectionPage(total=total, limit=limit, offset=offset,
                       results=[SectionSummary(**r) for r in rows])


@router.get("/sections/{key}", response_model=SectionDetail)
def get_section(key: str) -> SectionDetail:
    """One section by citation key, e.g. `penal-s379`.

    This is what a citation in an answer links to, so the text shown here is the
    same text the claim was verified against.
    """
    rec = library.get(key)
    if not rec:
        raise HTTPException(404, f"no section with key {key!r}")
    return SectionDetail(**rec)
