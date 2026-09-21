"""E9 — does rewriting the query into statutory register improve retrieval?

The target is the biggest remaining failure: 28.6% of gold sections are never
retrieved at all, which no reranker can reach because reranking only reorders
what was already found. The hypothesis is a register mismatch — questions are
narrative, statutes are drafted — so rewriting the query before embedding should
recover some of that bucket.

Three arms over the same questions:

  baseline   the question as asked
  refined    the LLM's statutory restatement alone
  combined   original + restatement + terms (what app/rag/refine.py serves)

`combined` is the shipping default on the theory that a rewrite can only add
recall if the original is kept — a rewrite that drops a distinguishing detail
would otherwise lose a section the plain question would have found. That is a
theory, which is why `refined` is measured alongside it rather than assumed
worse.

Cost. One LLM call per question, reused across all three arms (the rewrite is
computed once). Retrieval is local and free. Subsample with --n; the full 608
would be 608 calls.

Read the result against +/-3.7pp at n=608, and much wider on a subsample — the
script prints the interval for the n actually used. E2 and E3 both imported a
technique from published work and found nothing here; this is the third such
import and gets the same standard of proof.

Run (from backend/, ainshonglap conda env active):

    python scripts/experiments/experiment_refine.py --n 60
    python scripts/experiments/experiment_refine.py --n 60 --model lfm
"""

from __future__ import annotations

import argparse
import json
import pathlib
import random
import sys
import time

BACKEND = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))            # app.rag
sys.path.insert(0, str(BACKEND / "scripts"))  # eval_retrieval, chunk_sections
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from app.rag import config  # noqa: E402
from eval_retrieval import load_eval, score  # noqa: E402

SEED = 20260919


def ci95(p: float, n: int) -> float:
    """Half-width in percentage points, for reading the arms against noise."""
    return 1.96 * ((p * (1 - p) / n) ** 0.5) * 100


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=60, help="questions (= LLM calls)")
    ap.add_argument("--k", type=int, nargs="+", default=[1, 5, 20])
    ap.add_argument("--model", default="qwen", help="alias or full id")
    ap.add_argument("--sleep", type=float, default=1.0)
    ap.add_argument("--resume", action="store_true",
                    help="reuse rewrites already cached for this model")
    args = ap.parse_args()

    from app.rag.pipeline import retrieve_many
    from app.rag.refine import refine

    _, model = config.resolve_generator(args.model)
    rows = load_eval(None)
    rng = random.Random(SEED)
    sample = rng.sample(rows, min(args.n, len(rows)))

    # Rewrites are cached per model: they are the expensive half, and an
    # interrupted run should not re-spend them.
    slug = model.replace("/", "_").replace(":", "_")
    cache_p = config.INDEX_DIR / f"refine_cache_{slug}.json"
    cache = (json.loads(cache_p.read_text(encoding="utf-8"))
             if cache_p.exists() and args.resume else {})
    print(f"model {model}   n={sample and len(sample)}   cached {len(cache)}")

    t0 = time.time()
    for i, row in enumerate(sample, start=1):
        q = row["question"]
        if q in cache:
            continue
        r = refine(q, model=model)
        cache[q] = {"intent": r.intent, "refined": r.refined, "terms": r.terms,
                    "failed": r.failed, "query": r.query}
        if i % 10 == 0 or r.failed:
            print(f"  {i}/{len(sample)}  ({time.time()-t0:.0f}s)"
                  + ("  [refiner failed]" if r.failed else ""))
        cache_p.write_text(json.dumps(cache, indent=2, ensure_ascii=False),
                           encoding="utf-8")
        time.sleep(args.sleep)

    usable = [r for r in sample if r["question"] in cache
              and not cache[r["question"]]["failed"]]
    if not usable:
        raise SystemExit("no successful rewrites — nothing to measure")
    intents = {}
    for r in usable:
        intents[cache[r["question"]]["intent"]] = \
            intents.get(cache[r["question"]]["intent"], 0) + 1
    print(f"\nusable {len(usable)}/{len(sample)}   intents {intents}")
    # Every gold question has a gold section, so anything not classified
    # 'question' is a refiner error on this set, and would cost a real answer.
    misroute = len(usable) - intents.get("question", 0)
    if misroute:
        print(f"  ! {misroute} answerable questions misrouted as browse/out_of_scope")

    arms = {
        "baseline":  [r["question"] for r in usable],
        "refined":   [cache[r["question"]]["refined"] or r["question"] for r in usable],
        "combined":  [cache[r["question"]]["query"] for r in usable],
    }

    kmax = max(args.k)
    hdr = f"{'arm':<10}" + "".join(f"{'R@'+str(k):>8}" for k in args.k) + \
          f"{'MRR':>8}{'Act%':>8}"
    print("\n" + hdr)
    print("-" * len(hdr))
    out = {}
    for name, queries in arms.items():
        res = retrieve_many(queries, k=kmax, rerank=True)
        s = score(res, usable, args.k)
        out[name] = s
        print(f"{name:<10}" + "".join(f"{s[f'R@{k}']:>7.1f}%" for k in args.k)
              + f"{s['MRR']:>8.3f}{s['act']:>7.1f}%")

    base = out["baseline"]
    print("\ndeltas vs baseline (pp):")
    for name in ("refined", "combined"):
        d = "  ".join(f"R@{k} {out[name][f'R@{k}']-base[f'R@{k}']:+.1f}"
                      for k in args.k)
        print(f"  {name:<10} {d}   Act {out[name]['act']-base['act']:+.1f}")
    half = ci95(base["R@1"] / 100, len(usable))
    print(f"\n95% CI at n={len(usable)} is about +/-{half:.1f}pp on R@1. "
          f"Anything smaller is not a result.")

    dest = config.INDEX_DIR / "refine_results.json"
    dest.write_text(json.dumps({"model": model, "n": len(usable),
                                "intents": intents, "ci95_pp": half,
                                "arms": out}, indent=2), encoding="utf-8")
    print(f"-> {dest}")


if __name__ == "__main__":
    main()
