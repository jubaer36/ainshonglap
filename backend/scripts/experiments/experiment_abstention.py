"""E8 — does the generator answer anyway when retrieval missed? (generation-plan.md)

The question this settles:

  Served R@5 is 59.0%, so 41% of the time the correct section is NOT among the
  five given to the generator — and what IS there is five plausible, adjacent,
  professionally drafted statutory sections. Nothing in the prompt looks broken.

  That is the *misleading context* condition. GRAB-RAG (arXiv 2608.22228) found
  prompt-based abstention collapses precisely there: models answer <=1% of the
  time when evidence is simply absent, but 13.6-74.3% when a fluent wrong
  passage is present, and 59.6% of those answers echo the wrong entity verbatim.
  That was 3.8-8B models on adversarial entity swaps; ours is a larger model on
  merely-wrong passages, so the result transfers in direction at best.

What is measured is the SHIPPING pipeline — `app.rag.answer.answer()`, prompt
and C2/C3 gates together — not an isolated prompt. The gates are part of the
abstention mechanism, so measuring the prompt alone would describe a system we
do not run. This is the same mistake E7 caught: measuring a component instead of
what is served.

Two strata, drawn from the same 608-question gold set:

  gold_absent    the correct section is NOT retrieved -> answering is the
                 failure. This is the condition under test.
  gold_present   it IS retrieved -> refusing is the failure. The control, and
                 the price of abstention: a system that refuses everything
                 scores perfectly on the first stratum alone.

Sequential by default, because the free OpenRouter tier is rate-limited and a
burst of parallel requests buys nothing here.

Run (from backend/, ainshonglap conda env active):

    python scripts/experiments/experiment_abstention.py --n 20          # 40 calls
    python scripts/experiments/experiment_abstention.py --n 20 --dry-run
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
from eval_retrieval import load_eval  # noqa: E402

SEED = 20260919


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% CI for a proportion. Printed because at n=20 the interval is ~+/-22pp,
    and a point estimate without it would invite reading noise as a result."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return (max(0.0, c - h) * 100, min(1.0, c + h) * 100)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20, help="questions per stratum")
    ap.add_argument("--k", type=int, default=5, help="sections shown to the generator")
    ap.add_argument("--backend", default=None, help="override RAG_LLM_BACKEND")
    ap.add_argument("--sleep", type=float, default=1.5, help="pause between calls")
    ap.add_argument("--dry-run", action="store_true",
                    help="stratify and report the call budget, make no API calls")
    args = ap.parse_args()

    from app.rag.answer import answer
    from app.rag.pipeline import Retrieved
    from app.rag.store import load_store

    rows = load_eval(None)
    index, _, _ = load_store()
    cache_p = config.INDEX_DIR / f"abstention_ctx_k{args.k}_{index.ntotal}.json"
    if cache_p.exists():
        ctxs = json.loads(cache_p.read_text(encoding="utf-8"))
        print(f"retrieval cache: {cache_p.name} ({len(ctxs)} questions)")
    else:
        from app.rag.pipeline import retrieve_many

        print(f"retrieving top-{args.k} for {len(rows)} questions "
              f"(~{len(rows)*0.95/60:.0f} min, cached afterwards) ...")
        res = retrieve_many([r["question"] for r in rows], k=args.k, progress=True)
        ctxs = [[{"act": h.act, "section": h.section,
                  "section_title": h.section_title, "text": h.text} for h in hits]
                for hits in res]
        cache_p.write_text(json.dumps(ctxs, ensure_ascii=False), encoding="utf-8")

    present, absent = [], []
    for row, hits in zip(rows, ctxs):
        found = any(
            h["act"].strip().lower() == row["act"].lower()
            and str(h["section"]) == row["section"]
            for h in hits
        )
        (present if found else absent).append((row, hits))
    print(f"gold present in top-{args.k}: {len(present)}   absent: {len(absent)}"
          f"   (R@{args.k} = {len(present)/len(rows)*100:.1f}%)")

    rng = random.Random(SEED)
    strata = {
        "gold_absent": rng.sample(absent, min(args.n, len(absent))),
        "gold_present": rng.sample(present, min(args.n, len(present))),
    }
    budget = sum(len(v) for v in strata.values())
    print(f"call budget: {budget} ({args.n} per stratum, sequential)")
    if args.dry_run:
        return

    out: list[dict] = []
    for sname, items in strata.items():
        print(f"\n[{sname}] {len(items)} questions")
        for i, (row, hits) in enumerate(items, start=1):
            rehydrated = [
                Retrieved(act=h["act"], section=str(h["section"]),
                          section_title=h.get("section_title"), text=h["text"],
                          score=0.0, dense_score=0.0, rank=j, metadata=h)
                for j, h in enumerate(hits, start=1)
            ]
            try:
                a = answer(row["question"], k=args.k, backend=args.backend,
                           hits=rehydrated)
                rec = {
                    "stratum": sname, "question": row["question"],
                    "gold": f"{row['act']}|{row['section']}",
                    "refused": a.refused, "reason": a.reason,
                    "ambiguous": a.ambiguous,
                    "n_claims": len(a.claims),
                    "dropped": a.dropped,
                    "cited": [c["section"] for c in a.claims],
                    "cited_gold": any(c["section"] == row["section"] for c in a.claims),
                    "validity": a.verification.rate if a.verification else None,
                }
            except Exception as e:
                print(f"  ! {str(e)[:120]}")
                rec = {"stratum": sname, "question": row["question"], "error": str(e)[:200]}
            out.append(rec)
            mark = ("REFUSED" if rec.get("refused") else
                    "error" if "error" in rec else f"answered s.{rec['cited']}")
            print(f"  {i:>2}/{len(items)}  {mark}")
            time.sleep(args.sleep)

    print("\n" + "=" * 78)
    hdr = (f"{'stratum':<14} {'n':>3} {'answered':>9} {'95% CI':>14} "
           f"{'cited gold':>11} {'gate drops':>11}")
    print(hdr)
    print("-" * len(hdr))
    summary = {}
    for sname in strata:
        rs = [r for r in out if r["stratum"] == sname and "error" not in r]
        if not rs:
            continue
        n = len(rs)
        ans = sum(not r["refused"] for r in rs)
        gold = sum(bool(r.get("cited_gold")) for r in rs)
        drops = sum(len(r.get("dropped") or []) for r in rs)
        lo, hi = wilson(ans, n)
        summary[sname] = {"n": n, "answered": ans, "answered_pct": ans / n * 100,
                          "ci95": [lo, hi], "cited_gold_pct": gold / n * 100,
                          "gate_drops": drops}
        print(f"{sname:<14} {n:>3} {ans/n*100:>8.1f}% {f'[{lo:.0f}, {hi:.0f}]':>14} "
              f"{gold/n*100:>10.1f}% {drops:>11}")

    print("\nReading it:")
    print("  gold_absent  answered  = the failure under test. Lower is better.")
    print("  gold_present answered  = utility retained. Higher is better.")
    print("  gate drops             = claims killed by C2/C3, i.e. the deterministic")
    print("                           checks catching what the prompt did not.")

    dest = config.INDEX_DIR / "abstention_results.json"
    dest.write_text(json.dumps({"k": args.k, "n": args.n, "summary": summary,
                                "rows": out}, indent=2, ensure_ascii=False),
                    encoding="utf-8")
    print(f"\n-> {dest}")


if __name__ == "__main__":
    main()
