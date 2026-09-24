"""Check Supabase against the local files it was loaded from: counts, text,
vectors, and identical search results.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sqlite3
import sys
from collections import Counter

import numpy as np

BACKEND = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

from app.rag import config  # noqa: E402

TOP = 60            # what pipeline.retrieve asks FAISS for: DENSE_K * OVERFETCH
TIE = 1e-5          # scores closer than this may legitimately swap places
PAGE = 500

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def fetch_all(client, table: str, columns: str, order: str) -> list[dict]:
    """Every row, paged: the API returns at most ~1000 rows a request."""
    out, start = [], 0
    while True:
        page = (client.table(table).select(columns).order(order)
                .range(start, start + PAGE - 1).execute().data)
        out.extend(page)
        if len(page) < PAGE:
            return out
        start += PAGE


def sha(text: str | None) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", type=int, default=60, help="search-parity probes")
    args = ap.parse_args()

    from app.db import supabase
    client = supabase.client()

    db = sqlite3.connect(config.CATALOG)
    db.row_factory = sqlite3.Row
    acts = {r["id"]: r["slug"] for r in db.execute("select id, slug from acts")}
    chunks = json.loads((config.INDEX_DIR / "chunks.json").read_text(encoding="utf-8"))
    emb = np.load(config.INDEX_DIR / "embeddings.npy")

    print("\nload records")
    loads = {r["act_slug"]: r for r in fetch_all(client, "law_loads", "*", "act_slug")}
    missing = sorted(set(acts.values()) - set(loads))
    check("every Act has a commit marker", not missing,
          f"missing: {missing}" if missing else f"{len(loads)} acts")

    print("\nstructure")
    remote_acts = {r["slug"]: r for r in fetch_all(client, "law_acts", "*", "slug")}
    local_acts = {r["slug"]: dict(r) for r in db.execute("select * from acts")}
    check("acts", remote_acts.keys() == local_acts.keys() and all(
        remote_acts[s]["name"] == local_acts[s]["name"] for s in local_acts),
        f"{len(remote_acts)} remote / {len(local_acts)} local")

    # Chapters, joined on (act, position).
    local_ch = {(acts[r["act_id"]], r["seq"]): (r["number"], r["title"], r["n_sections"])
                for r in db.execute("select * from chapters")}
    remote_ch = {(r["act_slug"], r["seq"]): (r["number"], r["title"], r["n_sections"])
                 for r in fetch_all(client, "law_chapters",
                                    "act_slug,seq,number,title,n_sections", "id")}
    check("chapters", local_ch == remote_ch,
          f"{len(remote_ch)} remote / {len(local_ch)} local"
          + ("" if local_ch == remote_ch else
             f", {len(set(local_ch.items()) ^ set(remote_ch.items()))} differ"))

    # Sections, joined on the citation key, text compared by hash byte for byte.
    cols = ("key", "number", "num_int", "num_suffix", "title", "page_start",
            "page_end", "seq", "dup_seq", "continuation")
    local_sec = {r["key"]: (tuple(r[c] if c != "continuation" else bool(r[c])
                                  for c in cols), sha(r["text"]))
                 for r in db.execute("select * from sections")}
    remote_sec = {r["key"]: (tuple(r[c] for c in cols), sha(r["text"]))
                  for r in fetch_all(client, "law_sections", ",".join(cols) + ",text", "key")}
    text_bad = [k for k in local_sec if k in remote_sec
                and local_sec[k][1] != remote_sec[k][1]]
    field_bad = [k for k in local_sec if k in remote_sec
                 and local_sec[k][0] != remote_sec[k][0]]
    check("sections present", local_sec.keys() == remote_sec.keys(),
          f"{len(remote_sec)} remote / {len(local_sec)} local")
    check("section text byte-identical", not text_bad,
          f"{len(text_bad)} differ, e.g. {text_bad[:3]}" if text_bad
          else f"{len(local_sec)} sections")
    check("section fields identical", not field_bad,
          f"{len(field_bad)} differ, e.g. {field_bad[:3]}" if field_bad else "")

    # Subsections, joined on (section key, position).
    key_of = {r["id"]: r["key"] for r in db.execute("select id, key from sections")}
    local_ss = {(key_of[r["section_id"]], r["seq"]): (r["type"], r["number"], sha(r["text"]))
                for r in db.execute("select * from subsections")}
    remote_ss = {(r["section_key"], r["seq"]): (r["type"], r["number"], sha(r["text"]))
                 for r in fetch_all(client, "law_subsections",
                                    "section_key,seq,type,number,text", "id")}
    check("subsections", local_ss == remote_ss,
          f"{len(remote_ss)} remote / {len(local_ss)} local")

    # Cross-references: compared as a multiset per section, since their ids are
    # the loader's own numbering and the verifier should not depend on it.
    local_x = Counter((key_of[r["section_id"]], acts.get(r["target_act_id"]),
                       r["target_number"], r["kind"], r["target_to"], r["raw"])
                      for r in db.execute("select * from cross_references"))
    remote_x = Counter((r["section_key"], r["target_act_slug"], r["target_number"],
                        r["kind"], r["target_to"], r["raw"])
                       for r in fetch_all(client, "law_cross_references",
                                          "section_key,target_act_slug,target_number,"
                                          "kind,target_to,raw", "id"))
    check("cross-references", local_x == remote_x,
          f"{sum(remote_x.values())} remote / {sum(local_x.values())} local")

    print("\nvectors")
    remote_ck = {r["vec_id"]: r for r in fetch_all(
        client, "law_chunks", "vec_id,chunk_id,text,metadata,embedding", "vec_id")}
    check("every vector present", set(remote_ck) == set(range(len(chunks))),
          f"{len(remote_ck)} remote / {len(chunks)} local")
    text_bad, meta_bad, vec_bad = [], [], []
    for i, c in enumerate(chunks):
        r = remote_ck.get(i)
        if r is None:
            continue
        if r["text"] != c["text"] or r["chunk_id"] != c["id"]:
            text_bad.append(i)
        if r["metadata"] != c["metadata"]:
            meta_bad.append(i)
        v = np.array(json.loads(r["embedding"]), dtype=np.float32)
        if not np.array_equal(v, emb[i]):
            vec_bad.append(i)
    check("chunk text identical", not text_bad, f"{len(text_bad)} differ" if text_bad else "")
    check("chunk metadata identical", not meta_bad, f"{len(meta_bad)} differ" if meta_bad else "")
    check("embeddings bit-identical", not vec_bad,
          f"{len(vec_bad)} differ, e.g. vec {vec_bad[:3]}" if vec_bad
          else f"{len(emb)} x {emb.shape[1]}")

    print(f"\nsearch parity (top {TOP}, {args.queries} probes)")
    import faiss

    index = faiss.read_index(str(config.INDEX_DIR / "chunks.faiss"))
    rng = np.random.default_rng(0)
    # Half the probes are stored vectors (a known best match, then 59 real
    # neighbours); half are blends of two, so the top result is not simply the
    # probe finding itself.
    probes = []
    for i in rng.choice(len(emb), args.queries // 2, replace=False):
        probes.append(emb[i])
    for _ in range(args.queries - len(probes)):
        a, b = rng.choice(len(emb), 2, replace=False)
        v = emb[a] + emb[b]
        probes.append((v / np.linalg.norm(v)).astype(np.float32))

    exact, tie_only, broken, worst = 0, 0, 0, 0.0
    for q in probes:
        f_scores, f_ids = index.search(q.reshape(1, -1), TOP)
        f_ids, f_scores = list(f_ids[0]), list(f_scores[0])
        lit = "[" + ",".join(np.format_float_positional(x, unique=True, trim="-")
                             for x in q) + "]"
        rows = client.rpc("law_match_chunks",
                          {"query_embedding": lit, "match_count": TOP}).execute().data
        p_ids = [r["vec_id"] for r in rows]
        p_scores = {r["vec_id"]: r["score"] for r in rows}
        f_by = dict(zip(f_ids, f_scores))
        shared = set(f_ids) & set(p_ids)
        worst = max([worst] + [abs(f_by[i] - p_scores[i]) for i in shared])
        if p_ids == f_ids:
            exact += 1
            continue
        # Differences are acceptable only where the scores are equal to within
        # float noise: then the order between them is arbitrary in both stores.
        # The comparison is on the score sequence, and a set difference is
        # tolerated only at the cut-off, where a tie can push one in or out.
        seq_ok = len(p_ids) == len(f_ids) and all(
            abs(a - b) <= TIE for a, b in zip(f_scores, [p_scores[i] for i in p_ids]))
        if seq_ok:
            tie_only += 1
        else:
            broken += 1
    check("search results identical", broken == 0,
          f"{exact} exact, {tie_only} differ only among tied scores, "
          f"{broken} genuinely different; max score gap {worst:.2e}")

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
