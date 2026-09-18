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

Run (from backend/, ainshonglap conda env active):

    python scripts/eval_retrieval.py
    python scripts/eval_retrieval.py --split single_hop --k 1 3 5 10
    python scripts/eval_retrieval.py --show-failures 10
"""

from __future__ import annotations

import argparse
import glob
import json
import pathlib
import re
import time

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
LAWS = ROOT / "Laws" / "main"
INDEX = LAWS / "index"
QA = ROOT / "Laws" / "bangladesh-legal-qa-dataset" / "qa-splits"
MODEL = "BAAI/bge-m3"

OUR_ACTS = {
    "the code of criminal procedure, 1898",
    "the penal code, 1860",
    "the evidence act, 1872",
}


def load_eval(split: str | None) -> list[dict]:
    rows = []
    for f in sorted(glob.glob(str(QA / "*english*.json"))):
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default=None, help="substring of the split filename")
    ap.add_argument("--k", type=int, nargs="+", default=[1, 3, 5, 10, 20])
    ap.add_argument("--show-failures", type=int, default=0)
    ap.add_argument("--batch", type=int, default=16)
    args = ap.parse_args()

    import faiss
    import torch

    from embed import embed_texts, load_encoder

    chunks = json.loads((INDEX / "chunks.json").read_text(encoding="utf-8"))
    index = faiss.read_index(str(INDEX / "chunks.faiss"))
    meta = json.loads((INDEX / "meta.json").read_text(encoding="utf-8"))
    rows = load_eval(args.split)
    print(f"index: {index.ntotal} vectors ({meta['model']})   eval: {len(rows)} questions")

    # Same encoder as build_index.py — query and document vectors must come
    # from the same model and the same pooling, or the inner product is noise.
    device = "cuda" if torch.cuda.is_available() else "cpu"
    tok, model = load_encoder(MODEL, device)
    t0 = time.time()
    qemb = embed_texts([r["question"] for r in rows], tok, model, device, batch=args.batch)
    print(f"  queries embedded in {time.time()-t0:.0f}s")

    kmax = max(args.k)
    _, ids = index.search(qemb, kmax)

    hits = {k: 0 for k in args.k}
    act_hits = 0
    rr = 0.0
    failures = []
    for i, row in enumerate(rows):
        got = [chunks[j]["metadata"] for j in ids[i]]
        # a hit = same Act AND same section number
        ranks = [
            r
            for r, m in enumerate(got, start=1)
            if m["act"].strip().lower() == row["act"].lower()
            and str(m["section"]) == row["section"]
        ]
        first = ranks[0] if ranks else None
        if first:
            rr += 1.0 / first
            for k in args.k:
                if first <= k:
                    hits[k] += 1
        if got and got[0]["act"].strip().lower() == row["act"].lower():
            act_hits += 1
        if not first and len(failures) < args.show_failures:
            failures.append((row, got[0] if got else None))

    n = len(rows)
    print(f"\n{'metric':<14} {'value':>8}")
    for k in args.k:
        print(f"Recall@{k:<8} {hits[k]/n*100:>7.1f}%")
    print(f"{'MRR':<14} {rr/n:>8.3f}")
    print(f"{'Act accuracy':<14} {act_hits/n*100:>7.1f}%   (top-1 from the right Act)")

    if failures:
        print(f"\n--- {len(failures)} misses ---")
        for row, top in failures:
            print(f"\n  Q: {row['question'][:96]}")
            print(f"     gold: {row['act']} s.{row['section']}")
            if top:
                print(f"     top1: {top['act']} s.{top['section']} — {str(top['section_title'])[:52]}")


if __name__ == "__main__":
    main()
