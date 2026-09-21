"""Read side of the structural catalog — exact fetch by coordinates.

The division this module exists to enforce: **a model names the coordinates,
this code fetches them.** Every query here is parameterized and every predicate
is built from validated values, so no model output ever reaches SQLite as SQL.
A planner that hallucinates "Chapter XCIX" produces an empty result, which the
caller reports as "no such chapter" — never the nearest real chapter, which is
what a vector index would have returned.

Opened read-only. Nothing in the serving path writes to the catalog; it is
rebuilt from parsed JSON by `scripts/build_catalog.py` and checked by
`scripts/verify/verify_catalog.py`.

Continuation fragments (`continuation = 1`) are excluded from every listing by
default. They are five parse artifacts where a page break was re-read as a
section heading; they are kept in the table because the text is in the document,
but listing them would show a reader "section 344" twice.
"""

from __future__ import annotations

import functools
import re
import sqlite3

from app.rag import config

_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}

# Qualified with the alias: every query below joins `acts`, which also has an
# `id`, and an unqualified list makes SQLite refuse the statement.
SECTION_COLS = ("s.id, s.key, s.number, s.number_to, s.num_int, s.num_suffix,"
                " s.title, s.text, s.page_start, s.page_end, s.chapter_id,"
                " s.act_id, s.seq, s.continuation")

# The chapter a section sits in, by its printed number and title rather than its
# row id. Citations render "Part › Chapter title" as provenance, so a row that
# carried only `chapter_id` would show an integer where a chapter name belongs.
CHAPTER_COLS = ("c.number AS chapter, c.title AS chapter_title,"
                " c.part AS part, c.part_title AS part_title")
SECTION_FROM = (" FROM sections s JOIN acts a ON a.id = s.act_id"
                " LEFT JOIN chapters c ON c.id = s.chapter_id")


class CatalogMissing(RuntimeError):
    """The catalog has not been built yet."""


@functools.lru_cache(maxsize=1)
def connect() -> sqlite3.Connection:
    if not config.CATALOG.exists():
        raise CatalogMissing(
            f"no catalog at {config.CATALOG} — run scripts/build_catalog.py")
    con = sqlite3.connect(f"file:{config.CATALOG}?mode=ro", uri=True,
                          check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def _rows(sql: str, params: tuple = ()) -> list[dict]:
    return [dict(r) for r in connect().execute(sql, params)]


def _one(sql: str, params: tuple = ()) -> dict | None:
    r = connect().execute(sql, params).fetchone()
    return dict(r) if r else None


# ------------------------------------------------------------------ numbers

def roman_to_int(r: str) -> int | None:
    s = str(r or "").strip().upper()
    if not s or any(ch not in _ROMAN for ch in s):
        return None
    total, prev = 0, 0
    for ch in reversed(s):
        v = _ROMAN[ch]
        total, prev = (total - v if v < prev else total + v), max(v, prev)
    return total


def chapter_int(number: str | None) -> int | None:
    """'XVI' and '16' both -> 16. Readers write arabic; the Acts print roman."""
    s = str(number or "").strip()
    if not s:
        return None
    return int(s) if s.isdigit() else roman_to_int(s)


def split_number(num: str) -> tuple[int | None, str]:
    """'302A' -> (302, 'A'), so ranges and ordering work statutorily."""
    m = re.match(r"\s*(\d+)\s*([A-Za-z]*)", str(num or ""))
    return (int(m.group(1)), m.group(2).upper()) if m else (None, "")


# ------------------------------------------------------------------ acts

def acts() -> list[dict]:
    return _rows("SELECT id, slug, name, act_no, source_pdf, n_chapters,"
                 " n_sections FROM acts ORDER BY slug")


def act(slug: str) -> dict | None:
    return _one("SELECT id, slug, name, act_no, source_pdf, n_chapters,"
                " n_sections FROM acts WHERE slug = ?", (str(slug).lower(),))


@functools.lru_cache(maxsize=1)
def act_slugs() -> frozenset[str]:
    return frozenset(a["slug"] for a in acts())


# ------------------------------------------------------------------ chapters

def chapters(act_slug: str | None = None) -> list[dict]:
    sql = ("SELECT c.id, c.number, c.number_int, c.title, c.part, c.part_title,"
           " c.n_sections, a.slug AS act_slug, a.name AS act"
           " FROM chapters c JOIN acts a ON a.id = c.act_id")
    if act_slug:
        return _rows(sql + " WHERE a.slug = ? ORDER BY c.seq",
                     (str(act_slug).lower(),))
    return _rows(sql + " ORDER BY a.slug, c.seq")


def chapter(act_slug: str, number: str) -> dict | None:
    """One chapter, matched on the printed number or its arabic equivalent.

    Returns None for a chapter that does not exist — the caller reports that,
    rather than falling back to something close. A planner naming a chapter the
    Act does not have must fail visibly.
    """
    n = chapter_int(number)
    rows = _rows(
        "SELECT c.id, c.number, c.number_int, c.title, c.part, c.part_title,"
        " c.n_sections, a.slug AS act_slug, a.name AS act"
        " FROM chapters c JOIN acts a ON a.id = c.act_id"
        " WHERE a.slug = ? AND (UPPER(c.number) = ? OR c.number_int IS ?)",
        (str(act_slug).lower(), str(number).strip().upper(), n))
    return rows[0] if rows else None


# ------------------------------------------------------------------ sections

def _section_query(act_slug: str | None, chapter_id: int | None,
                   numbers: list[str] | None, num_from: str | None,
                   num_to: str | None, include_continuations: bool
                   ) -> tuple[str, list]:
    """Build the WHERE clause from validated values only.

    Every fragment below is a literal in this file; every value is bound. The
    one place a caller's string could reach SQL — the section-number list — is
    bound as placeholders generated from `len()`, not interpolated.
    """
    where, params = ["1 = 1"], []
    if not include_continuations:
        where.append("s.continuation = 0")
    if act_slug:
        where.append("a.slug = ?")
        params.append(str(act_slug).lower())
    if chapter_id is not None:
        where.append("s.chapter_id = ?")
        params.append(int(chapter_id))
    if numbers:
        want = [str(x).strip().upper() for x in numbers if str(x).strip()]
        if want:
            where.append(f"UPPER(s.number) IN ({','.join('?' * len(want))})")
            params += want
    if num_from or num_to:
        # Ranges compare on (num_int, num_suffix) so '378 to 382' includes 379A
        # and stops before 383 — lexicographic comparison would not.
        lo_i, lo_s = split_number(num_from) if num_from else (None, "")
        hi_i, hi_s = split_number(num_to) if num_to else (None, "")
        if lo_i is not None:
            where.append("(s.num_int > ? OR (s.num_int = ? AND s.num_suffix >= ?))")
            params += [lo_i, lo_i, lo_s]
        if hi_i is not None:
            where.append("(s.num_int < ? OR (s.num_int = ? AND s.num_suffix <= ?))")
            params += [hi_i, hi_i, hi_s or "zzz"]
    return " AND ".join(where), params


def sections(act_slug: str | None = None, chapter_number: str | None = None,
             numbers: list[str] | None = None, number_from: str | None = None,
             number_to: str | None = None, limit: int | None = None,
             offset: int = 0, include_continuations: bool = False,
             ) -> tuple[int, list[dict]]:
    """(total, page) for a set of structural coordinates, in statutory order.

    `total` is the count before pagination, so a caller can say "60 of 86"
    rather than presenting a truncated page as the whole chapter.
    """
    chapter_id = None
    if chapter_number:
        ch = chapter(act_slug, chapter_number) if act_slug else None
        if ch is None:
            return 0, []            # named chapter does not exist: empty, not near
        chapter_id = ch["id"]

    where, params = _section_query(act_slug, chapter_id, numbers, number_from,
                                   number_to, include_continuations)
    base = f"{SECTION_FROM} WHERE {where}"
    total = connect().execute(f"SELECT COUNT(*){base}", params).fetchone()[0]

    sql = (f"SELECT {SECTION_COLS}, {CHAPTER_COLS}, a.slug AS act_slug,"
           f" a.name AS act, a.act_no, a.source_pdf{base}"
           " ORDER BY a.slug, s.num_int, s.num_suffix")
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params = params + [int(limit), int(offset)]
    return total, _rows(sql, tuple(params))


def section(act_slug: str, number: str) -> dict | None:
    _, rows = sections(act_slug=act_slug, numbers=[number], limit=2)
    return rows[0] if rows else None


def get(key: str) -> dict | None:
    """One section by citation key ('penal-s302'), the handle the API uses."""
    return _one(
        f"SELECT {SECTION_COLS}, {CHAPTER_COLS}, a.slug AS act_slug,"
        f" a.name AS act, a.act_no, a.source_pdf{SECTION_FROM}"
        " WHERE s.key = ?", (str(key).strip(),))


def find_number(number: str) -> list[dict]:
    """Every Act containing this section number.

    All three Acts have a section 25, so "show section 25" is ambiguous. The
    caller asks which Act was meant rather than picking one.
    """
    _, rows = sections(numbers=[number])
    return rows


def subsections(section_id: int) -> list[dict]:
    return _rows(
        "SELECT id, parent_id, number, type, text, depth, seq FROM subsections"
        " WHERE section_id = ? ORDER BY seq", (int(section_id),))


# ------------------------------------------------------------------ search

_FTS_SAFE = re.compile(r'[^\w\s]')


def search(query: str, act_slug: str | None = None, limit: int = 20
           ) -> list[dict]:
    """Exact keyword/phrase search over titles and text (FTS5).

    A complement to dense retrieval, not a replacement: this is what finds rare
    terms, proper nouns and numbers that embeddings smooth away. Punctuation is
    stripped because FTS5 would otherwise read it as query syntax — a user
    typing `theft OR "` must not produce a syntax error.
    """
    q = _FTS_SAFE.sub(" ", str(query or "")).strip()
    if not q:
        return []
    terms = " OR ".join(t for t in q.split() if t)
    sql = (f"SELECT {SECTION_COLS}, a.slug AS act_slug, a.name AS act,"
           " bm25(sections_fts) AS score"
           " FROM sections_fts f JOIN sections s ON s.id = f.rowid"
           " JOIN acts a ON a.id = s.act_id"
           " WHERE sections_fts MATCH ? AND s.continuation = 0")
    params: list = [terms]
    if act_slug:
        sql += " AND a.slug = ?"
        params.append(str(act_slug).lower())
    sql += " ORDER BY score LIMIT ?"
    params.append(int(limit))
    try:
        return _rows(sql, tuple(params))
    except sqlite3.OperationalError:
        return []          # malformed MATCH despite sanitising: no hits, not a 500


# ------------------------------------------------------------------ cross-refs

def references_from(section_id: int) -> list[dict]:
    """Provisions this section points at, resolved where they exist in corpus."""
    return _rows(
        "SELECT x.target_number, x.kind, x.target_to, x.raw,"
        " a.slug AS target_act, s.key AS target_key, s.title AS target_title"
        " FROM cross_references x"
        " LEFT JOIN acts a ON a.id = x.target_act_id"
        " LEFT JOIN sections s ON s.act_id = x.target_act_id"
        "   AND s.number = x.target_number AND s.continuation = 0"
        " WHERE x.section_id = ? ORDER BY x.id", (int(section_id),))


def references_to(act_slug: str, number: str) -> list[dict]:
    """Provisions that point at this one — the incoming edges."""
    return _rows(
        "SELECT s.key, s.number, s.title, a.slug AS act_slug, x.raw"
        " FROM cross_references x"
        " JOIN sections s ON s.id = x.section_id AND s.continuation = 0"
        " JOIN acts a ON a.id = s.act_id"
        " JOIN acts ta ON ta.id = x.target_act_id"
        " WHERE ta.slug = ? AND UPPER(x.target_number) = ?"
        " ORDER BY a.slug, s.num_int, s.num_suffix",
        (str(act_slug).lower(), str(number).strip().upper()))


# ------------------------------------------------------------------ shape

def outline(act_slug: str) -> dict | None:
    """Chapter-by-chapter shape of one Act — what a whole-Act overview runs on.

    Top-k retrieval cannot represent an Act: 557 sections is not a top-5. This
    returns the real hierarchy so a summary describes the document rather than
    the model's memory of it.
    """
    a = act(act_slug)
    if not a:
        return None
    chs = chapters(act_slug)
    for c in chs:
        _, rows = sections(act_slug=act_slug, chapter_number=c["number"])
        c["sections"] = [{"key": r["key"], "section": r["number"],
                          "title": r["title"]} for r in rows]
    pages = connect().execute(
        "SELECT MIN(page_start), MAX(page_end) FROM sections s"
        " JOIN acts a ON a.id = s.act_id WHERE a.slug = ?",
        (str(act_slug).lower(),)).fetchone()
    return {"slug": a["slug"], "act": a["name"], "act_no": a["act_no"],
            "source_pdf": a["source_pdf"], "sections": a["n_sections"],
            "pages": tuple(pages) if pages and pages[0] else None,
            "chapters": chs}


def manifest() -> dict:
    """What the corpus contains — the capability facts, read from the catalog.

    Both the planner prompt and the "what can you do" answer are built from
    this, so neither can drift from what is actually indexed.
    """
    return {
        "acts": [{"slug": a["slug"], "name": a["name"], "act_no": a["act_no"],
                  "chapters": a["n_chapters"], "sections": a["n_sections"]}
                 for a in acts()],
        "sections": sum(a["n_sections"] for a in acts()),
        "chapters": sum(a["n_chapters"] for a in acts()),
    }


def chapter_listing(act_slug: str | None = None) -> str:
    """The real chapter list, as text for a planner prompt.

    A planner shown the true list selects from what exists; one asked to recall
    chapter numbers invents them.
    """
    lines: list[str] = []
    current = None
    for c in chapters(act_slug):
        if c["act_slug"] != current:
            current = c["act_slug"]
            lines.append(f"[{current}] {c['act']}")
        lines.append(f"  {c['number']} — {c['title'] or '(untitled)'}"
                     f" ({c['n_sections']} sections)")
    return "\n".join(lines)
