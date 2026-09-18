"""Cross-encoder reranking over the dense top-k (experiments.md E4).

Why a cross-encoder, and why this one:

  the failure it fixes   Recall@1 is 33% while Recall@20 is 68% — two thirds of
                         the time the right section IS retrieved, just ranked
                         badly. Margins are thin (0.7385 vs 0.7179 on the s.54
                         example), which is what happens when two independently
                         produced vectors are compared. A cross-encoder reads
                         query and chunk together in one pass instead.

  bge-reranker-v2-m3     Reported to win 50-67% of the time on queries under
                         300 characters but to fall off sharply past that. 93%
                         of our eval questions are under 300 (median 198), so
                         the profile matches. Qwen3-Reranker-0.6B is flat across
                         lengths and marginally stronger on MTEB — worth trying
                         if the 7% long-query tail turns out to matter.

  latency is a non-issue Cross-encoders are criticised for cost, but that is
                         about large candidate sets. We score 20 candidates per
                         query with no QPS pressure.

Ceiling: reranking can only reorder what retrieval already found, so it cannot
exceed Recall@20 (68%). The remaining 32% needs query rewriting, not this.

Run (from backend/, ainshonglap conda env active):

    python scripts/rerank.py --topk 20
    python scripts/rerank.py --topk 50 --model BAAI/bge-reranker-v2-m3
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

import numpy as np
import torch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from embed import embed_texts, load_encoder  # noqa: E402
from eval_retrieval import load_eval  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
INDEX = ROOT / "Laws" / "main" / "index"
EMBEDDER = "BAAI/bge-m3"
RERANKER = "BAAI/bge-reranker-v2-m3"


def load_reranker(name: str, device: str):
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(name)
    model = AutoModelForSequenceClassification.from_pretrained(name).to(device).eval()
    return tok, model


@torch.no_grad()
def rerank_scores(pairs, tok, model, device, batch=16, max_len=512):
    """Relevance score per (query, passage) pair — higher is better."""
    out = []
    for i in range(0, len(pairs), batch):
        b = pairs[i : i + batch]
        enc = tok(
            [p[0] for p in b],
            [p[1] for p in b],
            padding=True,
            truncation=True,
            max_length=max_len,
            return_tensors="pt",
        ).to(device)
        logits = model(**enc).logits.view(-1).float()
        out.extend(logits.cpu().tolist())
    return out


def metrics(order_per_query, rows, ks=(1, 5, 20)):
    hits = {k: 0 for k in ks}
    act_hits = 0
    rr = 0.0
    for row, got in zip(rows, order_per_query):
        ranks = [
            r
            for r, m in enumerate(got, start=1)
            if m["act"].strip().lower() == row["act"].lower()
            and str(m["section"]) == row["section"]
        ]
        if ranks:
            rr += 1.0 / ranks[0]
            for k in ks:
                if ranks[0] <= k:
                    hits[k] += 1
        if got and got[0]["act"].strip().lower() == row["act"].lower():
            act_hits += 1
    n = len(rows)
    return {
        "R@1": hits[1] / n * 100,
        "R@5": hits[5] / n * 100,
        "R@20": hits[20] / n * 100,
        "MRR": rr / n,
        "act": act_hits / n * 100,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topk", type=int, default=20, help="candidates to rerank")
    ap.add_argument("--model", default=RERANKER)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--show-gains", type=int, default=0)
    args = ap.parse_args()

    import faiss

    chunks = json.loads((INDEX / "chunks.json").read_text(encoding="utf-8"))
    index = faiss.read_index(str(INDEX / "chunks.faiss"))
    rows = load_eval(None)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"index {index.ntotal} vectors   eval {len(rows)} questions   topk={args.topk}")

    # --- stage 1: dense retrieval (the baseline being improved on)
    tok_e, mod_e = load_encoder(EMBEDDER, device)
    qemb = embed_texts([r["question"] for r in rows], tok_e, mod_e, device, batch=8)
    _, ids = index.search(qemb, max(args.topk, 20))
    del mod_e
    torch.cuda.empty_cache()

    base_order = [[chunks[j]["metadata"] for j in ids[i][:20]] for i in range(len(rows))]
    base = metrics(base_order, rows)

    # --- stage 2: cross-encoder rerank of those candidates
    print(f"loading {args.model} ...")
    tok_r, mod_r = load_reranker(args.model, device)
    t0 = time.time()
    reranked_order = []
    for i, row in enumerate(rows):
        cand = list(ids[i][: args.topk])
        pairs = [(row["question"], chunks[j]["text"]) for j in cand]
        scores = rerank_scores(pairs, tok_r, mod_r, device, batch=args.batch)
        order = [c for _, c in sorted(zip(scores, cand), key=lambda t: -t[0])]
        reranked_order.append([chunks[j]["metadata"] for j in order[:20]])
        if (i + 1) % 100 == 0:
            print(f"  {i+1}/{len(rows)}  ({time.time()-t0:.0f}s)")
    elapsed = time.time() - t0
    rr = metrics(reranked_order, rows)

    print(f"\nreranked {len(rows)} queries x {args.topk} candidates in {elapsed:.0f}s"
          f"  ({elapsed/len(rows)*1000:.0f} ms/query)")
    hdr = f"{'':<12} {'R@1':>8} {'R@5':>8} {'R@20':>8} {'MRR':>8} {'Act%':>8}"
    print("\n" + hdr)
    print("-" * len(hdr))
    print(f"{'dense':<12} {base['R@1']:>7.1f}% {base['R@5']:>7.1f}% {base['R@20']:>7.1f}% "
          f"{base['MRR']:>8.3f} {base['act']:>7.1f}%")
    print(f"{'+ rerank':<12} {rr['R@1']:>7.1f}% {rr['R@5']:>7.1f}% {rr['R@20']:>7.1f}% "
          f"{rr['MRR']:>8.3f} {rr['act']:>7.1f}%")
    print(f"{'delta':<12} {rr['R@1']-base['R@1']:>+7.1f}  {rr['R@5']-base['R@5']:>+7.1f}  "
          f"{rr['R@20']-base['R@20']:>+7.1f}  {rr['MRR']-base['MRR']:>+8.3f} "
          f"{rr['act']-base['act']:>+7.1f}")
    # R@20 cannot improve: the candidate set is fixed. Any change there is a bug.
    print(f"\n(R@20 is a fixed candidate set — it should not move; delta "
          f"{rr['R@20']-base['R@20']:+.1f} is the sanity check)")

    out = INDEX / "rerank_results.json"
    out.write_text(json.dumps({"topk": args.topk, "model": args.model,
                               "dense": base, "reranked": rr,
                               "ms_per_query": elapsed / len(rows) * 1000}, indent=2),
                   encoding="utf-8")
    print(f"-> {out}")


if __name__ == "__main__":
    main()
