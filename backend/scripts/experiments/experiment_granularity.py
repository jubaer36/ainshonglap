"""E5: does embedding subsections as well as sections help retrieval?
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

import numpy as np

BACKEND = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))                 # app.rag
sys.path.insert(0, str(BACKEND / "scripts"))     # chunk_sections
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from chunk_sections import hierarchy_path, ntok  # noqa: E402
from app.rag.encoder import embed_texts, load_encoder  # noqa: E402
from eval_retrieval import load_eval  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[3]
PARSED = ROOT / "Laws" / "main" / "parsed"
EMBEDDER = "BAAI/bge-m3"
ACTS = ["crpc", "penal", "evidence"]
MIN_CHILD_TOK = 25   # below this a child is too thin to embed usefully


def build(granularity: str) -> list[dict]:
    """Chunks at the requested granularity. Metadata always names the PARENT
    section, so scoring is identical across variants."""
    out: list[dict] = []
    for key in ACTS:
        doc = json.loads((PARSED / f"{key}.json").read_text(encoding="utf-8"))
        act = doc["act"]["name"]
        for ch in doc["chapters"]:
            for sec in ch["sections"]:
                meta = {
                    "act": act,
                    "section": sec["section_number"],
                    "section_title": sec.get("section_title"),
                    "chapter": sec.get("chapter"),
                    "page_start": sec["page_start"],
                }
                path = hierarchy_path(act, sec)
                if granularity in ("section", "both"):
                    out.append({"text": f"{path}\n\n{sec['text']}", "metadata": meta})
                if granularity in ("subsection", "both"):
                    kids = [
                        c
                        for c in (sec.get("children") or [])
                        if c["type"] in ("subsection", "clause")
                        and ntok(c["text"]) >= MIN_CHILD_TOK
                    ]
                    for c in kids:
                        label = f"{path} > ({c['number']})"
                        out.append({"text": f"{label}\n\n{c['text']}", "metadata": meta})
                    # a section with no usable children still needs a vector,
                    # or it becomes unretrievable in the subsection-only variant
                    if granularity == "subsection" and not kids:
                        out.append({"text": f"{path}\n\n{sec['text']}", "metadata": meta})
    return out


def score(corpus, rows, tok, model, device, batch, reranker=None):
    import faiss

    emb = embed_texts([c["text"] for c in corpus], tok, model, device, batch=batch)
    index = faiss.IndexFlatIP(emb.shape[1])
    index.add(emb)
    qemb = embed_texts([r["question"] for r in rows], tok, model, device, batch=batch)
    # over-fetch: with several vectors per section, the top-20 vectors can
    # collapse to far fewer distinct sections
    _, ids = index.search(qemb, 60)

    ks = (1, 5, 20)
    hits = {k: 0 for k in ks}
    act_hits = 0
    rr = 0.0
    for i, row in enumerate(rows):
        seen, ordered = set(), []
        for j in ids[i]:
            m = corpus[j]["metadata"]
            key = (m["act"], m["section"])
            if key in seen:            # dedupe to distinct sections
                continue
            seen.add(key)
            ordered.append(m)
            if len(ordered) >= 20:
                break
        ranks = [
            r
            for r, m in enumerate(ordered, start=1)
            if m["act"].strip().lower() == row["act"].lower()
            and str(m["section"]) == row["section"]
        ]
        if ranks:
            rr += 1.0 / ranks[0]
            for k in ks:
                if ranks[0] <= k:
                    hits[k] += 1
        if ordered and ordered[0]["act"].strip().lower() == row["act"].lower():
            act_hits += 1
    n = len(rows)
    return {"vectors": len(corpus), "R@1": hits[1] / n * 100, "R@5": hits[5] / n * 100,
            "R@20": hits[20] / n * 100, "MRR": rr / n, "act": act_hits / n * 100}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=8)
    args = ap.parse_args()

    import torch

    device = "cuda" if torch.cuda.is_available() else "cpu"
    tok, model = load_encoder(EMBEDDER, device)
    rows = load_eval(None)
    print(f"eval: {len(rows)} questions   encoder: {EMBEDDER} on {device}\n")

    hdr = f"{'granularity':<14} {'vectors':>8} {'R@1':>7} {'R@5':>7} {'R@20':>7} {'MRR':>7} {'Act%':>7}"
    print(hdr)
    print("-" * len(hdr))
    results = []
    for g in ("section", "subsection", "both"):
        t0 = time.time()
        corpus = build(g)
        s = score(corpus, rows, tok, model, device, args.batch)
        s["granularity"] = g
        results.append(s)
        print(f"{g:<14} {s['vectors']:>8} {s['R@1']:>6.1f}% {s['R@5']:>6.1f}% "
              f"{s['R@20']:>6.1f}% {s['MRR']:>7.3f} {s['act']:>6.1f}%   ({time.time()-t0:.0f}s)")

    out = ROOT / "Laws" / "main" / "index" / "granularity_results.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\n-> {out}")


if __name__ == "__main__":
    main()
