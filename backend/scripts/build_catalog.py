"""Build Laws/main/catalog.sqlite from the parsed JSON: sections, chapters and
cross-references for exact lookups.  Run: python scripts/build_catalog.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.rag import config  # noqa: E402

from app.rag.catalog_schema import SCHEMA  # noqa: E402  (one definition, shared)

_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def roman_to_int(r: str) -> int | None:
    """'XVI' -> 16. None when it is not roman at all."""
    s = str(r or "").strip().upper()
    if not s or any(ch not in _ROMAN for ch in s):
        return None
    total, prev = 0, 0
    for ch in reversed(s):
        v = _ROMAN[ch]
        total, prev = (total - v if v < prev else total + v), max(v, prev)
    return total


def chapter_int(number: str | None) -> int | None:
    s = str(number or "").strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    return roman_to_int(s)


def split_number(num: str) -> tuple[int | None, str]:
    """'302A' -> (302, 'A'). Sorting and range predicates both need the split;
    lexicographic order puts '302A' before '31', which is not statutory order."""
    m = re.match(r"\s*(\d+)\s*([A-Za-z]*)", str(num or ""))
    if not m:
        return None, ""
    return int(m.group(1)), m.group(2).upper()


# A reference to another provision, as drafters write them:
#   "section 302", "sections 107 and 108", "sections 378 to 382",
#   "section 34 of the Penal Code"
XREF_RE = re.compile(
    r"\bsections?\s+"
    r"(\d{1,3}[A-Z]{0,2}"
    r"(?:\s*(?:,|and|or|to|-|–)\s*\d{1,3}[A-Z]{0,2})*)",
    re.I,
)
XREF_NUM_RE = re.compile(r"\d{1,3}[A-Z]{0,2}", re.I)
# "section 3 and 2nd Schedule" is a reference to section 3 and a schedule, not
# to a section "2ND". Statutory suffixes are letters (302A, 489B); an ordinal
# ending is a different kind of word, and taking it produced 49 references to a
# section that does not exist.
ORDINAL_RE = re.compile(r"^\d{1,3}(ST|ND|RD|TH)$", re.I)
# Which Act the reference lands in, read from the words just after it. "this
# Code"/"this Act" and silence both mean the Act the section is in.
XREF_ACT_RE = re.compile(
    r"^\s*(?:of\s+)?the\s+"
    r"(?:(code\s+of\s+criminal\s+procedure|criminal\s+procedure\s+code)"
    r"|(penal\s+code)|(evidence\s+act))",
    re.I,
)


def extract_xrefs(text: str, own_act: str) -> list[dict]:
    """Outgoing references from one section's text.

    Deliberately conservative: only phrases that literally say "section N" are
    taken. A missed edge costs a suggestion; an invented edge would put two
    unrelated provisions in one answer.
    """
    out: list[dict] = []
    for m in XREF_RE.finditer(text or ""):
        span = m.group(1)
        tail = text[m.end(): m.end() + 48]
        am = XREF_ACT_RE.match(tail)
        if am:
            target = "crpc" if am.group(1) else "penal" if am.group(2) else "evidence"
        else:
            target = own_act
        nums = [n.upper() for n in XREF_NUM_RE.findall(span)
                if not ORDINAL_RE.match(n)]
        if not nums:
            continue
        ranged = re.search(r"\b(to|-|–)\b|-|–", span) is not None and len(nums) >= 2
        if ranged:
            out.append({"target_act": target, "number": nums[0], "kind": "range",
                        "to": nums[-1], "raw": m.group(0)[:120]})
        else:
            for n in nums:
                out.append({"target_act": target, "number": n, "kind": "single",
                            "to": None, "raw": m.group(0)[:120]})
    return out


def dup_map(doc: dict) -> dict[int, int]:
    """id(section dict) -> how many times this number was already seen.

    Five section numbers in the corpus appear twice, and in every case the
    second is a continuation the parser re-detected as a new section: a page or
    column break where the spilled text begins "344. (10) If, before the
    argument..." reads exactly like a section heading. All five second
    occurrences are untitled (or carry a marginal note as a title); all five
    firsts carry the real rubric.

    They are kept rather than dropped — the text is in the document, and a read
    model that silently discards source text is the failure this project is
    built to avoid — but they cannot hold the citation key, or `penal-s2` would
    resolve to a fragment from page 104 instead of section 2. So the first
    occurrence in corpus order keeps the key and the later ones are flagged.

    This is a finding about the parser, recorded here rather than papered over:
    `app/rag/library.py` concatenates same-key chunks, so today these fragments
    are silently glued onto the end of the real section's text.
    """
    counts: dict[str, int] = {}
    out: dict[int, int] = {}
    for ch in doc["chapters"]:
        for sec in ch["sections"]:
            num = str(sec["section_number"])
            out[id(sec)] = counts.get(num, 0)
            counts[num] = counts.get(num, 0) + 1
    return out


def flatten_children(children: list[dict] | None, depth: int = 0):
    """Walk the nested child tree, yielding (depth, node). Clauses sit inside
    subsections in the parsed JSON, and a flat table needs the parent link kept
    rather than the nesting thrown away."""
    for node in (children or []):
        yield depth, node
        yield from flatten_children(node.get("children"), depth + 1)


def build(db_path: pathlib.Path) -> dict:
    if db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)

    stats = {"acts": 0, "chapters": 0, "sections": 0, "subsections": 0,
             "xrefs": 0, "continuations": 0}
    pending_xrefs: list[tuple[int, dict]] = []
    act_ids: dict[str, int] = {}

    for slug in config.acts():
        p = config.PARSED_DIR / f"{slug}.json"
        if not p.exists():
            continue
        doc = json.loads(p.read_text(encoding="utf-8"))
        a = doc["act"]
        n_sec = sum(len(c["sections"]) for c in doc["chapters"])
        cur = con.execute(
            "INSERT INTO acts (slug, name, act_no, source_pdf, n_chapters, n_sections)"
            " VALUES (?,?,?,?,?,?)",
            (slug, a["name"], a.get("act_no"), a.get("source_pdf"),
             len(doc["chapters"]), n_sec),
        )
        act_id = cur.lastrowid
        act_ids[slug] = act_id
        stats["acts"] += 1

        sec_seq = 0
        dups = dup_map(doc)
        for ch_seq, ch in enumerate(doc["chapters"]):
            cur = con.execute(
                "INSERT INTO chapters (act_id, number, number_int, title, part,"
                " part_title, seq, n_sections) VALUES (?,?,?,?,?,?,?,?)",
                (act_id, ch.get("number"), chapter_int(ch.get("number")),
                 ch.get("title"), ch.get("part"), ch.get("part_title"),
                 ch_seq, len(ch["sections"])),
            )
            chapter_id = cur.lastrowid
            stats["chapters"] += 1

            for sec in ch["sections"]:
                num = str(sec["section_number"])
                num_int, suffix = split_number(num)
                d = dups[id(sec)]
                key = f"{slug}-s{num}" if d == 0 else f"{slug}-s{num}#{d + 1}"
                cur = con.execute(
                    "INSERT INTO sections (act_id, chapter_id, number, number_to,"
                    " num_int, num_suffix, title, text, page_start, page_end, seq,"
                    " key, dup_seq, continuation) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (act_id, chapter_id, num, sec.get("section_number_to"),
                     num_int, suffix, sec.get("section_title"), sec["text"],
                     sec.get("page_start"), sec.get("page_end"), sec_seq,
                     key, d, 1 if d else 0),
                )
                section_id = cur.lastrowid
                sec_seq += 1
                stats["sections"] += 1
                stats["continuations"] += 1 if d else 0

                # depth -> the id of the last node seen at that depth, so a
                # clause records the subsection it sits in.
                last_at: dict[int, int] = {}
                for sub_seq, (depth, node) in enumerate(
                        flatten_children(sec.get("children"))):
                    cur = con.execute(
                        "INSERT INTO subsections (section_id, parent_id, number,"
                        " type, text, depth, seq) VALUES (?,?,?,?,?,?,?)",
                        (section_id, last_at.get(depth - 1), node.get("number"),
                         node.get("type", "part"), node.get("text", ""),
                         depth, sub_seq),
                    )
                    last_at[depth] = cur.lastrowid
                    stats["subsections"] += 1

                for x in extract_xrefs(sec["text"], slug):
                    pending_xrefs.append((section_id, x))

    # Second pass: targets can point at an Act that had not been inserted yet.
    for section_id, x in pending_xrefs:
        con.execute(
            "INSERT INTO cross_references (section_id, target_act_id,"
            " target_number, kind, target_to, raw) VALUES (?,?,?,?,?,?)",
            (section_id, act_ids.get(x["target_act"]), x["number"], x["kind"],
             x["to"], x["raw"]),
        )
        stats["xrefs"] += 1

    con.execute("INSERT INTO sections_fts(rowid, title, text)"
                " SELECT id, COALESCE(title,''), text FROM sections")
    con.commit()
    con.execute("VACUUM")
    con.commit()
    con.close()
    return stats


def content_hash(db_path: pathlib.Path) -> str:
    """Digest of the rows, not the file.

    The file's bytes can differ between rebuilds without a single value
    changing — that is SQLite's page allocation, not our data. Hashing the rows
    in a fixed order is the check that actually means "same catalog".
    """
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    h = hashlib.sha256()
    for table, order in (("acts", "slug"), ("chapters", "act_id, seq"),
                         ("sections", "act_id, seq"),
                         ("subsections", "section_id, seq"),
                         ("cross_references", "section_id, id")):
        for row in con.execute(f"SELECT * FROM {table} ORDER BY {order}"):
            h.update(repr(row).encode("utf-8"))
    con.close()
    return h.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None, help="catalog path (default: config.CATALOG)")
    ap.add_argument("--hash", action="store_true",
                    help="print the content digest after building")
    args = ap.parse_args()

    out = pathlib.Path(args.out) if args.out else config.CATALOG
    stats = build(out)
    print(f"-> {out}")
    for k, v in stats.items():
        print(f"   {k:13} {v}")
    print(f"   {'size':13} {out.stat().st_size/1e6:.1f} MB")
    if args.hash:
        print(f"   {'content':13} {content_hash(out)}")


if __name__ == "__main__":
    main()
