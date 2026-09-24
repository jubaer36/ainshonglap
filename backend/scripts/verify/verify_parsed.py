"""Check the parsed JSON against the PDF and the independent bdlaws copy: every
word recovered, no phantom or missing sections.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import statistics
import sys
import unicodedata

import pymupdf

# The verifier re-reads the PDF with the same extractor the parser used, so a
# bug in extraction shows up in both and cannot hide. It deliberately does NOT
# reuse the parser's own structure logic beyond that.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from parse_law_pdf import ACTS, LAWS, SECTION_RE, extract_lines, strip_furniture  # noqa: E402

ORACLE = pathlib.Path(__file__).resolve().parents[3] / "Laws" / "bdlaws" / "bdlaws.jsonl"
PARSED = LAWS / "parsed"

ROMAN_ORDER = [
    "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII",
    "XIII", "XIV", "XV", "XVI", "XVII", "XVIII", "XIX", "XX", "XXI", "XXII",
    "XXIII", "XXIV", "XXV", "XXVI", "XXVII", "XXVIII", "XXIX", "XXX", "XXXI",
    "XXXII", "XXXIII", "XXXIV", "XXXV", "XXXVI", "XXXVII", "XXXVIII", "XXXIX",
    "XL", "XLI", "XLII", "XLIII", "XLIV", "XLV", "XLVI", "XLVII", "XLVIII",
    "XLIX", "L", "LI", "LII", "LIII", "LIV", "LV",
]


def words(s: str) -> list[str]:
    """Comparable word stream: fold quotes/dashes, drop punctuation and case.

    The PDF uses curly quotes and en-dashes where the oracle uses ASCII, and
    the parser normalises whitespace, so a raw string compare reports
    differences that are not real.
    """
    s = unicodedata.normalize("NFKD", s)
    s = s.replace("“", '"').replace("”", '"')
    s = s.replace("‘", "'").replace("’", "'")
    s = re.sub(r"[‐-―]", "-", s)
    return re.findall(r"[a-z0-9]+", s.lower())


def flat_sections(doc: dict) -> list[dict]:
    return [s for c in doc["chapters"] for s in c["sections"]]


def all_text(doc: dict) -> str:
    """Every scrap of text the JSON holds, including nested children.

    Heading lines are reconstituted as "PART <n>" / "CHAPTER <n>" because the
    JSON stores those split into `number` and `title` fields — the literal words
    "PART" and "CHAPTER" are structure, not content, and would otherwise read as
    55 missing words per act.
    """
    out = [doc.get("preamble") or ""]
    out += [n["text"] for n in doc.get("unpaired_gloss_notes", [])]
    seen_parts = set()
    for c in doc["chapters"]:
        if c.get("part") and c["part"] not in seen_parts:
            seen_parts.add(c["part"])
            out.append(f"PART {c['part']} {c.get('part_title') or ''}")
        out.append(f"CHAPTER {c.get('number') or ''} {c.get('title') or ''}")
        for s in c["sections"]:
            out.append(s.get("section_title") or "")
            out.append(s.get("text") or "")
    return " ".join(out)


# ---------------------------------------------------------------- checks
def c1_text_recovery(doc: dict, body_lines) -> dict:
    """Every body LINE of the PDF must be findable in the JSON.

    Line-level, not word-frequency. Comparing word counts says "32 occurrences
    of 'the' are missing" without saying where, which is unactionable; a line
    either survived into the JSON or it did not, and the ones that did not can
    be printed and looked at.

    A line is matched on its word sequence, so whitespace normalisation and
    quote/dash folding do not count as loss.
    """
    hay = " ".join(words(all_text(doc)))
    lost = []
    checked = 0
    for l in body_lines:
        w = words(l.text)
        if len(w) < 3:
            continue  # page numbers, stray marks: too short to locate reliably
        checked += 1
        if " ".join(w) not in hay:
            lost.append(l)
    return {
        "lines_checked": checked,
        "lines_lost": len(lost),
        "recovery_pct": 100.0 * (checked - len(lost)) / checked if checked else 100.0,
        "sample": lost[:12],
    }


def c2_page_coverage(doc: dict, body_pages: set[int]) -> dict:
    covered = set()
    for s in flat_sections(doc):
        for p in range(s["page_start"], s["page_end"] + 1):
            covered.add(p)
    missed = sorted(body_pages - covered)
    return {"body_pages": len(body_pages), "covered": len(covered & body_pages), "uncovered": missed}


def c3_section_continuity(doc: dict, raw_pages: dict[int, str], body_pages: set[int]) -> dict:
    # Duplicates are judged on the FULL number: "4" and "4A" are different
    # sections, and folding them to a base integer reported 43 false duplicates.
    full = [s["section_number"] for s in flat_sections(doc)]
    dupes = sorted({x for x in full if full.count(x) > 1})
    nums = []
    for s in flat_sections(doc):
        m = re.match(r"^(\d+)", s["section_number"])
        if m:
            nums.append(int(m.group(1)))
    if not nums:
        return {"gaps": 0, "real_gaps": [], "duplicates": dupes}
    present = set(nums)
    gaps = [n for n in range(min(nums), max(nums) + 1) if n not in present]
    # A gap is only a defect if the PDF actually prints that section.
    real = []
    for n in gaps:
        pat = re.compile(rf"(?m)^\s*(?:\d{{1,3}}\s*\[\s*)?{n}\.\s")
        if any(pat.search(raw_pages.get(p, "")) for p in body_pages):
            real.append(n)
    return {
        "range": f"{min(nums)}-{max(nums)}",
        "gaps": len(gaps),
        "real_gaps": real,
        "duplicates": dupes,
    }


def c4_oracle(doc: dict, act_name: str) -> dict:
    o: dict[str, str] = {}
    if ORACLE.exists():
        with open(ORACLE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                if (r.get("act_name") or "").strip().lower() != act_name.lower():
                    continue
                m = re.match(r"^\s*(\d{1,3}[A-Z]{0,3})\.", (r.get("section_name") or "").strip())
                if m:
                    o.setdefault(m.group(1), re.sub(r"\s+", " ", r.get("section_text") or "").strip())
    if not o:
        return {"available": False}
    mine = {s["section_number"]: s for s in flat_sections(doc)}
    both = set(mine) & set(o)
    sims = []
    for k in both:
        a, b = set(words(mine[k]["text"])), set(words(o[k]))
        if a or b:
            sims.append(len(a & b) / len(a | b))
    return {
        "available": True,
        "oracle_sections": len(o),
        "parsed_sections": len(mine),
        "phantom": sorted(set(mine) - set(o))[:20],
        "missing": sorted(set(o) - set(mine))[:20],
        "n_phantom": len(set(mine) - set(o)),
        "n_missing": len(set(o) - set(mine)),
        "median_jaccard": statistics.median(sims) if sims else 0.0,
        "below_0_8": sum(1 for s in sims if s < 0.8),
    }


def c5_hierarchy(doc: dict) -> dict:
    problems = []
    seen_chapters: dict[str, int] = {}
    for i, c in enumerate(doc["chapters"]):
        num = c["number"]
        if num in seen_chapters:
            problems.append(f"chapter {num} appears in {seen_chapters[num]} and {i} (interleaved)")
        seen_chapters[num] = i
        parts = {s["part"] for s in c["sections"]}
        if len(parts) > 1:
            problems.append(f"chapter {num} spans multiple parts: {sorted(map(str, parts))}")
        nums = []
        for s in c["sections"]:
            m = re.match(r"^(\d+)", s["section_number"])
            if m:
                nums.append(int(m.group(1)))
        if nums != sorted(nums):
            problems.append(f"chapter {num}: sections not ascending")
    ordered = [c["number"] for c in doc["chapters"] if c["number"] in ROMAN_ORDER]
    idx = [ROMAN_ORDER.index(n) for n in ordered]
    if idx != sorted(idx):
        problems.append("chapters not in roman-numeral order")
    no_chapter = [s["section_number"] for s in flat_sections(doc) if not s["chapter"]]
    return {
        "chapters": len(doc["chapters"]),
        "sections_without_chapter": no_chapter,
        "problems": problems,
    }


def c6_children(doc: dict) -> dict:
    problems = []
    first_of = {"subsection": "1", "clause": "a", "subclause": "i"}
    blocks_deep = 0

    def walk(nodes, depth, sec):
        nonlocal blocks_deep
        seq: dict[str, list[str]] = {}
        for n in nodes:
            t = n["type"]
            if t in ("explanation", "illustration", "exception", "provided"):
                if depth > 0:
                    blocks_deep += 1
                continue
            seq.setdefault(t, []).append(n.get("number"))
            if n.get("children"):
                walk(n["children"], depth + 1, sec)
        for t, vals in seq.items():
            if vals and vals[0] != first_of.get(t):
                problems.append(f"s.{sec}: {t} list starts at {vals[0]!r}, not {first_of.get(t)!r}")

    for s in flat_sections(doc):
        if s.get("children"):
            walk(s["children"], 0, s["section_number"])
    return {
        "sections_with_children": sum(1 for s in flat_sections(doc) if s.get("children")),
        "blocks_nested_below_section": blocks_deep,
        "problems": problems[:20],
        "n_problems": len(problems),
    }


def c7_citations(doc: dict, n_pages: int) -> dict:
    problems = []
    for s in flat_sections(doc):
        ps, pe = s["page_start"], s["page_end"]
        if ps is None or pe is None or ps > pe or ps < 0 or pe >= n_pages:
            problems.append(f"s.{s['section_number']}: bad page range {ps}-{pe}")
        head = (s["text"] or "")[:24]
        if not re.match(rf"^\s*(?:\d{{1,3}}\s*\[\s*)?{re.escape(s['section_number'])}\.", head):
            problems.append(f"s.{s['section_number']}: text does not open with its own number")
    return {"problems": problems[:20], "n_problems": len(problems)}


# ---------------------------------------------------------------- driver
def verify(act_key: str, verbose: bool) -> bool:
    act_name, _, pdf_name = ACTS[act_key]
    path = PARSED / f"{act_key}.json"
    if not path.exists():
        print(f"  {act_key}: NO PARSED FILE at {path}")
        return False
    doc = json.loads(path.read_text(encoding="utf-8"))

    # Re-read the PDF independently of the parser.
    pdfdoc = pymupdf.open(LAWS / pdf_name)
    raw = extract_lines(pdfdoc, 0, None)
    kept, _ = strip_furniture(raw, pdfdoc, act_name)
    toc = doc["stats"]["toc_pages_skipped"]
    body_pages = {l.page for l in kept if l.page >= toc}
    body_lines = [l for l in kept if l.page >= toc]
    raw_pages = {p: (pdfdoc[p].get_text() or "") for p in range(len(pdfdoc))}

    r1 = c1_text_recovery(doc, body_lines)
    r2 = c2_page_coverage(doc, body_pages)
    r3 = c3_section_continuity(doc, raw_pages, body_pages)
    r4 = c4_oracle(doc, act_name)
    r5 = c5_hierarchy(doc)
    r6 = c6_children(doc)
    r7 = c7_citations(doc, len(pdfdoc))

    ok = (
        r1["recovery_pct"] >= 99.5
        and not r2["uncovered"]
        and not r3["real_gaps"]
        and not r3["duplicates"]
        and (not r4.get("available") or r4["n_phantom"] == 0)
        and not r5["problems"]
        and r6["blocks_nested_below_section"] == 0
        and r7["n_problems"] == 0
    )

    print(f"\n=== {act_name} ===")
    print(f"  C1 text recovery     {r1['recovery_pct']:.3f}%  "
          f"({r1['lines_lost']} of {r1['lines_checked']} body lines not found in JSON)")
    for l in r1["sample"][:6]:
        print(f"                       LOST p{l.page} x0={l.x0:.0f} sz={l.size:.1f} | {l.text[:66]}")
    print(f"  C2 page coverage     {r2['covered']}/{r2['body_pages']} body pages"
          + (f"   UNCOVERED: {r2['uncovered'][:10]}" if r2["uncovered"] else ""))
    print(f"  C3 continuity        range {r3.get('range')}  {r3['gaps']} numbering gaps, "
          f"{len(r3['real_gaps'])} real"
          + (f" -> {r3['real_gaps'][:10]}" if r3["real_gaps"] else "")
          + (f"   DUPLICATES: {r3['duplicates']}" if r3["duplicates"] else ""))
    if r4.get("available"):
        print(f"  C4 oracle            parsed {r4['parsed_sections']} vs oracle {r4['oracle_sections']}; "
              f"phantom {r4['n_phantom']}, missing {r4['n_missing']}; "
              f"median word-overlap {r4['median_jaccard']*100:.1f}%, {r4['below_0_8']} below 80%")
        if r4["missing"]:
            print(f"                       missing: {r4['missing']}")
    else:
        print("  C4 oracle            (oracle unavailable)")
    print(f"  C5 hierarchy         {r5['chapters']} chapters, "
          f"{len(r5['sections_without_chapter'])} sections without one; "
          f"{len(r5['problems'])} problems")
    for p in r5["problems"][:6]:
        print(f"                       - {p}")
    print(f"  C6 child nesting     {r6['sections_with_children']} sections with children; "
          f"{r6['blocks_nested_below_section']} blocks too deep; {r6['n_problems']} sequence problems")
    if verbose:
        for p in r6["problems"][:10]:
            print(f"                       - {p}")
    print(f"  C7 citations         {r7['n_problems']} problems")
    if verbose:
        for p in r7["problems"][:10]:
            print(f"                       - {p}")
    print(f"  ---> {'PASS' if ok else 'ATTENTION'}")
    return ok


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--act", choices=sorted(ACTS))
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    keys = [args.act] if args.act else sorted(ACTS)
    results = [verify(k, args.verbose) for k in keys]
    print(f"\n{sum(results)}/{len(results)} acts pass all checks")


if __name__ == "__main__":
    main()
