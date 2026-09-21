"""Check `catalog.sqlite` against the parsed JSON it was built from.

The build script reports what it thinks it inserted. That is not evidence: a
builder that drops a section drops it from its own count too. So this re-reads
`Laws/main/parsed/*.json` and compares, the same way `verify_parsed.py` re-reads
the PDF rather than trusting the parser.

What is checked:

  counts        acts, chapters, sections, subsections — from the JSON, per act
  text parity   every section's text, byte for byte, not a length or a hash of
                what the builder happened to hold in memory
  identity      citation keys unique; no section orphaned from its chapter
  ordering      statutory order, i.e. 302 < 302A < 303, which is the one thing
                lexicographic sorting gets wrong and nobody notices until a
                range query returns the wrong interval
  hierarchy     every nested child present, with its parent link intact
  FTS parity    the search index covers exactly the sections table
  cross-refs    resolvable targets actually exist; unresolved ones are reported
                as a rate, not failed — drafters cite repealed and out-of-corpus
                provisions, and that is the document's business, not a bug
  determinism   rebuild into a temp file; the content digest must match

Exit code is 1 if any check fails, so it can gate a rebuild.

Run (from backend/, ainshonglap conda env active):

    python scripts/verify/verify_catalog.py
    python scripts/verify/verify_catalog.py --skip-rebuild   # faster
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import sys
import tempfile

BACKEND = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

from app.rag import config  # noqa: E402
from build_catalog import build, content_hash, flatten_children  # noqa: E402

FAILURES: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{'  ' + detail if detail else ''}")
    if not ok:
        FAILURES.append(f"{label} {detail}".strip())
    return ok


def load_parsed() -> dict[str, dict]:
    out = {}
    for slug in config.acts():
        p = config.PARSED_DIR / f"{slug}.json"
        if p.exists():
            out[slug] = json.loads(p.read_text(encoding="utf-8"))
    return out


def verify(db_path: pathlib.Path, rebuild: bool = True) -> None:
    parsed = load_parsed()
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    print("counts (catalog vs parsed JSON)")
    check(con.execute("SELECT COUNT(*) FROM acts").fetchone()[0] == len(parsed),
          "acts", f"{len(parsed)}")

    for slug, doc in parsed.items():
        row = con.execute("SELECT id, n_chapters, n_sections FROM acts WHERE slug=?",
                          (slug,)).fetchone()
        if not check(row is not None, f"{slug}: present"):
            continue
        act_id = row["id"]
        want_ch = len(doc["chapters"])
        want_sec = sum(len(c["sections"]) for c in doc["chapters"])
        want_sub = sum(
            sum(1 for _ in flatten_children(s.get("children")))
            for c in doc["chapters"] for s in c["sections"])

        got_ch = con.execute("SELECT COUNT(*) FROM chapters WHERE act_id=?",
                             (act_id,)).fetchone()[0]
        got_sec = con.execute("SELECT COUNT(*) FROM sections WHERE act_id=?",
                              (act_id,)).fetchone()[0]
        got_sub = con.execute(
            "SELECT COUNT(*) FROM subsections su JOIN sections s ON s.id=su.section_id"
            " WHERE s.act_id=?", (act_id,)).fetchone()[0]

        check(got_ch == want_ch, f"{slug}: chapters", f"{got_ch} == {want_ch}")
        check(got_sec == want_sec, f"{slug}: sections", f"{got_sec} == {want_sec}")
        check(got_sub == want_sub, f"{slug}: subsections", f"{got_sub} == {want_sub}")
        check(row["n_sections"] == want_sec, f"{slug}: stored n_sections",
              f"{row['n_sections']} == {want_sec}")

    print("\ntext parity (every section, byte for byte)")
    # Compared by POSITION, not by citation key. Five section numbers occur
    # twice in the corpus, so a key is not a unique handle on a section — the
    # first version of this check assumed it was and reported the catalog as
    # broken when the parsed JSON was what held duplicates.
    mismatches, missing, total = [], [], 0
    for slug, doc in parsed.items():
        rows = con.execute(
            "SELECT s.seq, s.text, s.number, s.key FROM sections s"
            " JOIN acts a ON a.id = s.act_id WHERE a.slug = ? ORDER BY s.seq",
            (slug,)).fetchall()
        by_seq = {r["seq"]: r for r in rows}
        seq = 0
        for ch in doc["chapters"]:
            for sec in ch["sections"]:
                total += 1
                got = by_seq.get(seq)
                seq += 1
                if got is None:
                    missing.append(f"{slug}#{seq - 1}")
                    continue
                if got["number"] != str(sec["section_number"]):
                    mismatches.append(f"{got['key']} (number moved)")
                elif got["text"] != sec["text"]:
                    mismatches.append(got["key"])
    check(not missing, "no section missing",
          f"{len(missing)} missing" + (f" e.g. {missing[:3]}" if missing else ""))
    check(not mismatches, "text identical",
          f"{total - len(mismatches)}/{total}" +
          (f" e.g. {mismatches[:3]}" if mismatches else ""))

    print("\nidentity and hierarchy")
    dupes = con.execute(
        "SELECT key, COUNT(*) c FROM sections GROUP BY key HAVING c > 1").fetchall()
    check(not dupes, "citation keys unique",
          f"{len(dupes)} duplicated" + (f" e.g. {dupes[0]['key']}" if dupes else ""))

    # A repeated section number is a parser artifact, not a catalog one: a page
    # break where the spilled text begins "344. (10) If, before..." reads like a
    # heading. Reported every run so the count cannot creep up unnoticed, and
    # asserted to stay at the 5 known cases.
    conts = con.execute(
        "SELECT s.key, s.number, s.page_start, LENGTH(s.text) n, a.slug"
        " FROM sections s JOIN acts a ON a.id = s.act_id"
        " WHERE s.continuation = 1 ORDER BY a.slug, s.seq").fetchall()
    for c in conts:
        print(f"  INFO  continuation fragment  {c['slug']}-s{c['number']}"
              f" p.{c['page_start']} ({c['n']} chars) -> {c['key']}")
    check(len(conts) == 5, "known continuation fragments only",
          f"{len(conts)} (expected 5)")

    canon = con.execute(
        "SELECT COUNT(*) FROM sections WHERE dup_seq = 0 AND key LIKE '%#%'"
    ).fetchone()[0]
    check(canon == 0, "the section proper keeps the plain key", f"{canon} do not")

    orphans = con.execute(
        "SELECT COUNT(*) FROM sections WHERE chapter_id IS NULL"
        " OR chapter_id NOT IN (SELECT id FROM chapters)").fetchone()[0]
    check(orphans == 0, "every section in a chapter", f"{orphans} orphaned")

    bad_parent = con.execute(
        "SELECT COUNT(*) FROM subsections WHERE depth > 0 AND parent_id IS NULL"
    ).fetchone()[0]
    check(bad_parent == 0, "nested children keep their parent",
          f"{bad_parent} lost")

    cross_act = con.execute(
        "SELECT COUNT(*) FROM sections s JOIN chapters c ON c.id = s.chapter_id"
        " WHERE c.act_id != s.act_id").fetchone()[0]
    check(cross_act == 0, "no section under another Act's chapter", f"{cross_act}")

    print("\nstatutory ordering (302 < 302A < 303)")
    bad_order = []
    for slug in parsed:
        rows = con.execute(
            "SELECT number, num_int, num_suffix FROM sections s"
            " JOIN acts a ON a.id = s.act_id WHERE a.slug = ?"
            " ORDER BY s.num_int, s.num_suffix", (slug,)).fetchall()
        keys = [(r["num_int"], r["num_suffix"]) for r in rows]
        if keys != sorted(keys):
            bad_order.append(slug)
        nulls = [r["number"] for r in rows if r["num_int"] is None]
        if nulls:
            bad_order.append(f"{slug}:unparsed={nulls[:3]}")
    check(not bad_order, "num_int/num_suffix sort statutorily", str(bad_order or ""))

    # The ordering columns must agree with the printed number, or a range query
    # silently covers the wrong interval.
    wrong_split = con.execute(
        "SELECT COUNT(*) FROM sections"
        " WHERE number != CAST(num_int AS TEXT) || num_suffix").fetchone()[0]
    check(wrong_split == 0, "num_int || num_suffix == number", f"{wrong_split} differ")

    print("\nfull-text index")
    fts = con.execute("SELECT COUNT(*) FROM sections_fts").fetchone()[0]
    secs = con.execute("SELECT COUNT(*) FROM sections").fetchone()[0]
    check(fts == secs, "FTS covers every section", f"{fts} == {secs}")
    hit = con.execute(
        "SELECT s.key FROM sections_fts f JOIN sections s ON s.id = f.rowid"
        " WHERE sections_fts MATCH 'murder' LIMIT 1").fetchone()
    check(hit is not None, "FTS returns a match for 'murder'",
          hit["key"] if hit else "no hits")

    print("\ncross-references")
    total_x = con.execute("SELECT COUNT(*) FROM cross_references").fetchone()[0]
    no_act = con.execute(
        "SELECT COUNT(*) FROM cross_references WHERE target_act_id IS NULL"
    ).fetchone()[0]
    unresolved = con.execute(
        "SELECT COUNT(*) FROM cross_references x LEFT JOIN sections s"
        " ON s.act_id = x.target_act_id AND s.number = x.target_number"
        " WHERE s.id IS NULL").fetchone()[0]
    check(total_x > 0, "references extracted", f"{total_x}")
    check(no_act == 0, "every reference names an Act", f"{no_act} without")
    # An unresolved target is usually fine — drafters cite repealed provisions
    # and Acts outside this corpus. Two patterns inside that number are not
    # fine, so they are separated out instead of averaging into a rate:
    #
    #   gap        'section 99A' is cited and 99B..99G are in the catalog. A
    #              lettered series missing its first member is a parsing gap,
    #              not a drafting quirk.
    #   mis-target a reference inside the CrPC to 'section 323' with no Act
    #              named defaults to the CrPC, but 323 is a Penal Code section.
    #              The build stays conservative rather than guessing; this
    #              counts how often the conservative default lands nowhere.
    gaps, elsewhere = [], []
    for r in con.execute(
            "SELECT DISTINCT ta.slug, x.target_number FROM cross_references x"
            " JOIN acts ta ON ta.id = x.target_act_id LEFT JOIN sections s"
            " ON s.act_id = x.target_act_id AND s.number = x.target_number"
            " WHERE s.id IS NULL"):
        slug, num = r["slug"], r["target_number"]
        base = "".join(ch for ch in num if ch.isdigit())
        sibling = con.execute(
            "SELECT COUNT(*) FROM sections s JOIN acts a ON a.id = s.act_id"
            " WHERE a.slug = ? AND s.num_int = ? AND s.num_suffix != ''",
            (slug, int(base) if base else -1)).fetchone()[0]
        if num != base and sibling:
            gaps.append(f"{slug}-s{num}")
        other = con.execute(
            "SELECT a.slug FROM sections s JOIN acts a ON a.id = s.act_id"
            " WHERE s.number = ? AND a.slug != ? LIMIT 1", (num, slug)).fetchone()
        if other:
            elsewhere.append(f"{num}: {slug}->{other['slug']}")
    if gaps:
        print(f"  INFO  cited but absent beside lettered siblings (parse gap?):"
              f" {', '.join(sorted(gaps)[:6])}")
    print(f"  INFO  unresolved in own Act but present in another:"
          f" {len(elsewhere)} (e.g. {', '.join(sorted(elsewhere)[:3])})")

    rate = 100.0 * unresolved / total_x if total_x else 0.0
    # Reported, not failed: a section may cite a repealed provision or one in an
    # Act outside this corpus, and the catalog must not invent a target for it.
    print(f"  INFO  unresolved targets  {unresolved}/{total_x} ({rate:.1f}%)")
    check(rate < 25.0, "unresolved rate below 25%", f"{rate:.1f}%")

    con.close()

    if rebuild:
        print("\ndeterminism")
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td) / "rebuild.sqlite"
            build(tmp)
            check(content_hash(tmp) == content_hash(db_path),
                  "rebuild produces the same rows")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--skip-rebuild", action="store_true")
    args = ap.parse_args()

    db = pathlib.Path(args.db) if args.db else config.CATALOG
    if not db.exists():
        sys.exit(f"no catalog at {db} — run scripts/build_catalog.py first")
    print(f"catalog: {db}\n")
    verify(db, rebuild=not args.skip_rebuild)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) FAILED")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
