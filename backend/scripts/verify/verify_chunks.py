"""Verify the chunks against the parsed JSON they came from.

Same discipline as verify_parsed.py: the chunker's own "0 sections lost" is
self-reported and cannot be the proof. Everything here is recomputed from
`Laws/main/parsed/` independently of anything the chunker recorded.

  K1  text fidelity     Each section's chunks, concatenated in order, must
                        reproduce that section's text as a WORD SEQUENCE —
                        not as a set. A set comparison passes even when text is
                        duplicated or scrambled, which is how the first version
                        of this check missed both.
  K2  no duplication    No section's text may appear twice across its chunks.
                        Over-eager merging silently doubles content.
  K3  coverage          Every section yields >= 1 chunk; every chunk maps to a
                        real section. Nothing invented, nothing dropped.
  K4  ids               Chunk ids are unique — they become vector-store keys.
  K5  metadata          Every chunk's metadata matches its source section, and
                        the hierarchy prefix agrees with the metadata. A chunk
                        whose prefix and metadata disagree would cite wrongly.
  K6  size              Distribution, and how many exceed the budget.
  K7  prefix            Every chunk carries a hierarchy prefix and a body; a
                        prefix-only or body-only chunk is malformed.

Run (from backend/, ainshonglap conda env active):

    python scripts/verify/verify_chunks.py
    python scripts/verify/verify_chunks.py --act crpc --verbose
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import statistics
from collections import Counter

LAWS = pathlib.Path(__file__).resolve().parents[3] / "Laws" / "main"
PARSED = LAWS / "parsed"
CHUNKS = LAWS / "chunks"
BUDGET = 512


def words(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (s or "").lower())


def body_of(chunk: dict) -> str:
    """Chunk text minus the hierarchy prefix."""
    parts = chunk["text"].split("\n\n", 1)
    return parts[1] if len(parts) == 2 else ""


def prefix_of(chunk: dict) -> str:
    return chunk["text"].split("\n\n", 1)[0]


def verify(act_key: str, verbose: bool) -> bool:
    par = json.loads((PARSED / f"{act_key}.json").read_text(encoding="utf-8"))
    chunks = json.loads((CHUNKS / f"{act_key}.json").read_text(encoding="utf-8"))
    # Section numbers are not unique (CrPC prints 100/344/429/526 twice, Penal
    # s.2 twice), so sections are keyed by (number, occurrence) and chunks are
    # matched via the "_dup<n>" marker the chunker puts in the id. Keying on the
    # bare number pooled duplicate sections' chunks and compared them against
    # only one source, reporting false text-fidelity and duplication failures.
    seen: Counter = Counter()
    sections: dict[tuple[str, int], dict] = {}
    for c in par["chapters"]:
        for s in c["sections"]:
            seen[s["section_number"]] += 1
            sections[(s["section_number"], seen[s["section_number"]])] = s

    def key_of(chunk: dict) -> tuple[str, int]:
        m = re.search(r"_dup(\d+)", chunk["id"])
        return (chunk["metadata"]["section"], int(m.group(1)) if m else 1)

    by_sec: dict[tuple[str, int], list[dict]] = {}
    for c in chunks:
        by_sec.setdefault(key_of(c), []).append(c)

    # K1 — word-sequence fidelity, order preserved
    k1_bad = []
    for num, sec in sections.items():
        got = by_sec.get(num)
        if not got:
            continue
        want = words(sec["text"])
        have = words(" ".join(body_of(c) for c in got))
        if want != have:
            k1_bad.append((num[0], len(want), len(have)))

    # K2 — duplication: a chunk set longer than its source means repeats
    k2_bad = []
    for num, sec in sections.items():
        got = by_sec.get(num) or []
        want, have = words(sec["text"]), words(" ".join(body_of(c) for c in got))
        if len(have) > len(want):
            k2_bad.append((num[0], len(want), len(have)))

    # K3 — coverage both directions
    no_chunk = [n[0] for n in sections if n not in by_sec]
    orphan = [n[0] for n in by_sec if n not in sections]

    # K4 — unique ids
    ids = Counter(c["id"] for c in chunks)
    dupe_ids = [i for i, n in ids.items() if n > 1]

    # K5 — metadata agrees with the source section AND with the prefix
    k5_bad = []
    for c in chunks:
        m, sec = c["metadata"], sections.get(key_of(c))
        if not sec:
            continue
        if (m.get("chapter") != sec.get("chapter")
                or m.get("page_start") != sec.get("page_start")
                or m.get("section_title") != sec.get("section_title")):
            k5_bad.append((c["id"], "metadata != parsed section"))
            continue
        pre = prefix_of(c)
        # The prefix must identify the chunk's section and its Act — those are
        # what a reader needs to place a citation. It is NOT required to repeat
        # the chapter: E2 showed the longer Part>Chapter path bought nothing, so
        # chapter now lives in metadata only (used for filtering and display).
        if f"Section {m['section']}" not in pre:
            k5_bad.append((c["id"], "prefix omits its own section number"))
        elif m.get("act") and m["act"].split(",")[0] not in pre:
            k5_bad.append((c["id"], "prefix omits its Act"))

    # K6 — sizes
    sizes = sorted(int(len(c["text"].split()) * 1.35) for c in chunks)
    over = [c["id"] for c in chunks if int(len(c["text"].split()) * 1.35) > BUDGET]

    # K7 — well-formed
    malformed = [c["id"] for c in chunks if not body_of(c).strip() or not prefix_of(c).strip()]

    ok = not (k1_bad or k2_bad or no_chunk or orphan or dupe_ids or k5_bad or malformed)

    print(f"\n=== {par['act']['name']} ===")
    print(f"  sections {len(sections)}  ->  chunks {len(chunks)}")
    print(f"  K1 text fidelity     {len(k1_bad)} sections whose chunks != source word sequence")
    for n, w, h in k1_bad[:6]:
        print(f"                       s.{n}: source {w} words, chunks {h}")
    print(f"  K2 duplication       {len(k2_bad)} sections with repeated text")
    for n, w, h in k2_bad[:6]:
        print(f"                       s.{n}: source {w} words, chunks {h} (+{h-w})")
    print(f"  K3 coverage          {len(no_chunk)} sections with no chunk, {len(orphan)} orphan chunks")
    if no_chunk[:8]:
        print(f"                       no chunk: {no_chunk[:8]}")
    if orphan[:8]:
        print(f"                       orphan:   {orphan[:8]}")
    print(f"  K4 ids               {len(dupe_ids)} duplicate ids" + (f" {dupe_ids[:5]}" if dupe_ids else ""))
    print(f"  K5 metadata/prefix   {len(k5_bad)} mismatches")
    for i, why in k5_bad[:6]:
        print(f"                       {i}: {why}")
    print(f"  K6 size              median {statistics.median(sizes):.0f}  "
          f"p95 {sizes[int(len(sizes)*0.95)-1]}  max {max(sizes)}  over {BUDGET}: {len(over)}")
    print(f"  K7 well-formed       {len(malformed)} malformed")
    if verbose and over[:10]:
        print(f"                       oversize: {over[:10]}")
    print(f"  ---> {'PASS' if ok else 'ATTENTION'}")
    return ok


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--act", choices=["crpc", "penal", "evidence"])
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    keys = [args.act] if args.act else ["crpc", "penal", "evidence"]
    res = [verify(k, args.verbose) for k in keys]
    print(f"\n{sum(res)}/{len(res)} acts pass all checks")


if __name__ == "__main__":
    main()
