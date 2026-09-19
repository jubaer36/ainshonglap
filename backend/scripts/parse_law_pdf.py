"""Parse a Bangladesh law PDF into hierarchical JSON — text and structure both
taken from the PDF itself.

    Act -> Part -> Chapter -> Section -> Subsection -> Clause -> Subclause
                                      +- Explanation / Illustration
                                      +- Exception / Proviso

Everything here exists because of a failure observed in these actual PDFs:

  page-split sections   Body lines from every page are sorted into ONE ordered
                        stream before any section regex runs, so a page break is
                        just whitespace mid-sentence. Parsing per page is what
                        made other tools emit the second half of a split section
                        as a new section.

  running headers       Detected by CONTENT, never by position. A "top 11% of the
                        page" rule looks right on the Penal Code (header at
                        y=93.7) and silently eats statutory text from CrPC, whose
                        body starts at y=72.3 with no running header at all.

  footnotes             Detected by PROPORTIONAL font size plus position. The
                        tiers differ per document and per page (CrPC body 9.0 /
                        gloss 8.5; Penal p45 body 11.0 / gloss 10.0; Penal p52
                        body 10.7 / gloss 9.7 / footnote 8.8), so an absolute
                        cutoff deleted 341 lines on the Penal Code — every gloss
                        note and every Illustration.

  gloss/body columns    Detected per page, never hardcoded. The body column is
                        the x-cluster containing section-number lines; the gloss
                        is the narrower one. CrPC keeps the gloss on the left
                        throughout; the Penal Code mirrors it onto the outer
                        margin, swapping sides every page.

  gloss -> section      Paired by vertical overlap of real coordinates. In the
                        linear text stream the note leads its section on some
                        pages and trails it on others.

  contents pages        Skipped. They mimic sections (`154. Information in
                        cognizable cases`) with no body, and unfiltered they
                        become phantom duplicate sections.

Run (from backend/, ainshonglap conda env active):

    python scripts/parse_law_pdf.py --act penal
    python scripts/parse_law_pdf.py --act crpc --pages 20      # first 20 pages
    python scripts/parse_law_pdf.py --all
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import statistics
from dataclasses import dataclass, field

import pymupdf

LAWS = pathlib.Path(__file__).resolve().parents[2] / "Laws" / "main"
OUT = LAWS / "parsed"

ACTS = {
    "crpc": (
        "The Code of Criminal Procedure, 1898",
        "V of 1898",
        "The Code of Criminal Procedure, 1898 (bdcode.gov.bd copy).pdf",
    ),
    "penal": ("The Penal Code, 1860", "XLV of 1860", "The Penal Code, 1860.pdf"),
    "evidence": ("The Evidence Act, 1872", "I of 1872", "The Evidence Act, 1872.pdf"),
}

# "154." / "17A." / "265D.(1)" / "26[ 17A." (amendment-inserted section)
SECTION_RE = re.compile(r"^\s*(?:\d{1,3}\s*\[\s*)?(\d{1,3}[A-Z]{0,2})\.\s*")
PART_RE = re.compile(r"^\s*(?:\d{1,3}\s*)?PART\s+([IVXLC]+)\s*$", re.I)
CHAPTER_RE = re.compile(r"^\s*(?:\d{1,3}\s*)?CHAPTER\s+([IVXLC]+)\s*$", re.I)

SUBSEC_RE = re.compile(r"(?:(?<=\s)|^)\((\d{1,2})\)\s")
CLAUSE_RE = re.compile(r"(?:(?<=\s)|^)\(([a-z]{1,3})\)\s")
BLOCK_RE = re.compile(
    r"(?:(?<=\s)|^)(Illustrations?|Explanations?|Exception|Provided)\b"
)
ROMAN = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii"]


@dataclass
class Line:
    page: int
    x0: float
    x1: float
    y0: float
    y1: float
    size: float
    text: str

    @property
    def width(self) -> float:
        return self.x1 - self.x0


@dataclass
class Note:
    page: int
    y0: float
    y1: float
    text: str


@dataclass
class Section:
    number: str
    title: str | None = None
    part: str | None = None
    part_title: str | None = None
    chapter: str | None = None
    chapter_title: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    lines: list[Line] = field(default_factory=list)

    @property
    def text(self) -> str:
        return norm(" ".join(l.text for l in self.lines))


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


# --------------------------------------------------------------------------
# 1. extraction
# --------------------------------------------------------------------------
def extract_lines(doc: pymupdf.Document, start: int, limit: int | None) -> list[Line]:
    last = len(doc) if limit is None else min(start + limit, len(doc))
    lines: list[Line] = []
    for pno in range(start, last):
        for block in doc[pno].get_text("dict")["blocks"]:
            for ln in block.get("lines", []):
                text = "".join(s["text"] for s in ln["spans"]).strip()
                if not text:
                    continue
                size = max((s["size"] for s in ln["spans"]), default=0.0)
                x0, y0, x1, y1 = ln["bbox"]
                lines.append(Line(pno, x0, x1, y0, y1, size, text))
    return lines


# Below this many characters per page, PyMuPDF is reading an image, not text.
# A scanned statute yields near-zero: there are no text objects to extract, and
# every downstream rule here keys off text, so the parse does not fail loudly —
# it produces an empty act. Docling is the fallback because it OCRs; PyMuPDF
# cannot, at any setting.
TEXT_DENSITY_FLOOR = 120


def text_density(doc: pymupdf.Document, sample: int = 8) -> float:
    """Mean extractable characters per page over the first `sample` pages."""
    n = min(sample, len(doc))
    if not n:
        return 0.0
    return sum(len(doc[i].get_text("text").strip()) for i in range(n)) / n


def extract_lines_docling(pdf_path: pathlib.Path, start: int,
                          limit: int | None) -> list[Line]:
    """Same Line shape, via Docling's OCR pipeline.

    Used only for PDFs PyMuPDF cannot read at all, and measured to be much worse
    wherever PyMuPDF works. On the first 6 pages of the Borstal Schools Act:

        PyMuPDF   247 lines, 13 section starts (ss.1-13)
        Docling    61 lines,  5 section starts (ss.9-13 only)

    It dropped three quarters of the lines and the first eight sections, which
    reproduces the failure parsing-plan.md rejected it for. This is not an
    alternative parser; it is the only one that reads an image at all, and on a
    scan a degraded parse beats an empty one.

    Geometry is synthesised rather than reported: OCR coordinates are not
    reliable enough for the gloss-column detection this parser does on native
    PDFs, so scanned documents are treated as single-column. The section,
    subsection and clause rules are text-based and apply unchanged.
    """
    from docling.document_converter import DocumentConverter

    doc = DocumentConverter().convert(str(pdf_path)).document
    lines: list[Line] = []
    for item, _level in doc.iterate_items():
        text = (getattr(item, "text", "") or "").strip()
        if not text:
            continue
        prov = (getattr(item, "prov", None) or [None])[0]
        pno = (getattr(prov, "page_no", 1) - 1) if prov else 0
        if pno < start or (limit is not None and pno >= start + limit):
            continue
        bbox = getattr(prov, "bbox", None) if prov else None
        y = getattr(bbox, "t", len(lines)) if bbox else len(lines)
        for i, part in enumerate(text.split("\n")):
            part = part.strip()
            if part:
                lines.append(Line(pno, 0.0, 100.0, y + i, y + i + 1, 10.0, part))
    return lines


def strip_furniture(
    lines: list[Line], doc: pymupdf.Document, act_name: str
) -> tuple[list[Line], list[Line]]:
    base = statistics.mode([round(l.size, 1) for l in lines]) if lines else 0.0
    by_page: dict[int, list[Line]] = {}
    for l in lines:
        by_page.setdefault(l.page, []).append(l)

    act_words = {w for w in re.findall(r"\w+", act_name.lower()) if w not in {"the", "of"}}

    def is_header(l: Line) -> bool:
        # 18%, not 12%: the Evidence Act's running header sits at y=123 on an
        # 842pt page (14.7%). The test below is content-specific enough that a
        # looser band is safe.
        if l.y0 >= doc[l.page].rect.height * 0.18:
            return False
        t = l.text.strip()
        if re.fullmatch(r"\d{1,4}", t):
            return True
        words = set(re.findall(r"\w+", t.lower()))
        return bool(words) and len(words) <= 6 and len(words & act_words) >= 2

    def is_small(l: Line) -> bool:
        return l.size < base * 0.88



    # Whole-page endnote apparatus. The CrPC closes with 24 pages of amendment
    # notes set entirely in the footnote size (7.6pt against a 9.0pt body).
    # The per-line rule below spares anything in the top 55% of a page — right
    # for a footnote block under real text, wrong for a page that is nothing but
    # notes. Left in, those 24 pages were appended to the last open section,
    # giving s.565 10,918 words (24 pages) of endnotes and a copyright footer.
    by_page: dict[int, list[Line]] = {}
    for l in lines:
        by_page.setdefault(l.page, []).append(l)
    note_pages = {
        p
        for p, pl in by_page.items()
        if len(pl) >= 8 and sum(1 for l in pl if is_small(l)) / len(pl) >= 0.9
    }

    def is_footnote(l: Line) -> bool:
        if l.page in note_pages:
            return True
        return is_small(l) and l.y0 > doc[l.page].rect.height * 0.55

    kept, dropped = [], []
    for l in lines:
        (dropped if (is_header(l) or is_footnote(l)) else kept).append(l)
    return kept, dropped


def find_body_start(kept: list[Line], first: int, last: int) -> int:
    """First page of the body — everything before it is front-matter/contents.

    Contents pages cannot be identified one at a time. A body page carrying
    short definitional sections (Penal p37: "10. The word 'man' denotes...",
    ss.8-19 all one sentence each) is indistinguishable from a contents page by
    any per-page measure — same numbered-line ratio, same short lines. Two
    earlier attempts failed exactly there: an average-length rule left 13
    contents pages in (≈250 phantom sections), and a longest-line rule then
    threw p37 out (losing ss.8-19 and 43-47).

    What *is* reliable: contents form one contiguous run at the front. So find
    where the body starts and cut once. A body page has more full-measure prose
    lines than numbered ones; requiring a sustained run of them avoids tripping
    on a single dense contents page.

    Measured: Penal pages 0-33 score 0-7 prose lines against 7-21 numbered;
    from p34 on it is 5-26 prose against 0-12. CrPC has no contents pages at
    all — every page scores 8-28 prose — and correctly yields 0 here.
    """
    RUN, NEEDED = 4, 3

    def prose(pno: int) -> int:
        pl = [l for l in kept if l.page == pno]
        return sum(1 for l in pl if len(l.text) >= 55 and not SECTION_RE.match(l.text))

    scores = {p: prose(p) for p in range(first, last)}
    for p in range(first, last):
        window = [scores.get(q, 0) for q in range(p, min(p + RUN, last))]
        if len(window) != RUN:
            continue
        # The candidate page must itself be a body page. Tolerating a sparse
        # FIRST page let the last contents page open the run — Evidence started
        # at p9 and the Penal Code at p33, one page early each, pulling a whole
        # page of contents entries in as duplicate title-only "sections"
        # (Evidence ss.160-167, Penal ss.503-511).
        if window[0] < 5:
            continue
        # A sparse page LATER in the window is fine: the Evidence Act's p13 is
        # near-empty, and requiring all four started that act 4 pages late,
        # dropping ss.1-3 with the title and preamble.
        if sum(1 for s in window if s >= 5) >= NEEDED:
            return p
    return first


def detect_columns(page_lines: list[Line]) -> tuple[float, float] | None:
    """(body_x0, gloss_x0) for a page, or None if it is not two-column.

    Body is found by CONTENT — the cluster holding section-number lines — which
    is what makes this work on both the CrPC (gloss always left) and the Penal
    Code (gloss on the outer margin, swapping sides every page).
    """
    if not page_lines:
        return None
    clusters: dict[float, list[Line]] = {}
    for l in page_lines:
        clusters.setdefault(round(l.x0 / 12) * 12, []).append(l)
    if len(clusters) < 2:
        return None

    def rank(item):
        _, ls = item
        return (
            1 if any(SECTION_RE.match(l.text) for l in ls) else 0,
            statistics.mean(l.width for l in ls),
        )

    body_x0 = max(clusters.items(), key=rank)[0]
    body_w = statistics.mean(l.width for l in clusters[body_x0])
    cands = [
        (k, ls)
        for k, ls in clusters.items()
        if k != body_x0 and statistics.mean(l.width for l in ls) < body_w * 0.6
    ]
    if not cands:
        return None
    return body_x0, max(cands, key=lambda kv: len(kv[1]))[0]


def group_notes(lines: list[Line], gap: float = 3.0) -> list[Note]:
    """Gloss notes wrap over 2-6 lines; merge them back into one note.

    The gap threshold is tight on purpose. Within a single wrapped note the
    lines sit ~0.5pt apart; between two adjacent notes it is ~6pt. A 6.0
    threshold sat exactly on that boundary and swallowed the next note —
    Penal p46 merged "Injury" into "Legally bound to do", moving its anchor
    26pt away from s.44 and leaving that section untitled.
    """
    notes: list[Note] = []
    for l in sorted(lines, key=lambda l: (l.page, l.y0)):
        if notes and notes[-1].page == l.page and l.y0 - notes[-1].y1 <= gap:
            notes[-1].text += " " + l.text
            notes[-1].y1 = l.y1
        else:
            notes.append(Note(l.page, l.y0, l.y1, l.text))
    for n in notes:
        n.text = norm(n.text)
    return notes


# --------------------------------------------------------------------------
# 2. sub-structure within a section
# --------------------------------------------------------------------------
def succ(kind: str, val: str) -> str | None:
    if kind == "num":
        return str(int(val) + 1)
    if kind == "roman":
        i = ROMAN.index(val)
        return ROMAN[i + 1] if i + 1 < len(ROMAN) else None
    if len(val) == 1:
        return chr(ord(val) + 1)
    return val[:-1] + chr(ord(val[-1]) + 1)


def split_children(text: str) -> list[dict]:
    """Nest (1) -> (a) -> (i) using a stack keyed on each level's expected next
    value. That is what disambiguates "(i)": roman one, or the letter i after
    (h)? Whichever level already expects it wins; only otherwise does a new
    level open.
    """
    marks: list[tuple[int, str, str]] = []
    for m in SUBSEC_RE.finditer(text):
        marks.append((m.start(), "num", m.group(1)))
    for m in CLAUSE_RE.finditer(text):
        marks.append((m.start(), "alpha", m.group(1)))
    for m in BLOCK_RE.finditer(text):
        marks.append((m.start(), "block", m.group(1)))
    marks.sort()
    if not marks:
        return []

    root: list[dict] = []
    stack: list[dict] = []

    for i, (pos, kind, val) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        body = text[pos:end].strip()

        if kind == "block":
            # Explanations/Illustrations/Exceptions/Provisos belong to the
            # SECTION, not to whichever subsection happened to be open, and they
            # terminate any list before them.
            root.append({"type": val.lower().rstrip("s"), "text": body})
            del stack[:]
            continue

        if kind == "alpha" and len(val) > 1 and val in ROMAN:
            kind = "roman"

        node = {
            "type": {"num": "subsection", "alpha": "clause", "roman": "subclause"}[kind],
            "number": val,
            "text": body,
            "children": [],
        }

        hit = next(
            (
                j
                for j in range(len(stack) - 1, -1, -1)
                if stack[j]["kind"] == kind and stack[j]["expect"] == val
            ),
            None,
        )
        if hit is not None:
            del stack[hit:]
            parent = stack[-1]["node"]["children"] if stack else root
        elif val in ("1", "a", "i") and stack:
            parent = stack[-1]["node"]["children"]
        else:
            del stack[:]
            parent = root
        parent.append(node)
        stack.append({"kind": kind, "expect": succ(kind, val), "node": node})

    def prune(nodes: list[dict]) -> list[dict]:
        for n in nodes:
            if "children" in n:
                n["children"] = prune(n["children"])
                if not n["children"]:
                    del n["children"]
        return nodes

    return prune(root)


# --------------------------------------------------------------------------
# 3. the parse
# --------------------------------------------------------------------------
def slugify(name: str) -> str:
    """Filename -> a short stable key. This becomes the citation prefix
    (`penal-s379`), so it is derived once from the source and then fixed."""
    stem = re.sub(r"\.pdf$", "", name, flags=re.I)
    stem = re.sub(r"\(.*?\)", "", stem)                 # drop "(bdcode.gov.bd copy)"
    stem = re.sub(r"[^A-Za-z0-9]+", "-", stem).strip("-").lower()
    stem = re.sub(r"^(the)-", "", stem)
    return re.sub(r"-\d{4}$", "", stem) or "act"


def parse(act_key: str, start: int = 0, limit: int | None = None, *,
          pdf: pathlib.Path | None = None, act_name: str | None = None,
          act_no: str | None = None, backend: str = "auto") -> dict:
    """Parse one act's PDF into the hierarchical JSON the pipeline consumes.

    `act_key` selects a known act; `pdf` overrides it with any legal PDF, so
    ingesting a new document is a command rather than a source edit. The
    structural rules are content-based (section numbering, font size, column
    geometry) and carry no per-act knowledge, so an unseen act goes through the
    same path — verify_parsed.py is what says whether it came out right.
    """
    if pdf is not None:
        pdf_path = pathlib.Path(pdf)
        if not pdf_path.is_absolute():
            pdf_path = (LAWS / pdf_path) if (LAWS / pdf_path).exists() else pdf_path.resolve()
        if not pdf_path.exists():
            raise SystemExit(f"no such PDF: {pdf_path}")
        act_name = act_name or re.sub(r"\.pdf$", "", pdf_path.name, flags=re.I)
        act_no = act_no or ""
        pdf_name = pdf_path.name
    else:
        act_name, act_no, pdf_name = ACTS[act_key]
        pdf_path = LAWS / pdf_name
    doc = pymupdf.open(pdf_path)

    # Pick the extractor from what the file actually contains, not from its
    # extension. `auto` uses PyMuPDF unless the pages are effectively empty of
    # text, which means a scan.
    chosen = backend
    if backend == "auto":
        density = text_density(doc)
        chosen = "pymupdf" if density >= TEXT_DENSITY_FLOOR else "docling"
        print(f"  text density {density:.0f} chars/page -> {chosen}"
              + ("  (scanned; OCR required)" if chosen == "docling" else ""))

    if chosen == "docling":
        print("  ! Docling loses text on native PDFs (measured: 5 of 13 sections "
              "on a 6-page sample). Verify with verify_parsed.py.")
        raw = extract_lines_docling(pdf_path, start, limit)
        if not raw:
            raise SystemExit(
                f"no text recovered from {pdf_path.name} by either backend")
    else:
        raw = extract_lines(doc, start, limit)
    kept, dropped = strip_furniture(raw, doc, act_name)

    toc_pages: list[int] = []
    cols_per_page: list[dict] = []
    body_lines: list[Line] = []
    gloss_lines: list[Line] = []
    heading_lines: list[Line] = []

    last = len(doc) if limit is None else min(start + limit, len(doc))
    body_start = find_body_start(kept, start, last)
    for pno in range(start, last):
        page_lines = [l for l in kept if l.page == pno]
        if not page_lines:
            continue
        if pno < body_start:
            toc_pages.append(pno)
            continue
        cols = detect_columns(page_lines)
        if cols is None:
            body_lines.extend(page_lines)
            cols_per_page.append({"page": pno, "layout": "single"})
            continue
        body_x0, gloss_x0 = cols
        cols_per_page.append(
            {
                "page": pno,
                "body_x0": body_x0,
                "gloss_x0": gloss_x0,
                "gloss_side": "left" if gloss_x0 < body_x0 else "right",
            }
        )
        for l in page_lines:
            if PART_RE.match(l.text) or CHAPTER_RE.match(l.text):
                heading_lines.append(l)
            elif abs(round(l.x0 / 12) * 12 - gloss_x0) <= 12:
                gloss_lines.append(l)
            else:
                body_lines.append(l)

    notes = group_notes(gloss_lines)

    # Part/Chapter headings in document order, with the title line that follows.
    ordered_headings = sorted(heading_lines, key=lambda l: (l.page, l.y0))
    all_sorted = sorted(kept, key=lambda l: (l.page, l.y0))
    headings: list[tuple] = []
    def title_like(t: str) -> bool:
        """A heading's title line is set in caps; a gloss note is not.

        Without this, a chapter that has no title of its own takes whatever line
        follows it — CrPC Chapter I is printed as `PART I / PRELIMINARY /
        CHAPTER I` with no title, and picked up the gloss note "Short title".
        """
        letters = [c for c in t if c.isalpha()]
        return bool(letters) and sum(c.isupper() for c in letters) / len(letters) > 0.8

    for h in ordered_headings:
        kind = "part" if PART_RE.match(h.text) else "chapter"
        num = (PART_RE.match(h.text) or CHAPTER_RE.match(h.text)).group(1)
        # Titles wrap: take consecutive caps lines, so "OF THE CONSTITUTION OF
        # CRIMINAL COURTS AND / OFFICES" is not truncated at the line break.
        parts: list[str] = []
        for l in all_sorted:
            if (l.page, l.y0) <= (h.page, h.y0) or not l.text.strip():
                continue
            t = l.text.strip()
            if PART_RE.match(t) or CHAPTER_RE.match(t) or SECTION_RE.match(t):
                break
            if title_like(t):
                parts.append(t)
            elif parts:
                break
            else:
                break
        headings.append((kind, num, " ".join(parts) or None, h.page, h.y0))

    # ---- ONE ordered stream across every page: this is what makes a section
    # broken by a page break reassemble instead of splitting in two.
    sections: list[Section] = []
    preamble: list[Line] = []  # body text before the first section (title, preamble)
    for l in sorted(body_lines, key=lambda l: (l.page, l.y0)):
        m = SECTION_RE.match(l.text)
        if m:
            part = part_title = chap = chap_title = None
            for kind, num, title, hp, hy in headings:
                if (hp, hy) <= (l.page, l.y0):
                    if kind == "part":
                        part, part_title = num, title
                    else:
                        chap, chap_title = num, title
            sections.append(
                Section(
                    number=m.group(1),
                    part=part,
                    part_title=part_title,
                    chapter=chap,
                    chapter_title=chap_title,
                    page_start=l.page,
                    page_end=l.page,
                    lines=[l],
                )
            )
        elif sections:
            sections[-1].lines.append(l)
            sections[-1].page_end = l.page
        else:
            preamble.append(l)

    # ---- gloss -> section by vertical overlap, never by stream order
    titled = 0
    used: set[int] = set()
    for s in sections:
        first = s.lines[0]
        same_page = [n for n in notes if n.page == first.page]
        if not same_page:
            continue
        best = min(same_page, key=lambda n: abs(n.y0 - first.y0))
        if abs(best.y0 - first.y0) <= 20:
            s.title = best.text
            titled += 1
            used.add(id(best))
    # Notes that matched no section are kept rather than dropped. Silently
    # discarding them loses real text from the document (and hides pairing
    # failures); surfacing them makes both visible.
    unpaired_notes = [
        {"page": n.page, "y": round(n.y0, 1), "text": n.text}
        for n in notes
        if id(n) not in used
    ]

    # ---- assemble
    out_sections = []
    for s in sections:
        # Omitted/repealed runs are printed as a range on one line
        # ("58.- 59. [Omitted by ...]"), so record the span rather than losing
        # the second number entirely.
        rng = re.match(r"^\s*\d{1,3}[A-Z]{0,2}\.\s*-\s*(\d{1,3}[A-Z]{0,2})\.", s.text)
        out_sections.append(
            {
                "section_number": s.number,
                "section_number_to": rng.group(1) if rng else None,
                "section_title": s.title,
                "part": s.part,
                "part_title": s.part_title,
                "chapter": s.chapter,
                "chapter_title": s.chapter_title,
                "page_start": s.page_start,
                "page_end": s.page_end,
                "spans_page_break": s.page_start != s.page_end,
                "text": s.text,
                "children": split_children(s.text),
            }
        )

    # group into chapters by first appearance (never "changed since previous",
    # which shatters a chapter wherever one section fails to resolve one)
    tree: list[dict] = []
    seen: dict[str | None, dict] = {}
    for sec in out_sections:
        key = (sec["part"], sec["chapter"])
        grp = seen.get(key)
        if grp is None:
            grp = {
                "type": "chapter",
                "part": sec["part"],
                "part_title": sec["part_title"],
                "number": sec["chapter"],
                "title": sec["chapter_title"],
                "sections": [],
            }
            seen[key] = grp
            tree.append(grp)
        grp["sections"].append(sec)

    placed = (
        sum(len(s.lines) for s in sections)
        + len(gloss_lines)
        + len(heading_lines)
        + len(preamble)
    )
    considered = len([l for l in kept if l.page not in set(toc_pages)])

    return {
        "act": {"name": act_name, "act_no": act_no, "source_pdf": pdf_name},
        "preamble": norm(" ".join(l.text for l in preamble)),
        "stats": {
            "pages": last - start,
            "toc_pages_skipped": len(toc_pages),
            "lines_total": len(raw),
            "lines_dropped_as_furniture": len(dropped),
            "sections": len(out_sections),
            "sections_titled": titled,
            "sections_with_chapter": sum(1 for s in out_sections if s["chapter"]),
            "chapters": len({s["chapter"] for s in out_sections if s["chapter"]}),
            "parts": len({s["part"] for s in out_sections if s["part"]}),
            "sections_spanning_pages": sum(1 for s in out_sections if s["spans_page_break"]),
            "sections_with_children": sum(1 for s in out_sections if s["children"]),
            "unpaired_gloss_notes": len(unpaired_notes),
            # coverage: every kept line must land somewhere. anything else is
            # statutory text going silently missing.
            "lines_placed": placed,
            "lines_considered": considered,
            "orphaned": considered - placed,
        },
        "unpaired_gloss_notes": unpaired_notes,
        "columns_per_page": cols_per_page,
        "dropped_as_furniture": [
            {"page": l.page, "y": round(l.y0, 1), "size": round(l.size, 1), "text": l.text}
            for l in sorted(dropped, key=lambda l: (l.page, l.y0))
        ],
        "chapters": tree,
    }


def report(res: dict) -> None:
    st = res["stats"]
    print(f"=== {res['act']['name']} ===")
    for k, v in st.items():
        flag = "   <-- should be 0" if k == "orphaned" and v else ""
        print(f"  {k:<28} {v}{flag}")
    mirrored = {c.get("gloss_side") for c in res["columns_per_page"] if c.get("gloss_side")}
    print(f"  gloss sides seen: {mirrored or '{}'}")
    print("\n  chapters:")
    for c in res["chapters"][:10]:
        print(
            f"    {'P'+str(c['part']) if c['part'] else '  ':<5} "
            f"CH {str(c['number']):<6} {str(c['title'])[:44]:<44} sections={len(c['sections'])}"
        )
    if len(res["chapters"]) > 10:
        print(f"    ... {len(res['chapters']) - 10} more")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Parse a legal PDF into hierarchical JSON "
                    "(Act > Part > Chapter > Section > Subsection > Clause).",
        epilog="After parsing: verify_parsed.py, chunk_sections.py, build_index.py.",
    )
    ap.add_argument("--act", choices=sorted(ACTS), help="a known act")
    ap.add_argument("--all", action="store_true", help="parse every known act")
    ap.add_argument("--pdf", help="any legal PDF, by path or by name inside Laws/main/")
    ap.add_argument("--name", help="act title for citations (default: the filename)")
    ap.add_argument("--act-no", default="", help='e.g. "XLV of 1860"')
    ap.add_argument("--slug", help="citation key prefix (default: derived from the filename)")
    ap.add_argument("--backend", choices=["auto", "pymupdf", "docling"], default="auto",
                    help="auto picks by text density: PyMuPDF for native text, "
                         "Docling (OCR) for scans")
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--pages", type=int, default=None, help="limit pages (default: all)")
    args = ap.parse_args()

    if not (args.act or args.all or args.pdf):
        ap.error("give --pdf, --act or --all")

    OUT.mkdir(parents=True, exist_ok=True)

    if args.pdf:
        key = args.slug or slugify(pathlib.Path(args.pdf).name)
        res = parse(key, args.start, args.pages, pdf=args.pdf,
                    act_name=args.name, act_no=args.act_no, backend=args.backend)
        jobs = [(key, res)]
    else:
        keys = sorted(ACTS) if args.all else [args.act]
        jobs = [(k, parse(k, args.start, args.pages, backend=args.backend))
                for k in keys]

    for key, res in jobs:
        path = OUT / f"{key}.json"
        path.write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
        report(res)
        print(f"\n  -> {path}  ({path.stat().st_size/1024:.0f} KB)\n")


if __name__ == "__main__":
    main()
