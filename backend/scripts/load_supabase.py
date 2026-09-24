"""Load the corpus into Supabase, one Act at a time, from the local read models.
Unchanged Acts are skipped.  Run: python scripts/load_supabase.py --all
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sqlite3
import sys
import time

import numpy as np

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.rag import config  # noqa: E402

BATCH = {"law_chapters": 500, "law_sections": 200, "law_subsections": 500,
         "law_cross_references": 500, "law_chunks": 60, "law_alt_chunks": 30}


def _vector_literal(v: np.ndarray) -> str:
    """pgvector's text form, at float32's shortest round-trip precision.

    `unique=True` prints the shortest decimal that parses back to the same
    float32, so the value stored is bit-identical to the one in embeddings.npy
    — and at roughly half the payload of printing it as a double.
    """
    return "[" + ",".join(np.format_float_positional(x, unique=True, trim="-")
                          for x in v.astype(np.float32)) + "]"


def build(slug: str, db: sqlite3.Connection, chunks: list[dict],
          emb: np.ndarray) -> dict[str, list[dict]]:
    """Every row for one Act, keyed by table. Deterministic order throughout."""
    db.row_factory = sqlite3.Row
    act = db.execute("select * from acts where slug = ?", (slug,)).fetchone()
    if act is None:
        raise SystemExit(f"no act {slug!r} in the catalog")

    chapter_ids = {}
    chapters = []
    for r in db.execute("select * from chapters where act_id = ? order by seq",
                        (act["id"],)):
        cid = f"{slug}:ch{r['seq']}"
        chapter_ids[r["id"]] = cid
        chapters.append({"id": cid, "act_slug": slug, "number": r["number"],
                         "number_int": r["number_int"], "title": r["title"],
                         "part": r["part"], "part_title": r["part_title"],
                         "seq": r["seq"], "n_sections": r["n_sections"]})

    section_keys = {}
    sections = []
    for r in db.execute("select * from sections where act_id = ? order by seq",
                        (act["id"],)):
        section_keys[r["id"]] = r["key"]
        sections.append({"key": r["key"], "act_slug": slug,
                         "chapter_id": chapter_ids.get(r["chapter_id"]),
                         "number": r["number"], "number_to": r["number_to"],
                         "num_int": r["num_int"], "num_suffix": r["num_suffix"],
                         "title": r["title"], "text": r["text"],
                         "page_start": r["page_start"], "page_end": r["page_end"],
                         "seq": r["seq"], "dup_seq": r["dup_seq"],
                         "continuation": bool(r["continuation"])})

    sub_ids = {}
    subsections = []
    rows = db.execute(
        "select s.* from subsections s join sections x on x.id = s.section_id "
        "where x.act_id = ? order by x.seq, s.seq", (act["id"],)).fetchall()
    for r in rows:
        sub_ids[r["id"]] = f"{section_keys[r['section_id']]}:ss{r['seq']}"
    for r in rows:
        subsections.append({"id": sub_ids[r["id"]],
                            "section_key": section_keys[r["section_id"]],
                            "parent_id": sub_ids.get(r["parent_id"]),
                            "number": r["number"], "type": r["type"],
                            "text": r["text"], "depth": r["depth"], "seq": r["seq"]})

    slugs = {r["id"]: r["slug"] for r in db.execute("select id, slug from acts")}
    xrefs = []
    counter: dict[str, int] = {}
    for r in db.execute(
            "select c.* from cross_references c join sections x on x.id = c.section_id "
            "where x.act_id = ? order by x.seq, c.id", (act["id"],)):
        key = section_keys[r["section_id"]]
        n = counter.get(key, 0)
        counter[key] = n + 1
        xrefs.append({"id": f"{key}:r{n}", "section_key": key,
                      "target_act_slug": slugs.get(r["target_act_id"]),
                      "target_number": r["target_number"], "kind": r["kind"],
                      "target_to": r["target_to"], "raw": r["raw"]})

    # Chunks belong to this Act by the Act NAME in their metadata, which is how
    # the pipeline itself groups them.
    name = act["name"].strip().lower()
    chunk_rows = []
    for vec_id, c in enumerate(chunks):
        m = c["metadata"]
        if m["act"].strip().lower() != name:
            continue
        chunk_rows.append({"vec_id": vec_id, "chunk_id": c["id"], "act_slug": slug,
                           "section": str(m["section"]),
                           "granularity": m.get("granularity") or "section",
                           "text": c["text"], "metadata": m,
                           "embedding": _vector_literal(emb[vec_id])})

    return {
        "law_acts": [{"slug": slug, "name": act["name"], "act_no": act["act_no"],
                      "source_pdf": act["source_pdf"],
                      "n_chapters": act["n_chapters"],
                      "n_sections": act["n_sections"]}],
        "law_chapters": chapters,
        "law_sections": sections,
        "law_subsections": subsections,
        "law_cross_references": xrefs,
        "law_chunks": chunk_rows,
    }


def digest(rows: dict[str, list[dict]]) -> str:
    """sha256 over every row, so an unchanged Act is recognised and skipped."""
    h = hashlib.sha256()
    for table in sorted(rows):
        h.update(table.encode())
        h.update(json.dumps(rows[table], sort_keys=True, ensure_ascii=False).encode())
    return h.hexdigest()


def _insert(client, table: str, rows: list[dict]) -> None:
    from postgrest.types import ReturnMethod

    size = BATCH.get(table, 500)
    for i in range(0, len(rows), size):
        part = rows[i:i + size]
        for attempt in range(3):
            try:
                client.table(table).insert(part, returning=ReturnMethod.minimal).execute()
                break
            except Exception:
                # A dropped connection mid-load is worth one or two retries; a
                # schema error will fail identically each time and surface.
                if attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))


def load(client, slug: str, rows: dict[str, list[dict]], d: str) -> None:
    # Delete first: the cascade removes chapters, sections, subsections,
    # references, chunks and the old load record in one statement.
    client.table("law_acts").delete().eq("slug", slug).execute()
    for table in ("law_acts", "law_chapters", "law_sections", "law_subsections",
                  "law_cross_references", "law_chunks"):
        t0 = time.time()
        _insert(client, table, rows[table])
        print(f"    {table:22s} {len(rows[table]):5d} rows  {time.time() - t0:5.1f}s")
    # The commit marker, last.
    client.table("law_loads").insert({
        "act_slug": slug, "digest": d,
        "n_sections": len(rows["law_sections"]),
        "n_chunks": len(rows["law_chunks"])}).execute()


# ── alternate embedders (E21, migration 003) ────────────────────────────────
# Another model's vectors for the same chunks, into law_alt_chunks. Reloading an
# Act above deletes its law_acts row, and the cascade takes that Act's alternate
# vectors and their load record with it — so after any reload these must be
# loaded again, which the digest check below does by itself.

def build_alt(slug: str, db: sqlite3.Connection, model: str, chunks: list[dict],
              emb: np.ndarray) -> list[dict]:
    """law_alt_chunks rows for one Act and one model, the chunks `build` selects."""
    name = db.execute("select name from acts where slug = ?", (slug,)).fetchone()[0]
    name = name.strip().lower()
    return [{"model": model, "vec_id": vec_id, "chunk_id": c["id"], "act_slug": slug,
             "section": str(c["metadata"]["section"]),
             "granularity": c["metadata"].get("granularity") or "section",
             "text": c["text"], "metadata": c["metadata"],
             "embedding": _vector_literal(emb[vec_id])}
            for vec_id, c in enumerate(chunks)
            if c["metadata"]["act"].strip().lower() == name]


def load_alt(client, slug: str, model: str, rows: list[dict], force: bool = False,
             log=print) -> bool:
    """Replace one Act's vectors for one model. False when already current."""
    import hashlib

    d = hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False)
                       .encode()).hexdigest()
    have = (client.table("law_alt_loads").select("digest")
            .eq("model", model).eq("act_slug", slug).execute().data)
    if have and have[0]["digest"] == d and not force:
        return False
    client.table("law_alt_chunks").delete().eq("model", model).eq("act_slug", slug).execute()
    client.table("law_alt_loads").delete().eq("model", model).eq("act_slug", slug).execute()
    t0 = time.time()
    _insert(client, "law_alt_chunks", rows)
    client.table("law_alt_loads").insert({"model": model, "act_slug": slug,
                                          "digest": d, "n_chunks": len(rows)}).execute()
    log(f"    {slug}: {len(rows)} {model} vectors  {time.time() - t0:.1f}s")
    return True


def alt_models() -> list[str]:
    """Remote embedders that have a local vector file to load."""
    from app.rag.store import alt_paths

    return [m for m in config.REMOTE_EMBEDDERS if alt_paths(m)[0].exists()]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--act", action="append", help="act slug; repeatable")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--force", action="store_true", help="reload even if unchanged")
    ap.add_argument("--dry-run", action="store_true", help="build rows, write nothing")
    ap.add_argument("--embedder", choices=list(config.REMOTE_EMBEDDERS),
                    help="load this model's vectors into law_alt_chunks (migration "
                         "003) instead of the corpus")
    args = ap.parse_args()

    db = sqlite3.connect(config.CATALOG)
    chunks = json.loads((config.INDEX_DIR / "chunks.json").read_text(encoding="utf-8"))
    emb = np.load(config.INDEX_DIR / "embeddings.npy")
    if len(chunks) != len(emb):
        raise SystemExit(f"chunks.json has {len(chunks)} rows but embeddings.npy "
                         f"has {len(emb)} — the index is out of step; rebuild it")

    all_slugs = [r[0] for r in db.execute("select slug from acts order by slug")]

    # Checked before anything is written. A chunk whose Act name matches no
    # catalog Act would never be loaded, and search would silently lose it.
    names = {r[0].strip().lower() for r in db.execute("select name from acts")}
    orphans = [c["id"] for c in chunks if c["metadata"]["act"].strip().lower() not in names]
    if orphans:
        raise SystemExit(f"{len(orphans)} chunk(s) match no Act in the catalog, "
                         f"e.g. {orphans[:3]} — rebuild the catalog or the index")
    slugs = all_slugs if args.all or args.dry_run and not args.act else (args.act or [])
    if not slugs:
        raise SystemExit("name an act with --act, or pass --all")

    client = None
    if not args.dry_run:
        from app.db import supabase
        client = supabase.client()

    if args.embedder:
        from app.rag.store import alt_paths, texts_digest

        emb_path, meta_path = alt_paths(args.embedder)
        if not emb_path.exists():
            raise SystemExit(f"no {emb_path.name} — run build_index.py --embedder "
                             f"{args.embedder} first")
        alt = np.load(emb_path)
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if len(alt) != len(chunks) or meta.get("texts_digest") != texts_digest(chunks):
            raise SystemExit(f"{emb_path.name} does not match chunks.json — rebuild it")
        for slug in slugs:
            rows = build_alt(slug, db, args.embedder, chunks, alt)
            if args.dry_run:
                print(f"{slug}: {len(rows)} {args.embedder} vectors")
            elif not load_alt(client, slug, args.embedder, rows, args.force):
                print(f"{slug}: {args.embedder} vectors unchanged — skipped")
        return

    total_chunks = 0
    for slug in slugs:
        rows = build(slug, db, chunks, emb)
        d = digest(rows)
        total_chunks += len(rows["law_chunks"])
        payload = sum(len(json.dumps(v, ensure_ascii=False)) for v in rows.values())
        counts = "  ".join(f"{t.removeprefix('law_')}={len(v)}" for t, v in rows.items())
        print(f"{slug}: {counts}  (~{payload / 1e6:.1f} MB)  digest {d[:12]}")
        if args.dry_run:
            continue

        existing = (client.table("law_loads").select("digest")
                    .eq("act_slug", slug).execute().data)
        if existing and existing[0]["digest"] == d and not args.force:
            print("    unchanged — skipped")
            continue
        t0 = time.time()
        load(client, slug, rows, d)
        print(f"    loaded in {time.time() - t0:.1f}s")

    print(f"\n{'dry run — nothing written' if args.dry_run else 'done'}: "
          f"{len(slugs)} act(s), {total_chunks} chunks")


if __name__ == "__main__":
    main()
