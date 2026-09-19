"""Measure retrieval against gold sections (retrieval-plan.md step 3).

The point of this file is to make every later choice — reranking, hybrid, a
paid embedder — an argued decision rather than a guess. Nothing gets adopted
before it moves a number here.

Gold standard: `Laws/bangladesh-legal-qa-dataset`, which ships QA pairs
carrying the Act and section each answer comes from. It was scraped
independently of our PDFs, so it is a genuine external judge rather than
something derived from the thing being tested.

Reported:
  Recall@k     did the gold section appear in the top k
  MRR          1/rank of the first correct hit — rewards ranking it first
  Act accuracy did the top hit come from the right Act at all. This isolates
               Document-Level Retrieval Mismatch, the failure the hierarchy
               prefix exists to prevent, and it is the one that matters most:
               citing the right section number of the wrong statute is worse
               than returning nothing.

It measures `app.rag.pipeline`, the same code the API serves. Earlier this file
had its own inline retrieval loop, which is how the logged numbers came to
describe a pipeline that could not actually be served.

Run (from backend/, ainshonglap conda env active):

    python scripts/eval_retrieval.py                  # dense + rerank (as served)
    python scripts/eval_retrieval.py --no-rerank      # dense only
    python scripts/eval_retrieval.py --split single_hop --k 1 3 5 10
    python scripts/eval_retrieval.py --show-failures 10
"""

from __future__ import annotations

import argparse
import glob
import json
import pathlib
import re
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.rag import config  # noqa: E402

OUR_ACTS = {
    "the code of criminal procedure, 1898",
    "the penal code, 1860",
    "the evidence act, 1872",
}


def load_eval(split: str | None) -> list[dict]:
    rows = []
    for f in sorted(glob.glob(str(config.QA_DIR / "*english*.json"))):
        name = pathlib.Path(f).stem
        if split and split not in name:
            continue
        for r in json.loads(pathlib.Path(f).read_text(encoding="utf-8")):
            act = (r.get("Act") or "").strip()
            secnum = str(r.get("Section Number") or "")
            m = re.search(r"(\d{1,3}[A-Z]{0,2})", secnum)
            q = (r.get("Question") or "").strip()
            if act.lower() in OUR_ACTS and m and q:
                rows.append(
                    {"question": q, "act": act, "section": m.group(1), "split": name}
                )
    return rows


def score(results, rows, ks) -> dict:
    """Recall@k / MRR / Act accuracy for one ranked list per question."""
    hits = {k: 0 for k in ks}
    act_hits = 0
    rr = 0.0
    for row, got in zip(rows, results):
        ranks = [
            r.rank
            for r in got
            if r.act.strip().lower() == row["act"].lower() and r.section == row["section"]
        ]
        if ranks:
            rr += 1.0 / ranks[0]
            for k in ks:
                if ranks[0] <= k:
                    hits[k] += 1
        if got and got[0].act.strip().lower() == row["act"].lower():
            act_hits += 1
    n = len(rows)
    out = {f"R@{k}": hits[k] / n * 100 for k in ks}
    out["MRR"] = rr / n
    out["act"] = act_hits / n * 100
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default=None, help="substring of the split filename")
    ap.add_argument("--k", type=int, nargs="+", default=[1, 3, 5, 10, 20])
    ap.add_argument("--show-failures", type=int, default=0)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--no-rerank", action="store_true",
                    help="dense only — the pre-E4 baseline")
    args = ap.parse_args()

    from app.rag.pipeline import retrieve_many
    from app.rag.store import load_store

    index, _, meta = load_store()
    rows = load_eval(args.split)
    kmax = max(args.k)
    print(f"index: {index.ntotal} vectors ({meta['model']}, "
          f"granularity={meta.get('granularity','section')})   "
          f"eval: {len(rows)} questions   rerank={'off' if args.no_rerank else 'on'}")

    t0 = time.time()
    results = retrieve_many([r["question"] for r in rows], k=kmax,
                            dense_k=max(kmax, config.DENSE_K),
                            rerank=not args.no_rerank, batch=args.batch,
                            progress=True)
    elapsed = time.time() - t0
    print(f"  retrieved in {elapsed:.0f}s  ({elapsed/len(rows)*1000:.0f} ms/query)")

    s = score(results, rows, args.k)
    print(f"\n{'metric':<14} {'value':>8}")
    for k in args.k:
        print(f"Recall@{k:<8} {s[f'R@{k}']:>7.1f}%")
    print(f"{'MRR':<14} {s['MRR']:>8.3f}")
    print(f"{'Act accuracy':<14} {s['act']:>7.1f}%   (top-1 from the right Act)")

    shown = 0
    for row, got in zip(rows, results):
        if shown >= args.show_failures:
            break
        if any(r.act.strip().lower() == row["act"].lower() and r.section == row["section"]
               for r in got):
            continue
        if shown == 0:
            print("\n--- misses ---")
        shown += 1
        print(f"\n  Q: {row['question'][:96]}")
        print(f"     gold: {row['act']} s.{row['section']}")
        if got:
            print(f"     top1: {got[0].act} s.{got[0].section} — "
                  f"{str(got[0].section_title)[:52]}")


if __name__ == "__main__":
    main()
