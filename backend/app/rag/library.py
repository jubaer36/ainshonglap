"""Browsable view of the indexed statute, independent of any question.

Retrieval answers "which sections are relevant". This answers "what does the
corpus contain" — the statute browser, and citation lookup by key.

Backed by the CATALOG — the same rows answers quote from. It used to be built
from `chunks.json`, which made a second copy of the text, and the copies
disagreed for five sections: the chunks glued page-break fragments onto the real
section, so the preview of `penal-s2` showed 1,155 characters where the section
is 188. Reading the catalog, with its continuation fragments excluded, the side
panel shows exactly what an answer cites — from Supabase, like the catalog,
and from the local file only when the catalog falls back.
"""

from __future__ import annotations

import functools
import re



@functools.lru_cache(maxsize=1)
def _library() -> dict[str, dict]:
    """citation key -> section record, in corpus order.

    Continuation fragments are left out: they are parser artifacts that the
    catalog keeps flagged, not provisions anyone should browse to.
    """
    from app.rag import catalog

    rows = catalog.connect().execute(
        "SELECT s.key, s.number, s.title, s.text, s.page_start, s.page_end,"
        "       a.slug, a.name, a.act_no, a.source_pdf,"
        "       c.number AS chapter, c.title AS chapter_title, c.part"
        "  FROM sections s JOIN acts a ON a.id = s.act_id"
        "  LEFT JOIN chapters c ON c.id = s.chapter_id"
        " WHERE s.continuation = 0"
        " ORDER BY a.slug, s.seq").fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        out[r["key"]] = {
            "key": r["key"], "act": r["name"], "act_slug": r["slug"],
            "act_no": r["act_no"], "section": str(r["number"]),
            "section_title": r["title"],
            "part": r["part"], "chapter": r["chapter"],
            "chapter_title": r["chapter_title"],
            "page_start": r["page_start"], "page_end": r["page_end"],
            "source_pdf": r["source_pdf"],
            "text": r["text"],
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


def outline(act_slug: str) -> dict | None:
    """Chapter-by-chapter shape of one act.

    Built from the parsed hierarchy, so "how is this act organised" is answered
    from the document rather than from a model's memory of it. This is what both
    "list the sections" and "summarise the act" run on: the first needs the
    numbers, the second needs something true to summarise.
    """
    rows = [r for r in _library().values() if r["act_slug"] == act_slug]
    if not rows:
        return None
    chapters: dict[str, dict] = {}
    for r in sorted(rows, key=lambda r: _sortkey(r["section"])):
        ch = r.get("chapter") or "-"
        c = chapters.setdefault(ch, {
            "chapter": ch,
            "title": r.get("chapter_title"),
            "part": r.get("part"),
            "sections": [],
        })
        if not c["title"] and r.get("chapter_title"):
            c["title"] = r["chapter_title"]
        c["sections"].append({"key": r["key"], "section": r["section"],
                              "title": r.get("section_title")})
    pages = [r["page_start"] for r in rows if r.get("page_start")]
    return {
        "slug": act_slug,
        "act": rows[0]["act"],
        "act_no": rows[0].get("act_no"),
        "source_pdf": rows[0].get("source_pdf"),
        "sections": len(rows),
        "pages": (min(pages), max(pages)) if pages else None,
        "chapters": list(chapters.values()),
    }


def _sortkey(num: str):
    """`302` before `302A` before `303`, rather than lexicographic."""
    m = re.match(r"(\d+)([A-Z]*)", str(num))
    return (int(m.group(1)), m.group(2)) if m else (10**6, str(num))


def select(act: str | None = None, chapter: str | None = None,
           sections: list[str] | None = None) -> list[dict]:
    """Exact fetch by structural coordinates — the deterministic half.

    A model names the scope (which act, which chapter, which sections); this
    returns precisely those records. Nothing is ranked, nothing is generated,
    and a coordinate that does not exist comes back empty rather than as the
    nearest thing — an LLM naming a chapter that is not in the Act must fail
    visibly, not silently return a different one.
    """
    rows = list(_library().values())
    if act:
        rows = [r for r in rows if r["act_slug"] == act]
    if chapter:
        rows = [r for r in rows if _same_chapter(r.get("chapter"), chapter)]
    if sections:
        want = {str(x).strip().upper() for x in sections}
        rows = [r for r in rows if str(r["section"]).upper() in want]
    return sorted(rows, key=lambda r: (r["act_slug"], _sortkey(r["section"])))


_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}


def roman_to_int(r: str) -> int | None:
    total, prev = 0, 0
    for ch in reversed(str(r).upper()):
        v = _ROMAN.get(ch)
        if v is None:
            return None
        total, prev = (total - v if v < prev else total + v), max(v, prev)
    return total


def _same_chapter(stored: str | None, asked: str) -> bool:
    """"XVI" also matches "16": the Acts number chapters in roman, readers
    often do not."""
    a, b = (stored or "").strip().upper(), str(asked).strip().upper()
    if a == b:
        return True
    ai = roman_to_int(a)
    bi = int(b) if b.isdigit() else roman_to_int(b)
    return ai is not None and bi is not None and ai == bi


def chapters_of(act_slug: str) -> list[dict]:
    o = outline(act_slug)
    return o["chapters"] if o else []


STOP = {"the", "of", "a", "an", "in", "to", "and", "or", "for", "from", "on",
        "all", "some", "state", "list", "show", "me", "what", "which", "are",
        "is", "section", "sections", "chapter", "chapters", "law", "laws",
        "act", "code", "about", "under", "give", "tell"}


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{3,}", (text or "").lower())
            if w not in STOP}


def match_chapter(question: str, act_slug: str | None = None) -> tuple[str, str] | None:
    """(act slug, chapter) whose contents best match the question's wording.

    Deterministic, and it beats asking a small model: "documentary evidence" is
    literally the title of Evidence Act Chapter V, and "murder" is the title of
    Penal Code s.300, which tells you its chapter. A 2.6B model asked the same
    questions picked the wrong Act for one and gave up on the other.

    Chapter titles score higher than section titles — a chapter named for the
    subject is a better answer than one section that mentions it — and a single
    weak overlap is rejected, because a wrong chapter is worse than none.
    """
    q = _tokens(question)
    if not q:
        return None
    slugs = [act_slug] if act_slug else [a["slug"] for a in acts()]
    best, best_score = None, 0.0
    for slug in slugs:
        for c in chapters_of(slug):
            ct = _tokens(c["title"] or "")
            hit = q & ct
            # Weight by how much of the CHAPTER TITLE is matched, not just how
            # many words overlap. Raw counts favour long titles: "documentary
            # evidence" matched "OF THE EXCLUSION OF ORAL BY DOCUMENTARY
            # EVIDENCE" as strongly as "OF DOCUMENTARY EVIDENCE", and the
            # longer, more specific chapter is the wrong answer.
            # Both terms are scored as COVERAGE, never as raw counts, because
            # raw counts track chapter size rather than relevance:
            #   chapter term  how much of the chapter's own title is matched —
            #                 "OF DOCUMENTARY EVIDENCE" beats "OF THE EXCLUSION
            #                 OF ORAL BY DOCUMENTARY EVIDENCE" for "documentary
            #                 evidence", being entirely about it
            #   section term  how much of the QUESTION is found across section
            #                 titles. "murder" and "theft" appear in no chapter
            #                 title at all, only in section titles, so this term
            #                 is the only signal for them and cannot be a mere
            #                 tie-break. Counting distinct question words rather
            #                 than matching sections stops a 86-section chapter
            #                 outscoring a 5-section one on size alone.
            score = 3.0 * len(hit) * (len(hit) / len(ct)) if ct else 0.0
            in_sections, n_hit = set(), 0
            for sec in c["sections"]:
                if (m := q & _tokens(sec["title"] or "")):
                    in_sections |= m
                    n_hit += 1
            score += 3.0 * len(in_sections) / len(q)
            # Bounded tie-break on how many sections match. Coverage alone ties
            # whenever two chapters both mention the word once: "theft" appears
            # in one section of OFFENCES AFFECTING THE HUMAN BODY and in eight
            # of OFFENCES AGAINST PROPERTY, and the second is the answer. Capped
            # so it can only separate equals, never outweigh a title match.
            score += 0.15 * min(n_hit, 8)
            if score > best_score:
                best, best_score = (slug, str(c["chapter"])), score
    return best if best_score >= 3.0 else None


def resolve_act(text: str) -> str | None:
    """The one Act a question names, or None — from the catalog's own list."""
    from app.rag import acts

    return acts.hint_slug(text)
