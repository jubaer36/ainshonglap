"""PyMuPDF + regex parser spike (pdf-parsing-options.md primary approach).

Counterpart to docling_test.py: parses the first N pages of one Act and dumps
staged output so each step can be eyeballed before trusting it.

The three things Docling got wrong, and how this handles them:
  1. page-break splits  -> body text is concatenated across ALL pages before
                           any section regex runs, so a page boundary is just
                           whitespace mid-sentence
  2. nested list loss   -> subsection/clause markers detected explicitly
  3. column mispairing  -> gloss note matched to its section by y-overlap on
                           real coordinates, never by stream order

Column geometry is DETECTED per page, not hardcoded: CrPC keeps the gloss on
the left throughout, while the Penal Code mirrors it by page parity (gloss on
the outer margin), so a fixed x threshold would silently break on one of them.

Run (from backend/, ainshonglap conda env active):

    python scripts/pymupdf_parse_test.py
    python scripts/pymupdf_parse_test.py --pdf penal --pages 10
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import statistics
from dataclasses import dataclass, field

import pymupdf

LAWS_DIR = pathlib.Path(__file__).resolve().parents[2] / "Laws"
OUT_DIR = pathlib.Path(__file__).resolve().parent / "pymupdf_out"

PDFS = {
    "crpc": (
        "The Code of Criminal Procedure, 1898 (bdcode.gov.bd copy).pdf",
        "The Code of Criminal Procedure, 1898",
    ),
    "penal": ("The Penal Code, 1860.pdf", "The Penal Code, 1860"),
    "evidence": ("The Evidence Act, 1872.pdf", "The Evidence Act, 1872"),
}

# A section opener: optional amendment-footnote marker ("26[ "), then the
# number, then a period. Matches "154.", "17A.", "265D.(1)", "26[ 17A. (1)".
SECTION_RE = re.compile(r"^\s*(?:\d{1,3}\s*\[\s*)?(\d{1,3}[A-Z]{0,2})\.\s*")
PART_RE = re.compile(r"^\s*(?:\d{1,3}\s*)?PART\s+([IVXLC]+)\s*$", re.I)
CHAPTER_RE = re.compile(r"^\s*(?:\d{1,3}\s*)?CHAPTER\s+([IVXLC]+)\s*$", re.I)
SUBSECTION_RE = re.compile(r"\((\d{1,2})\)")
CLAUSE_RE = re.compile(r"\(([a-z]{1,2})\)")


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
class GlossNote:
    page: int
    y0: float
    y1: float
    text: str


@dataclass
class Section:
    number: str
    title: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    part: str | None = None
    chapter: str | None = None
    lines: list[Line] = field(default_factory=list)

    @property
    def text(self) -> str:
        return normalise(" ".join(l.text for l in self.lines))


def normalise(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def extract_lines(doc: pymupdf.Document, start: int, num_pages: int) -> list[Line]:
    """Every text line on the sampled pages, with coordinates and font size."""
    lines: list[Line] = []
    for pno in range(start, min(start + num_pages, len(doc))):
        for block in doc[pno].get_text("dict")["blocks"]:
            for ln in block.get("lines", []):
                text = "".join(s["text"] for s in ln["spans"]).strip()
                if not text:
                    continue
                size = max((s["size"] for s in ln["spans"]), default=0.0)
                x0, y0, x1, y1 = ln["bbox"]
                lines.append(Line(pno, x0, x1, y0, y1, size, text))
    return lines


def body_font_size(lines: list[Line]) -> float:
    """Modal font size = body text. Footnotes/headers sit below it."""
    sizes = [round(l.size, 1) for l in lines]
    return statistics.mode(sizes) if sizes else 0.0


def strip_furniture(
    lines: list[Line], doc: pymupdf.Document, act_name: str
) -> tuple[list[Line], list[Line]]:
    """Drop running headers/footers and the page-bottom footnote block.

    Footnotes are set in a smaller font than body text, which is a far more
    reliable signal than a y-position cutoff (the rule above them is drawn as
    vector graphics, not text, so it isn't in the text layer at all).

    Header detection is deliberately CONTENT-based, not positional. A plain
    "top 11% of the page" rule looks right on the Penal Code (running header at
    y=93.7) and silently eats real statutory text on CrPC, whose body starts at
    y=72.3 with no running header at all — the exact class of silent drop this
    parser exists to avoid.
    """
    base = body_font_size(lines)
    # "The Penal Code, 1860" -> {penal, code, 1860}
    act_words = {w for w in re.findall(r"\w+", act_name.lower()) if w not in {"the", "of"}}

    def looks_like_header(l: Line) -> bool:
        if l.y0 >= doc[l.page].rect.height * 0.12:
            return False
        txt = l.text.strip()
        if re.fullmatch(r"\d{1,4}", txt):  # bare page number
            return True
        words = {w for w in re.findall(r"\w+", txt.lower())}
        # running title, e.g. "Penal Code, 1860" — short and mostly act words
        return bool(words) and len(words) <= 6 and len(words & act_words) >= 2

    def looks_like_footnote(l: Line) -> bool:
        """Smallest type tier, and low on the page.

        Sizes are compared PROPORTIONALLY because the absolute points differ per
        document and even per page (CrPC body 9.0 / gloss 8.5; Penal p45 body
        11.0 / gloss 10.0; Penal p52 body 10.7 / gloss 9.7 / footnote 8.8).
        A flat "base - 0.6" cutoff wipes out the whole middle tier on the Penal
        Code — i.e. every gloss note and every Illustration.
        """
        return l.size < base * 0.88 and l.y0 > doc[l.page].rect.height * 0.55

    kept: list[Line] = []
    dropped: list[Line] = []
    for l in lines:
        if looks_like_header(l) or looks_like_footnote(l):
            dropped.append(l)
        else:
            kept.append(l)
    return kept, dropped


def is_toc_page(page_lines: list[Line]) -> bool:
    """Front-matter contents pages look like sections but have no body text.

    Two independent signals, measured on these PDFs (Penal Code TOC pages 0-30
    vs. body pages 40+): a contents page is mostly numbered entries
    (ratio 0.32-0.57 vs 0.00-0.09 on body pages) and its lines are short
    (avg 13-27 chars vs 34-49). Both must agree before a page is skipped.

    Left unfiltered, these produce exactly the duplicate section keys and empty
    stub records seen in the external bangladesh-legal-qa-dataset corpus.
    """
    if len(page_lines) < 8:
        return False
    numbered = sum(1 for l in page_lines if SECTION_RE.match(l.text))
    ratio = numbered / len(page_lines)
    avg_len = statistics.mean(len(l.text) for l in page_lines)
    return ratio >= 0.25 and avg_len < 32


def detect_columns(page_lines: list[Line]) -> tuple[float, float] | None:
    """Return (body_x0, gloss_x0) for one page, or None if it isn't 2-column.

    The body column is identified by *content*: it is the x-cluster that holds
    lines opening with a section number. Everything else that forms a distinct,
    narrower cluster is the gloss column. This is what makes the same code work
    on CrPC (gloss always left) and the Penal Code (gloss on the outer margin,
    so it swaps sides every page).
    """
    if not page_lines:
        return None
    clusters: dict[float, list[Line]] = {}
    for l in page_lines:
        key = round(l.x0 / 12) * 12
        clusters.setdefault(key, []).append(l)
    if len(clusters) < 2:
        return None

    # Body column: widest mean line width among clusters, cross-checked by
    # whether any of its lines open a section.
    def score(item: tuple[float, list[Line]]) -> tuple[int, float]:
        _, ls = item
        has_section = any(SECTION_RE.match(l.text) for l in ls)
        return (1 if has_section else 0, statistics.mean(l.width for l in ls))

    ranked = sorted(clusters.items(), key=score, reverse=True)
    body_x0 = ranked[0][0]
    body_width = statistics.mean(l.width for l in clusters[body_x0])

    # Gloss column: the biggest cluster that is clearly narrower than body.
    candidates = [
        (k, ls)
        for k, ls in clusters.items()
        if k != body_x0 and statistics.mean(l.width for l in ls) < body_width * 0.6
    ]
    if not candidates:
        return None
    gloss_x0 = max(candidates, key=lambda kv: len(kv[1]))[0]
    return body_x0, gloss_x0


def group_gloss(lines: list[Line], gap: float = 6.0) -> list[GlossNote]:
    """Merge consecutive gloss lines into one note (they wrap over 2-6 lines)."""
    notes: list[GlossNote] = []
    for l in sorted(lines, key=lambda l: (l.page, l.y0)):
        if notes and notes[-1].page == l.page and l.y0 - notes[-1].y1 <= gap:
            notes[-1].text += " " + l.text
            notes[-1].y1 = l.y1
        else:
            notes.append(GlossNote(l.page, l.y0, l.y1, l.text))
    for n in notes:
        n.text = normalise(n.text)
    return notes


def parse(pdf_key: str, num_pages: int, start: int = 0) -> dict:
    fname, act_name = PDFS[pdf_key]
    doc = pymupdf.open(LAWS_DIR / fname)

    raw = extract_lines(doc, start, num_pages)
    kept, dropped = strip_furniture(raw, doc, act_name)

    report: dict = {
        "act": act_name,
        "source_pdf": fname,
        "page_range": [start, min(start + num_pages, len(doc)) - 1],
        "pages_parsed": min(start + num_pages, len(doc)) - start,
        "lines_total": len(raw),
        "lines_dropped_as_furniture": len(dropped),
        "toc_pages_skipped": [],
        "columns_per_page": [],
    }

    body_lines: list[Line] = []
    gloss_lines: list[Line] = []
    full_width: list[Line] = []

    for pno in range(start, start + report["pages_parsed"]):
        page_lines = [l for l in kept if l.page == pno]
        if is_toc_page(page_lines):
            report["toc_pages_skipped"].append(pno)
            continue
        cols = detect_columns(page_lines)
        if cols is None:
            full_width.extend(page_lines)
            report["columns_per_page"].append({"page": pno, "layout": "single/none"})
            continue
        body_x0, gloss_x0 = cols
        report["columns_per_page"].append(
            {
                "page": pno,
                "body_x0": body_x0,
                "gloss_x0": gloss_x0,
                "gloss_side": "left" if gloss_x0 < body_x0 else "right",
            }
        )
        for l in page_lines:
            key = round(l.x0 / 12) * 12
            if PART_RE.match(l.text) or CHAPTER_RE.match(l.text):
                full_width.append(l)
            elif abs(key - gloss_x0) <= 12:
                gloss_lines.append(l)
            else:
                body_lines.append(l)

    notes = group_gloss(gloss_lines)

    # Structural headings, in document order, so each section can inherit the
    # Part/Chapter it falls under.
    headings = []
    for l in sorted(full_width, key=lambda l: (l.page, l.y0)):
        if m := PART_RE.match(l.text):
            headings.append(("part", m.group(1), l.page, l.y0))
        elif m := CHAPTER_RE.match(l.text):
            headings.append(("chapter", m.group(1), l.page, l.y0))

    # Split body text into sections. Crucially this walks ONE ordered stream of
    # lines across every page, so a section running over a page break just
    # continues — no page-level boundary exists to be mistaken for a new section.
    sections: list[Section] = []
    cur_part = cur_chapter = None
    for l in sorted(body_lines, key=lambda l: (l.page, l.y0)):
        for kind, num, hp, hy in headings:
            if (hp, hy) <= (l.page, l.y0):
                if kind == "part":
                    cur_part = num
                else:
                    cur_chapter = num
        m = SECTION_RE.match(l.text)
        if m:
            sections.append(
                Section(
                    number=m.group(1),
                    page_start=l.page,
                    page_end=l.page,
                    part=cur_part,
                    chapter=cur_chapter,
                    lines=[l],
                )
            )
        elif sections:
            sections[-1].lines.append(l)
            sections[-1].page_end = l.page

    # Pair each gloss note to the section whose opening line sits nearest it
    # vertically on the same page — the Docling failure mode this exists to fix.
    for sec in sections:
        first = sec.lines[0]
        same_page = [n for n in notes if n.page == first.page]
        if not same_page:
            continue
        best = min(same_page, key=lambda n: abs(n.y0 - first.y0))
        if abs(best.y0 - first.y0) <= 20:
            sec.title = best.text

    # Coverage check: every non-furniture line must end up in a section or a
    # gloss note. Anything unaccounted for is silently-lost statutory text.
    placed = sum(len(s.lines) for s in sections) + len(gloss_lines) + len(full_width)
    considered = len([l for l in kept if l.page not in set(report["toc_pages_skipped"])])
    report["sections_found"] = len(sections)
    report["gloss_notes_found"] = len(notes)
    report["sections_with_title"] = sum(1 for s in sections if s.title)
    report["coverage"] = {
        "body_lines": len(body_lines),
        "gloss_lines": len(gloss_lines),
        "heading_lines": len(full_width),
        "lines_placed": placed,
        "lines_kept": considered,
        "orphaned": considered - placed,
    }
    report["dropped_as_furniture"] = [
        {"page": l.page, "y": round(l.y0, 1), "size": l.size, "text": l.text}
        for l in sorted(dropped, key=lambda l: (l.page, l.y0))
    ]
    report["sections"] = [
        {
            "number": s.number,
            "title": s.title,
            "part": s.part,
            "chapter": s.chapter,
            "page_start": s.page_start,
            "page_end": s.page_end,
            "spans_page_break": s.page_start != s.page_end,
            "subsections_seen": SUBSECTION_RE.findall(s.text)[:12],
            "clauses_seen": CLAUSE_RE.findall(s.text)[:12],
            "text": s.text,
        }
        for s in sections
    ]
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", choices=sorted(PDFS), default="crpc")
    ap.add_argument("--pages", type=int, default=20)
    ap.add_argument(
        "--start",
        type=int,
        default=0,
        help="first PDF page index to parse (the Penal Code opens with ~35 pages of contents)",
    )
    args = ap.parse_args()

    OUT_DIR.mkdir(exist_ok=True)
    report = parse(args.pdf, args.pages, args.start)

    out = OUT_DIR / f"{args.pdf}_p{args.start}-{args.start + args.pages - 1}.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    cov = report["coverage"]
    lo, hi = report["page_range"]
    print(f"=== {report['act']} — pages {lo}-{hi} ===")
    if report["toc_pages_skipped"]:
        print(f"TOC pages skipped: {report['toc_pages_skipped']}")
    print(f"lines: {report['lines_total']} total, {report['lines_dropped_as_furniture']} dropped as header/footnote")
    print(f"sections: {report['sections_found']}   gloss notes: {report['gloss_notes_found']}   titled: {report['sections_with_title']}")
    print(
        f"coverage: {cov['lines_placed']}/{cov['lines_kept']} lines placed "
        f"(body {cov['body_lines']}, gloss {cov['gloss_lines']}, headings {cov['heading_lines']}) "
        f"-> ORPHANED: {cov['orphaned']}"
    )

    print("\n--- dropped as furniture (verify none is real statute text) ---")
    for d in report["dropped_as_furniture"][:15]:
        print(f"  p{d['page']:>3} y={d['y']:>6} sz={d['size']:.1f} | {d['text'][:66]}")
    if len(report["dropped_as_furniture"]) > 15:
        print(f"  ... {len(report['dropped_as_furniture']) - 15} more (see JSON)")

    print("\n--- column detection per page ---")
    for c in report["columns_per_page"]:
        if c.get("layout"):
            print(f"  p{c['page']:>3}  {c['layout']}")
        else:
            print(f"  p{c['page']:>3}  body_x0={c['body_x0']:>5}  gloss_x0={c['gloss_x0']:>5}  gloss on {c['gloss_side']}")

    print("\n--- sections (number | title | pages) ---")
    for s in report["sections"]:
        span = f"p{s['page_start']}" + (f"-{s['page_end']}" if s["spans_page_break"] else "")
        print(f"  {s['number']:>6}. {str(s['title'])[:58]:<58} {span}")

    print("\n--- first 3 sections, full text ---")
    for s in report["sections"][:3]:
        print(f"\n[{s['number']}] {s['title']}  (Part {s['part']}, Chapter {s['chapter']})")
        print(f"  subsections seen: {s['subsections_seen']}")
        print(f"  {s['text'][:600]}")

    print(f"\nJSON -> {out}")


if __name__ == "__main__":
    main()
