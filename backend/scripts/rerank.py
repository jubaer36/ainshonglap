"""E4: compare retrieval with and without the cross-encoder reranker.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # app.rag
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from app.rag import config  # noqa: E402
from eval_retrieval import load_eval, score  # noqa: E402

KS = (1, 5, 20)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topk", type=int, default=config.DENSE_K,
                    help="candidate sections to rerank")
    ap.add_argument("--model", default=config.RERANK_MODEL)
    ap.add_argument("--batch", type=int, default=config.RERANK_BATCH)
    args = ap.parse_args()

    config.RERANK_MODEL = args.model       # pipeline reads it from here
    config.RERANK_BATCH = args.batch

    from app.rag.pipeline import retrieve_many
    from app.rag.reranker import load_reranker
    from app.rag.store import load_store

    index, _, meta = load_store()
    rows = load_eval(None)
    qs = [r["question"] for r in rows]
    print(f"index {index.ntotal} vectors (granularity={meta.get('granularity','section')})"
          f"   eval {len(rows)} questions   topk={args.topk}")

    # --- arm 1: dense only (the baseline being improved on)
    base = score(retrieve_many(qs, k=20, dense_k=args.topk, rerank=False), rows, KS)

    # --- arm 2: same candidates, cross-encoder reordered
    print(f"loading {args.model} ...")
    _, _, _, kind = load_reranker(args.model)
    print(f"  scoring convention: {kind}")
    t0 = time.time()
    rr = score(retrieve_many(qs, k=20, dense_k=args.topk, rerank=True, progress=True),
               rows, KS)
    elapsed = time.time() - t0

    print(f"\nreranked {len(rows)} queries x {args.topk} candidates in {elapsed:.0f}s"
          f"  ({elapsed/len(rows)*1000:.0f} ms/query)")
    hdr = f"{'':<12} {'R@1':>8} {'R@5':>8} {'R@20':>8} {'MRR':>8} {'Act%':>8}"
    print("\n" + hdr)
    print("-" * len(hdr))
    for label, m in (("dense", base), ("+ rerank", rr)):
        print(f"{label:<12} {m['R@1']:>7.1f}% {m['R@5']:>7.1f}% {m['R@20']:>7.1f}% "
              f"{m['MRR']:>8.3f} {m['act']:>7.1f}%")
    print(f"{'delta':<12} {rr['R@1']-base['R@1']:>+7.1f}  {rr['R@5']-base['R@5']:>+7.1f}  "
          f"{rr['R@20']-base['R@20']:>+7.1f}  {rr['MRR']-base['MRR']:>+8.3f} "
          f"{rr['act']-base['act']:>+7.1f}")
    # R@20 cannot improve: both arms rerank the same candidate set. Any change
    # there is a bug, not a result.
    print(f"\n(R@20 is a fixed candidate set — it should not move; delta "
          f"{rr['R@20']-base['R@20']:+.1f} is the sanity check)")

    out = config.INDEX_DIR / "rerank_results.json"
    out.write_text(json.dumps({
        "topk": args.topk, "model": args.model,
        "granularity": meta.get("granularity", "section"),
        "vectors": int(index.ntotal),
        "dense": base, "reranked": rr,
        "ms_per_query": elapsed / len(rows) * 1000,
    }, indent=2), encoding="utf-8")
    print(f"-> {out}")


if __name__ == "__main__":
    main()
