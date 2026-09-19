"""Retrieved sections -> the `Citation` shape the frontend already renders.

The API contract was written against the mock module and uses display-oriented
fields (`badge`, `source`, `note`); chunk metadata is provenance-oriented
(`act_no`, `page_start`, `source_pdf`). This is the one place the two are
reconciled, so neither side has to learn the other's vocabulary.

Page numbers come from the PDF the text was parsed out of, so a citation and
the text it cites always come from the same document.
"""

from __future__ import annotations

from app.models.schemas import Citation
from app.rag.pipeline import Retrieved

# Stable url-safe slug per act; the citation key is built from it, so it is
# part of the API contract rather than a display detail.
ACT_SLUGS_BY_NAME = {
    "the code of criminal procedure, 1898": "crpc",
    "the penal code, 1860": "penal",
    "the evidence act, 1872": "evidence",
}

SHORT_NAMES = {
    "the code of criminal procedure, 1898": "CrPC",
    "the penal code, 1860": "Penal Code",
    "the evidence act, 1872": "Evidence Act",
}


def strip_prefix(text: str) -> str:
    """Drop the hierarchy line chunking prepends for the embedder.

    It is `Act, Section N: Title` followed by a blank line — retrieval context,
    not statute. Showing it back to the reader would duplicate the title.
    """
    head, sep, body = text.partition("\n\n")
    return (body if sep and head.startswith(("The ", "A ")) else text).strip()


def to_citation(r: Retrieved) -> Citation:
    m = r.metadata
    short = SHORT_NAMES.get(r.act.strip().lower(), r.act)
    title = f"{short} Section {r.section}"
    if r.section_title:
        title += f" • {r.section_title}"

    act_no = m.get("act_no")
    badge = f"{short} {act_no}".strip() if act_no else short

    pages = m.get("page_start")
    if pages and m.get("page_end") and m["page_end"] != pages:
        pages = f"{pages}-{m['page_end']}"
    src = m.get("source_pdf") or r.act
    source = f"{src} • p. {pages}" if pages else src

    parts = [p for p in (m.get("part"), m.get("chapter_title") or m.get("chapter")) if p]
    note = " › ".join(parts) if parts else f"{r.act}, Section {r.section}"

    return Citation(key=r.key, title=title, badge=badge, source=source,
                    text=strip_prefix(r.text), note=note)


def to_citations(results: list[Retrieved]) -> dict[str, Citation]:
    return {r.key: to_citation(r) for r in results}
