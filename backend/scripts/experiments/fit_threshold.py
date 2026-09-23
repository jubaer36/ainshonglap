"""Fit the D2 abstention threshold on the 608-question set.

The problem it solves. Asked "state some laws of the penal code", retrieval
returns five sections that share words with the query and answer nothing, and
the generator is handed them anyway. It refused — but only after a full
generation call, and only because the model happened to notice. A score gate
refuses before generating, and does not depend on the model noticing.

The signal. The reranker already separates these cases; nobody had measured
where. Observed by hand:

    "what is the punishment for theft"        top-1 rerank  +6.15
    "state some laws of the penal code"       top-1 rerank  -0.30

This script turns that anecdote into an operating point.

Method. For all 608 gold questions, record the top-1 reranker score and whether
the gold section is anywhere in the top-k. Those two columns are a binary
classifier: score above threshold -> answer, below -> refuse. Sweep the
threshold and report both error directions at every point, because only
reporting one is how a system that refuses everything looks perfect.

  kept miss    retrieval missed and we answered anyway -> the hallucination risk
  lost hit     retrieval succeeded and we refused      -> the utility cost

There is no single right threshold; there is a curve and a choice. The choice
belongs in the docs with its numbers attached, which is the point of printing
the whole sweep rather than one value.

Run (from backend/, ainshonglap conda env active; no API calls):

    python scripts/experiments/fit_threshold.py
    python scripts/experiments/fit_threshold.py --k 5 --target-precision 0.75
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

BACKEND = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))            # app.rag
sys.path.insert(0, str(BACKEND / "scripts"))  # eval_retrieval, chunk_sections
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from app.rag import config  # noqa: E402
from eval_retrieval import load_eval  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=5, help="sections the generator sees")
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--target-precision", type=float, default=0.75,
                    help="minimum share of answered questions whose gold section "
                         "was actually retrieved")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    from app.rag.pipeline import retrieve_many
    from app.rag.store import load_alt

    emb, _ = load_alt(config.EMBEDDER)
    rows = load_eval(None)
    cache = config.INDEX_DIR / f"threshold_scores_k{args.k}_{config.EMBEDDER}_{len(emb)}.json"

    if cache.exists() and not args.no_cache:
        data = json.loads(cache.read_text(encoding="utf-8"))
        print(f"cache: {cache.name} ({len(data)} rows)")
    else:
        print(f"scoring {len(rows)} questions at k={args.k} "
              f"(~{len(rows)*0.95/60:.0f} min) ...")
        t0 = time.time()
        results = retrieve_many([r["question"] for r in rows], k=args.k,
                                batch=args.batch, progress=True)
        data = []
        for row, hits in zip(rows, results):
            hit = any(h.act.strip().lower() == row["act"].lower()
                      and h.section == row["section"] for h in hits)
            data.append({
                "question": row["question"],
                "gold": f"{row['act']}|{row['section']}",
                "hit": hit,
                "top1": hits[0].score if hits else None,
                "top1_section": hits[0].key if hits else None,
            })
        cache.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                         encoding="utf-8")
        print(f"  done in {time.time()-t0:.0f}s -> {cache.name}")

    rowsd = [d for d in data if d["top1"] is not None]
    hits = [d["top1"] for d in rowsd if d["hit"]]
    miss = [d["top1"] for d in rowsd if not d["hit"]]
    n = len(rowsd)

    def q(xs, p):
        xs = sorted(xs)
        return xs[min(len(xs) - 1, int(p * len(xs)))]

    print(f"\nn={n}   retrieval hit {len(hits)} ({len(hits)/n*100:.1f}%)   "
          f"miss {len(miss)} ({len(miss)/n*100:.1f}%)")
    print(f"\n{'':<8} {'min':>7} {'p10':>7} {'p25':>7} {'median':>7} {'p75':>7} {'max':>7}")
    for label, xs in (("hit", hits), ("miss", miss)):
        print(f"{label:<8} {min(xs):>7.2f} {q(xs,.10):>7.2f} {q(xs,.25):>7.2f} "
              f"{q(xs,.50):>7.2f} {q(xs,.75):>7.2f} {max(xs):>7.2f}")

    # Sweep. `answered` = score >= t. Precision here is the share of answered
    # questions whose gold section was actually in front of the generator — an
    # upper bound on how often a confident answer can be right.
    print(f"\n{'thresh':>7} {'answered':>9} {'precision':>10} {'kept miss':>10} "
          f"{'lost hit':>9} {'refused miss':>13}")
    print("-" * 62)
    lo, hi = min(min(hits), min(miss)), max(max(hits), max(miss))
    best = None
    sweep = []
    steps = 40
    for i in range(steps + 1):
        t = lo + (hi - lo) * i / steps
        ans_hit = sum(1 for x in hits if x >= t)
        ans_miss = sum(1 for x in miss if x >= t)
        answered = ans_hit + ans_miss
        if answered == 0:
            continue
        prec = ans_hit / answered
        lost = len(hits) - ans_hit
        refused_miss = len(miss) - ans_miss
        rec = {"t": t, "answered": answered, "answered_pct": answered / n * 100,
               "precision": prec, "kept_miss": ans_miss, "lost_hit": lost,
               "refused_miss_pct": refused_miss / len(miss) * 100}
        sweep.append(rec)
        if i % 4 == 0:
            print(f"{t:>7.2f} {answered/n*100:>8.1f}% {prec*100:>9.1f}% "
                  f"{ans_miss:>10} {lost:>9} {refused_miss/len(miss)*100:>12.1f}%")
        # Cheapest threshold that reaches the target precision: refusing more
        # always raises precision, so the useful choice is the least restrictive
        # point that clears the bar.
        if prec >= args.target_precision and best is None:
            best = rec

    print()
    if best:
        print(f"At precision >= {args.target_precision:.0%}: threshold "
              f"{best['t']:.2f}")
        print(f"  answers {best['answered_pct']:.1f}% of questions, "
              f"precision {best['precision']*100:.1f}%")
        print(f"  refuses {best['refused_miss_pct']:.1f}% of the cases where "
              f"retrieval had already failed")
        print(f"  costs {best['lost_hit']} answerable questions "
              f"({best['lost_hit']/len(hits)*100:.1f}% of hits)")
    else:
        print(f"No threshold reaches precision {args.target_precision:.0%}. "
              f"The score does not separate these classes well enough to gate on.")

    out = config.INDEX_DIR / "threshold_fit.json"
    out.write_text(json.dumps({
        "k": args.k, "n": n, "hit_rate": len(hits) / n,
        "target_precision": args.target_precision,
        "chosen": best, "sweep": sweep,
    }, indent=2), encoding="utf-8")
    print(f"\n-> {out}")


if __name__ == "__main__":
    main()
