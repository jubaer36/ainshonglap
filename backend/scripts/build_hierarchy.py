"""Build a hierarchical JSON for one Act by joining two complementary sources.

Neither source alone is sufficient, and the split is not arbitrary:

  bdlaws.jsonl  -> verified statutory TEXT, complete to the section
                   (0 sections missing vs. the PDF) but 100% flat: no Part,
                   no Chapter, no subsection->parent link, no page numbers.
  the PDF       -> the STRUCTURE the JSONL threw away: Part/Chapter headings
                   and the page each section sits on.

So: text from the JSONL (where a parsing slip would become a citation bug),
structure from the PDF (where mistakes are checkable against hard invariants
— chapter counts are known, section numbers ascend, every orphan row must find
exactly one parent).

Within a section, the sub-structure is parsed out of the text itself:
subsections (1), clauses (a), roman sub-clauses (i), Explanations,
Illustrations, Exceptions and Provisos.

Run (from backend/, ainshonglap conda env active):

    python scripts/build_hierarchy.py --act penal
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import statistics

import pymupdf

LAWS = pathlib.Path(__file__).resolve().parents[2] / "Laws"
JSONL = LAWS / "bdlaws" / "bdlaws.jsonl"

ACTS = {
    "penal": ("The Penal Code, 1860", "The Penal Code, 1860.pdf"),
    "crpc": (
        "The Code of Criminal Procedure, 1898",
        "The Code of Criminal Procedure, 1898 (bdcode.gov.bd copy).pdf",
    ),
    "evidence": ("The Evidence Act, 1872", "The Evidence Act, 1872.pdf"),
}

SECTION_RE = re.compile(r"^\s*(?:\d{1,3}\s*\[\s*)?(\d{1,3}[A-Z]{0,2})\.\s")
CHAPTER_RE = re.compile(r"^\s*(?:\d{1,3}\s*)?CHAPTER\s+([IVXLC]+)\s*$", re.I)
PART_RE = re.compile(r"^\s*(?:\d{1,3}\s*)?PART\s+([IVXLC]+)\s*$", re.I)

# Sub-structure markers, matched against the section text.
SUBSEC_RE = re.compile(r"(?:(?<=\s)|^)\((\d{1,2})\)\s")
CLAUSE_RE = re.compile(r"(?:(?<=\s)|^)\(([a-z]{1,2})\)\s")
BLOCK_RE = re.compile(
    r"(?:(?<=\s)|^)(Illustrations?|Explanations?(?:\s+\d)?|Exception(?:\s+\d)?|Provided)\b"
)


def is_toc_page(lines: list[str]) -> bool:
    """Contents pages mimic sections but carry no body text.

    Measured on these PDFs: contents pages run 0.32-0.57 numbered-line ratio
    with 13-27 char average lines; body pages run 0.00-0.09 and 34-49 chars.
    """
    if len(lines) < 8:
        return False
    ratio = sum(1 for t in lines if SECTION_RE.match(t)) / len(lines)
    return ratio >= 0.25 and statistics.mean(len(t) for t in lines) < 32


def pdf_structure(pdf_path: pathlib.Path) -> tuple[dict[str, dict], dict[str, int]]:
    """Walk the PDF in reading order -> {section_number: {part, chapter...}}."""
    doc = pymupdf.open(pdf_path)
    sec_meta: dict[str, dict] = {}
    chapters: dict[str, str] = {}
    cur_part = cur_part_title = cur_chap = cur_chap_title = None
    pending = None  # a heading whose title is on the next line

    for pno in range(len(doc)):
        lines = []
        for b in doc[pno].get_text("dict")["blocks"]:
            for ln in b.get("lines", []):
                t = "".join(s["text"] for s in ln["spans"]).strip()
                if t:
                    lines.append((ln["bbox"][1], ln["bbox"][0], t))
        lines.sort()
        texts = [t for _, _, t in lines]
        if is_toc_page(texts):
            continue

        for t in texts:
            if pending:
                kind = pending
                pending = None
                if kind == "part":
                    cur_part_title = t
                else:
                    cur_chap_title = t
                    chapters[cur_chap] = t
                continue
            if m := PART_RE.match(t):
                cur_part, cur_part_title, pending = m.group(1), None, "part"
                continue
            if m := CHAPTER_RE.match(t):
                cur_chap, cur_chap_title, pending = m.group(1), None, "chapter"
                continue
            if m := SECTION_RE.match(t):
                num = m.group(1)
                sec_meta.setdefault(
                    num,
                    {
                        "part": cur_part,
                        "part_title": cur_part_title,
                        "chapter": cur_chap,
                        "chapter_title": cur_chap_title,
                        "page": pno,
                    },
                )
    return sec_meta, chapters


def load_act_rows(act_name: str) -> list[dict]:
    rows = []
    with open(JSONL, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if (r.get("act_name") or "").strip().lower() == act_name.lower():
                rows.append(r)
    rows.sort(key=lambda r: r.get("section_id") or 0)
    return rows


def split_children(text: str) -> list[dict]:
    """Decompose one section's text into subsections / clauses / blocks.

    Uses a stack keyed on each open level's EXPECTED NEXT value, which is what
    disambiguates "(i)" — roman one, or the letter i following (h)? Whichever
    level is already expecting it wins; only otherwise does a new level open.
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

    ROMAN = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii"]

    def succ(kind: str, val: str) -> str | None:
        """The value this level should see next, or None if unbounded."""
        if kind == "num":
            return str(int(val) + 1)
        if kind == "roman":
            i = ROMAN.index(val)
            return ROMAN[i + 1] if i + 1 < len(ROMAN) else None
        if len(val) == 1:
            return chr(ord(val) + 1)
        # "aa" -> "ab"
        return val[:-1] + chr(ord(val[-1]) + 1)

    root: list[dict] = []
    stack: list[dict] = []  # each: {kind, expect, node}

    for idx, (pos, kind, val) in enumerate(marks):
        end = marks[idx + 1][0] if idx + 1 < len(marks) else len(text)
        body = text[pos:end].strip()

        if kind == "block":
            node = {"type": val.split()[0].lower().rstrip("s"), "text": body}
            (stack[0]["node"]["children"] if stack else root).append(node)
            continue

        # Unambiguous roman numerals ("ii", "iv", ...) are their own level.
        # Bare "i"/"v"/"x" stay 'alpha' and get resolved by the stack below:
        # if an alpha level is already expecting "i" (having just seen "h"),
        # that match wins; otherwise it opens a nested level.
        if kind == "alpha" and len(val) > 1:
            kind = "roman" if val in ROMAN else kind

        node = {
            "type": {"num": "subsection", "alpha": "clause", "roman": "subclause"}[kind],
            "number": val,
            "text": body,
            "children": [],
        }

        # Find, without mutating, the innermost open level expecting this value.
        hit = next(
            (
                i
                for i in range(len(stack) - 1, -1, -1)
                if stack[i]["kind"] == kind and stack[i]["expect"] == val
            ),
            None,
        )

        if hit is not None:
            # Sibling at that level: close everything nested inside it.
            del stack[hit:]
            parent = stack[-1]["node"]["children"] if stack else root
        elif val in ("1", "a", "i") and stack:
            # First of a new sequence -> opens a level nested in the current one.
            parent = stack[-1]["node"]["children"]
        else:
            # Unanchored: belongs to the section body, not to any open list.
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


def build(act_key: str) -> dict:
    act_name, pdf_name = ACTS[act_key]
    rows = load_act_rows(act_name)
    sec_meta, chapters = pdf_structure(LAWS / pdf_name)

    meta = {}
    for k in (
        "act_name", "act_no", "volume_name", "published_date", "short_title",
        "preamble", "repealed", "repealed_notice", "related_act",
        "num_of_sections", "notes",
    ):
        vals = {json.dumps(r.get(k), ensure_ascii=False) for r in rows}
        if len(vals) == 1:
            meta[k] = rows[0].get(k)

    # Pass 1: rows -> sections, re-attaching orphan continuation rows to the
    # last numbered section (the source drops that link entirely).
    sections: list[dict] = []
    orphans_attached = 0
    for r in rows:
        name = (r.get("section_name") or "").replace("\t", " ").strip()
        text = re.sub(r"\s+", " ", r.get("section_text") or "").strip()
        m = re.match(r"^(\d{1,3}[A-Z]{0,3})\.\s*(.*)$", name)
        if m:
            num = m.group(1)
            st = sec_meta.get(num, {})
            sections.append(
                {
                    "section_number": num,
                    "section_title": m.group(2).strip(),
                    "part": st.get("part"),
                    "part_title": st.get("part_title"),
                    "chapter": st.get("chapter"),
                    "chapter_title": st.get("chapter_title"),
                    "page": st.get("page"),
                    "text": text,
                    "continuation_titles": [],
                    "source_section_ids": [r.get("section_id")],
                }
            )
        elif sections:
            sections[-1]["text"] += " " + text
            sections[-1]["continuation_titles"].append(name)
            sections[-1]["source_section_ids"].append(r.get("section_id"))
            orphans_attached += 1

    # Pass 2: decompose each section's text into nested children.
    for s in sections:
        s["children"] = split_children(s["text"])

    # Pass 3: nest sections under their chapters, grouped by chapter number in
    # first-appearance order. Grouping on "changed since the previous section"
    # would shatter a chapter into fragments wherever one section failed to
    # resolve a chapter.
    tree: list[dict] = []
    by_num: dict[str | None, dict] = {}
    for s in sections:
        ch = s.get("chapter")
        grp = by_num.get(ch)
        if grp is None:
            grp = {
                "type": "chapter",
                "number": ch,
                "title": s.get("chapter_title"),
                "sections": [],
            }
            by_num[ch] = grp
            tree.append(grp)
        grp["sections"].append(s)

    with_ch = sum(1 for s in sections if s["chapter"])
    return {
        "act": meta,
        "provenance": {
            "text_from": "bdlaws.jsonl (Ashik2380/bdlaws, scraped from bdlaws.minlaw.gov.bd)",
            "structure_from": pdf_name,
            "why": "the JSONL is complete on text but 100% flat; the PDF carries the Part/Chapter hierarchy and page numbers it dropped",
        },
        "stats": {
            "source_rows": len(rows),
            "sections": len(sections),
            "orphan_rows_reattached": orphans_attached,
            "sections_with_chapter": with_ch,
            "sections_without_chapter": len(sections) - with_ch,
            "chapters_found": len({s["chapter"] for s in sections if s["chapter"]}),
            "sections_with_children": sum(1 for s in sections if s["children"]),
        },
        "chapters": tree,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--act", choices=sorted(ACTS), default="penal")
    args = ap.parse_args()

    out = build(args.act)
    path = LAWS / f"{args.act}_hierarchical.json"
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    st = out["stats"]
    print(f"=== {out['act'].get('act_name')} ===")
    for k, v in st.items():
        print(f"  {k:<28} {v}")
    print(f"\n  -> {path}  ({path.stat().st_size/1024:.0f} KB)")
    print("\n  chapters:")
    for c in out["chapters"][:8]:
        print(f"    CH {str(c['number']):<6} {str(c['title'])[:50]:<50} sections={len(c['sections'])}")


if __name__ == "__main__":
    main()
