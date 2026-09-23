"""Section-level chunking of the parsed acts (chunking-plan.md).

One chunk per section. A section over budget is split at its own subsection
boundaries — never mid-provision, because the measured data says that is always
possible: every oversized section in all three acts has subsections to split at.

Each chunk's embedded text is prefixed with its hierarchy path. That is the
cheap half of Anthropic's Contextual Retrieval and the fix the legal-RAG
literature gives for Document-Level Retrieval Mismatch — a bare
"Whoever commits..." says nothing about which of three heavily-overlapping acts
it came from. Ancestry is already inline on every parsed section, so this is
string formatting, not an LLM call.

Run (from backend/, ainshonglap conda env active):

    python scripts/chunk_sections.py                  # all acts
    python scripts/chunk_sections.py --act crpc --show 5
    python scripts/chunk_sections.py --max-tokens 384
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import statistics
from collections import Counter

LAWS = pathlib.Path(__file__).resolve().parents[2] / "Laws" / "main"
PARSED = LAWS / "parsed"
OUT = LAWS / "chunks"

# Rough English-legal ratio; good enough for budgeting, and deliberately not a
# tokenizer dependency — the exact model is not chosen yet.
TOK_PER_WORD = 1.35
DEFAULT_MAX = 512


def ntok(s: str) -> int:
    return int(len(s.split()) * TOK_PER_WORD)


def hierarchy_path(act: str, sec: dict, unit: str = "Section") -> str:
    """Act + section + title only.

    E2 (experiments.md) A/B'd five prefix shapes and found a 3.0pp total spread
    against a +/-3.7pp confidence interval — nothing separable, including no
    prefix at all. The full Part>Chapter>Section path cost 38 tokens a chunk
    (35% larger) for no measurable gain, so it is gone. This shorter form was
    nominally best on every metric; that lead is NOT significant, it is simply
    the cheapest of the equals. Part and chapter remain in metadata for
    filtering and citation display.

    `unit` is "Section" for a statute and "Passage" for a document parsed by
    Docling (parse_docling_document.py), whose numbered units are passages in
    reading order, not provisions — "State v. Rahim, Section 1" would claim a
    structure the document does not have.
    """
    t = f"{act}, {unit} {sec['section_number']}"
    if sec.get("section_title"):
        t += f": {sec['section_title']}"
    return t


def split_oversized(sec: dict, max_tokens: int) -> list[tuple[str, str | None]]:
    """(text, subsection_label) pieces for a section that exceeds budget.

    Splits only on top-level subsections, keeping each subsection whole with its
    own nested clauses. Anything before the first subsection (the lead-in that
    the subsections hang off) rides with the first piece so it is never orphaned.
    """
    text = sec["text"]
    kids = sec.get("children") or []
    # Every top-level enumerated child is a candidate boundary, subsections and
    # clauses alike. Preferring subsections *when any exist* was too clever:
    # CrPC s.4 has one subsection and ten clauses, so it split at the single
    # subsection and left a 986-token piece with ten unused boundaries inside
    # it. The Penal Code has the opposite shape — clauses and no subsections at
    # all — so neither type can be the sole signal. Over-splitting is harmless
    # here because the merge below re-joins anything small.
    subs = [c for c in kids if c["type"] in ("subsection", "clause")]
    if not subs:
        return [(text, None)]

    # Locate each subsection's text inside the section text, in order.
    marks: list[tuple[int, str]] = []
    cursor = 0
    for c in subs:
        head = " ".join(c["text"].split()[:6])
        if not head:
            continue
        i = text.find(head, cursor)
        if i == -1:
            continue
        marks.append((i, c["number"]))
        cursor = i + 1
    if not marks:
        return [(text, None)]

    pieces: list[tuple[str, str | None]] = []
    lead = text[: marks[0][0]].strip()
    for j, (start, num) in enumerate(marks):
        end = marks[j + 1][0] if j + 1 < len(marks) else len(text)
        body = text[start:end].strip()
        if j == 0 and lead:
            body = f"{lead} {body}"          # lead-in belongs with the first
        pieces.append((body, num))

    # Re-join neighbours that are individually small: splitting a 600-token
    # section into eight 70-token fragments would lose more context than the
    # oversize cost.
    merged: list[tuple[str, str | None]] = []
    for body, num in pieces:
        if merged and ntok(merged[-1][0]) + ntok(body) <= max_tokens:
            prev_body, prev_num = merged[-1]
            label = prev_num
            if prev_num and num and num not in prev_num.split("-"):
                label = f"{prev_num}-{num}"
            merged[-1] = (f"{prev_body} {body}", label or num)
        else:
            merged.append((body, num))
    return merged


def chunk_act(act_key: str, max_tokens: int) -> dict:
    doc = json.loads((PARSED / f"{act_key}.json").read_text(encoding="utf-8"))
    act_name = doc["act"]["name"]
    chunks: list[dict] = []
    split_sections = 0

    # A section number is not a unique key. CrPC prints 100, 344, 429 and 526
    # twice (mid-document fragments the parser re-triggers on) and the Penal
    # Code prints s.2 under two chapters. Without a disambiguator their chunks
    # collide on id, and in a vector store one silently overwrites the other.
    seen_numbers: Counter[str] = Counter()

    for ch in doc["chapters"]:
        for sec in ch["sections"]:
            seen_numbers[sec["section_number"]] += 1
            occurrence = seen_numbers[sec["section_number"]]
            path = hierarchy_path(act_name, sec,
                                  "Passage" if doc.get("doc_type") == "document" else "Section")
            body = sec["text"]
            parts = [(body, None)]
            if ntok(body) > max_tokens:
                parts = split_oversized(sec, max_tokens)
                if len(parts) > 1:
                    split_sections += 1

            used_labels: set[str] = set()
            for i, (piece, sub) in enumerate(parts):
                # Merged labels can repeat within one section (s.499 produced
                # "a-b" twice), which would collide on id.
                if sub and sub in used_labels:
                    sub = f"{sub}#{i}"
                if sub:
                    used_labels.add(sub)
                label = path if sub is None else f"{path} > Subsection ({sub})"
                chunks.append(
                    {
                        "id": f"{act_key}_s{sec['section_number']}"
                        + (f"_dup{occurrence}" if occurrence > 1 else "")
                        + (f"_sub{sub}" if sub else "")
                        + (f"_p{i}" if sub is None and len(parts) > 1 else ""),
                        "text": f"{label}\n\n{piece}",
                        "metadata": {
                            "act": act_name,
                            "act_no": doc["act"]["act_no"],
                            "part": sec.get("part"),
                            "chapter": sec.get("chapter"),
                            "chapter_title": sec.get("chapter_title"),
                            "section": sec["section_number"],
                            "section_title": sec.get("section_title"),
                            "subsection": sub,
                            "page_start": sec["page_start"],
                            "page_end": sec["page_end"],
                            "source_pdf": doc["act"]["source_pdf"],
                            "tokens": ntok(piece),
                        },
                    }
                )

    sizes = sorted(ntok(c["text"]) for c in chunks)
    n_sections = sum(len(c["sections"]) for c in doc["chapters"])
    # Coverage: every section must produce at least one chunk, and every word of
    # section text must survive into some chunk for that section.
    by_section: dict[str, list[str]] = {}
    for c in chunks:
        by_section.setdefault(c["metadata"]["section"], []).append(c["text"])
    missing, lossy = [], []
    for ch in doc["chapters"]:
        for sec in ch["sections"]:
            got = by_section.get(sec["section_number"])
            if not got:
                missing.append(sec["section_number"])
                continue
            have = " ".join(got)
            src_words = sec["text"].split()
            if len(have.split()) < len(src_words):
                lossy.append(sec["section_number"])

    return {
        "act": act_name,
        "chunks": chunks,
        "stats": {
            "sections": n_sections,
            "chunks": len(chunks),
            "sections_split": split_sections,
            "median_tokens": int(statistics.median(sizes)) if sizes else 0,
            "p95_tokens": sizes[int(len(sizes) * 0.95) - 1] if sizes else 0,
            "max_tokens": max(sizes) if sizes else 0,
            "over_budget": sum(1 for s in sizes if s > max_tokens),
            "sections_with_no_chunk": missing,
            "sections_losing_text": lossy,
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--act", help="slug of one act; default is every parsed act")
    ap.add_argument("--max-tokens", type=int, default=DEFAULT_MAX)
    ap.add_argument("--show", type=int, default=2, help="sample chunks to print")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    # Whatever the parser produced, in whatever order it produced it.
    keys = [args.act] if args.act else sorted(p.stem for p in PARSED.glob("*.json"))
    if not keys:
        raise SystemExit(f"no parsed acts in {PARSED} — run parse_law_pdf.py first")
    grand = 0
    for k in keys:
        res = chunk_act(k, args.max_tokens)
        (OUT / f"{k}.json").write_text(
            json.dumps(res["chunks"], indent=2, ensure_ascii=False), encoding="utf-8"
        )
        st = res["stats"]
        grand += st["chunks"]
        print(f"\n=== {res['act']} ===")
        print(f"  sections {st['sections']}  ->  chunks {st['chunks']}"
              f"   ({st['sections_split']} sections split)")
        print(f"  tokens: median {st['median_tokens']}  p95 {st['p95_tokens']}  max {st['max_tokens']}")
        print(f"  over budget ({args.max_tokens}): {st['over_budget']}")
        print(f"  sections with no chunk: {len(st['sections_with_no_chunk'])}"
              f"   sections losing text: {len(st['sections_losing_text'])}")
        if st["sections_with_no_chunk"][:8]:
            print(f"    no chunk: {st['sections_with_no_chunk'][:8]}")
        if st["sections_losing_text"][:8]:
            print(f"    losing text: {st['sections_losing_text'][:8]}")

        for c in res["chunks"][: args.show]:
            print(f"\n  --- {c['id']} ({c['metadata']['tokens']} tok) ---")
            print("  " + c["text"][:330].replace("\n", "\n  "))

    print(f"\ntotal chunks: {grand}  ->  {OUT}")


if __name__ == "__main__":
    main()
