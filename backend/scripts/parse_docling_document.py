"""Parse a document that is not a numbered statute, by page layout (Docling), into
the same JSON shape as parse_law_pdf.py.
"""

from __future__ import annotations

import pathlib

MAX_WORDS = 250


def _page(item) -> int | None:
    """0-based page of an item, matching parse_law_pdf.py's numbering."""
    prov = (getattr(item, "prov", None) or [None])[0]
    return (prov.page_no - 1) if prov is not None else None


def convert(pdf_path: pathlib.Path, pages: tuple[int, int] | None = None):
    """Docling's document for the whole file, or a 1-based inclusive page range."""
    from docling.document_converter import DocumentConverter

    kw = {"page_range": pages} if pages else {}
    return DocumentConverter().convert(str(pdf_path), **kw).document


def passages(doc) -> list[dict]:
    """Heading-led passages in reading order, each at most MAX_WORDS words."""
    from docling_core.types.doc import (ListItem, SectionHeaderItem, TableItem,
                                        TextItem, TitleItem)

    out: list[dict] = []
    cur: dict | None = None

    def close():
        nonlocal cur
        if cur and cur["parts"]:
            out.append(cur)
        cur = None

    def start(title: str | None, page: int | None, cont: bool = False):
        nonlocal cur
        close()
        cur = {"title": title, "parts": [], "words": 0, "pages": [],
               "cont": cont}
        if page is not None:
            cur["pages"].append(page)

    for item, _level in doc.iterate_items():
        page = _page(item)
        if isinstance(item, (TitleItem, SectionHeaderItem)):
            text = (item.text or "").strip()
            if text:
                start(text, page)
            continue
        if isinstance(item, TableItem):
            text = item.export_to_markdown(doc=doc).strip()
        elif isinstance(item, (TextItem, ListItem)):
            text = (item.text or "").strip()
        else:
            continue
        if not text:
            continue
        words = len(text.split())
        if cur is None:
            start(None, page)
        elif cur["words"] and cur["words"] + words > MAX_WORDS:
            # Too long for one passage: carry the heading into the next one, so
            # a retrieved continuation still says what it is part of.
            start(cur["title"], page, cont=True)
        cur["parts"].append(text)
        cur["words"] += words
        if page is not None:
            cur["pages"].append(page)
    close()
    return out


def to_parsed(doc, act_name: str, act_no: str, pdf_name: str) -> dict:
    """Docling's document in parse_law_pdf.py's output shape."""
    sections = []
    for i, p in enumerate(passages(doc), start=1):
        pages = p["pages"] or [0]
        title = p["title"]
        if title and p["cont"]:
            title = f"{title} (continued)"
        sections.append({
            "section_number": str(i),
            "section_number_to": None,
            "section_title": title,
            "part": None, "part_title": None,
            "chapter": None, "chapter_title": None,
            "page_start": min(pages), "page_end": max(pages),
            "spans_page_break": min(pages) != max(pages),
            "text": "\n\n".join(p["parts"]),
            "children": [],
        })
    return {
        "act": {"name": act_name, "act_no": act_no, "source_pdf": pdf_name},
        "doc_type": "document",
        "parser": "docling",
        "preamble": "",
        "stats": {
            "sections": len(sections),
            "sections_titled": sum(1 for s in sections if s["section_title"]),
            "sections_spanning_pages": sum(1 for s in sections if s["spans_page_break"]),
            "chapters": 0,
        },
        "chapters": [{"type": "chapter", "part": None, "part_title": None,
                      "number": None, "title": None, "sections": sections}],
    }
