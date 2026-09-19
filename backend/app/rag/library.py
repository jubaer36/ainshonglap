"""Browsable view of the indexed statute, independent of any question.

Retrieval answers "which sections are relevant". This answers "what does the
corpus contain" — the statute browser, and citation lookup by key.

Backed by the same `chunks.json` the index was built from, so what is browsable
and what is retrievable cannot drift apart. Child (subsection) vectors are
skipped here: they exist to be retrieved, not to be listed as separate
provisions.
"""

from __future__ import annotations

import functools

from app.rag.citations import ACT_SLUGS_BY_NAME, strip_prefix
from app.rag.store import load_store


@functools.lru_cache(maxsize=1)
def _library() -> dict[str, dict]:
    """citation key -> section record, in corpus order."""
    _, chunks, _ = load_store()
    out: dict[str, dict] = {}
    for c in chunks:
        m = c["metadata"]
        if m.get("granularity") == "child":
            continue
        slug = ACT_SLUGS_BY_NAME.get(m["act"].strip().lower(), "act")
        key = f"{slug}-s{m['section']}"
        if key in out:            # a section split across several chunks
            out[key]["text"] += "\n" + strip_prefix(c["text"])
            continue
        out[key] = {
            "key": key, "act": m["act"], "act_slug": slug,
            "act_no": m.get("act_no"), "section": str(m["section"]),
            "section_title": m.get("section_title"),
            "part": m.get("part"), "chapter": m.get("chapter"),
            "chapter_title": m.get("chapter_title"),
            "page_start": m.get("page_start"), "page_end": m.get("page_end"),
            "source_pdf": m.get("source_pdf"),
            "text": strip_prefix(c["text"]),
        }
    return out


def acts() -> list[dict]:
    """One row per act, with how much of it is indexed."""
    agg: dict[str, dict] = {}
    for rec in _library().values():
        a = agg.setdefault(rec["act_slug"], {
            "slug": rec["act_slug"], "name": rec["act"],
            "act_no": rec["act_no"], "sections": 0,
            "source_pdf": rec["source_pdf"],
        })
        a["sections"] += 1
    return sorted(agg.values(), key=lambda a: a["slug"])


def sections(act: str | None = None, q: str | None = None,
             limit: int = 50, offset: int = 0) -> tuple[int, list[dict]]:
    """(total, page). `q` is a plain substring filter over number and title —
    a browser's find box, deliberately not the retrieval path."""
    rows = list(_library().values())
    if act:
        rows = [r for r in rows if r["act_slug"] == act]
    if q:
        needle = q.strip().lower()
        rows = [r for r in rows
                if needle in r["section"].lower()
                or needle in (r["section_title"] or "").lower()]
    return len(rows), rows[offset : offset + limit]


def get(key: str) -> dict | None:
    return _library().get(key)
