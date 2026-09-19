"""A/B the assumptions baked into chunking, before building on top of them.

Two decisions were made on published results from other people's corpora and
have never been measured here:

  the hierarchy prefix   40 tokens on every chunk (~25% of a 172-token median),
                         adopted because Contextual Retrieval reports 49% fewer
                         retrieval failures. Untested on this data.
  the 512-token budget   picked as a round number, not from evidence.

Both are cheap to settle: re-embed variants of the same corpus and score each
against the same 608 gold questions. If the prefix is neutral or harmful, every
later experiment sits on a bad base — which is worth knowing before adding a
reranker on top of it.

Run (from backend/, ainshonglap conda env active):

    python scripts/experiments/experiment_retrieval.py --what prefix
    python scripts/experiments/experiment_retrieval.py --what budget
    python scripts/experiments/experiment_retrieval.py --what all
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

import numpy as np

BACKEND = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))                  # app.rag
sys.path.insert(0, str(BACKEND / "scripts"))      # chunk_sections, eval_retrieval
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from chunk_sections import chunk_act, hierarchy_path, ntok  # noqa: E402
from app.rag.encoder import embed_texts, load_encoder  # noqa: E402
from eval_retrieval import load_eval  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[3]
PARSED = ROOT / "Laws" / "main" / "parsed"
MODEL = "BAAI/bge-m3"
ACTS = ["crpc", "penal", "evidence"]


# --- prefix variants -------------------------------------------------------
def prefix_none(act_name: str, sec: dict, sub: str | None) -> str:
    return ""


def prefix_full(act_name: str, sec: dict, sub: str | None) -> str:
    p = hierarchy_path(act_name, sec)
    return p if sub is None else f"{p} > Subsection ({sub})"


def prefix_act_section(act_name: str, sec: dict, sub: str | None) -> str:
    t = f"{act_name}, Section {sec['section_number']}"
    if sec.get("section_title"):
        t += f": {sec['section_title']}"
    return t


def prefix_title_only(act_name: str, sec: dict, sub: str | None) -> str:
    t = f"Section {sec['section_number']}"
    if sec.get("section_title"):
        t += f": {sec['section_title']}"
    return t


def prefix_act_only(act_name: str, sec: dict, sub: str | None) -> str:
    return act_name


PREFIXES = {
    "none": prefix_none,
    "act_only": prefix_act_only,
    "title_only": prefix_title_only,
    "act_section": prefix_act_section,
    "full": prefix_full,
}


def build_corpus(prefix_fn, budget: int) -> list[dict]:
    """Re-chunk at `budget`, then re-prefix. Returns chunk dicts."""
    out = []
    for key in ACTS:
        res = chunk_act(key, budget)
        doc = json.loads((PARSED / f"{key}.json").read_text(encoding="utf-8"))
        act_name = doc["act"]["name"]
        secs = {}
        for c in doc["chapters"]:
            for s in c["sections"]:
                secs.setdefault(s["section_number"], s)
        for ch in res["chunks"]:
            m = ch["metadata"]
            sec = secs.get(m["section"])
            if sec is None:
                continue
            body = ch["text"].split("\n\n", 1)[1] if "\n\n" in ch["text"] else ch["text"]
            pre = prefix_fn(act_name, sec, m.get("subsection"))
            out.append({"text": f"{pre}\n\n{body}" if pre else body, "metadata": m})
    return out


def score(corpus: list[dict], rows: list[dict], tok, model, device, batch: int) -> dict:
    import faiss

    emb = embed_texts([c["text"] for c in corpus], tok, model, device, batch=batch)
    index = faiss.IndexFlatIP(emb.shape[1])
    index.add(emb)
    qemb = embed_texts([r["question"] for r in rows], tok, model, device, batch=batch)
    _, ids = index.search(qemb, 20)

    ks = [1, 5, 20]
    hits = {k: 0 for k in ks}
    act_hits = 0
    rr = 0.0
    for i, row in enumerate(rows):
        got = [corpus[j]["metadata"] for j in ids[i]]
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
        "chunks": len(corpus),
        "median_tok": int(np.median([ntok(c["text"]) for c in corpus])),
        "R@1": hits[1] / n * 100,
        "R@5": hits[5] / n * 100,
        "R@20": hits[20] / n * 100,
        "MRR": rr / n,
        "act": act_hits / n * 100,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--what", choices=["prefix", "budget", "all"], default="prefix")
    ap.add_argument("--batch", type=int, default=8)
    args = ap.parse_args()

    import torch

    device = "cuda" if torch.cuda.is_available() else "cpu"
    tok, model = load_encoder(MODEL, device)
    rows = load_eval(None)
    print(f"eval: {len(rows)} questions   encoder: {MODEL} on {device}\n")

    runs: list[tuple[str, object, int]] = []
    if args.what in ("prefix", "all"):
        for name, fn in PREFIXES.items():
            runs.append((f"prefix={name}", fn, 512))
    if args.what in ("budget", "all"):
        for b in (256, 512, 1024):
            runs.append((f"budget={b}", prefix_full, b))

    results = []
    hdr = f"{'variant':<20} {'chunks':>7} {'med tok':>8} {'R@1':>7} {'R@5':>7} {'R@20':>7} {'MRR':>7} {'Act%':>7}"
    print(hdr)
    print("-" * len(hdr))
    for label, fn, budget in runs:
        t0 = time.time()
        corpus = build_corpus(fn, budget)
        s = score(corpus, rows, tok, model, device, args.batch)
        s["variant"] = label
        results.append(s)
        print(f"{label:<20} {s['chunks']:>7} {s['median_tok']:>8} {s['R@1']:>6.1f}% "
              f"{s['R@5']:>6.1f}% {s['R@20']:>6.1f}% {s['MRR']:>7.3f} {s['act']:>6.1f}%"
              f"   ({time.time()-t0:.0f}s)")

    out = ROOT / "Laws" / "main" / "index" / "experiments.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\n-> {out}")


if __name__ == "__main__":
    main()
