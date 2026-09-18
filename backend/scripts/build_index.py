"""Embed the chunks with BGE-M3 and build a FAISS index (retrieval-plan.md).

BGE-M3 because: its 8192-token window takes our 39 oversized chunks without
truncation, it runs locally so ingestion has no API dependency, and it is one
of the two models the statute-retrieval literature actually evaluated. A paid
legal-tuned model is an upgrade to be measured against this, not assumed.

Dense-only, no BM25. For statutory corpora the evidence is that hybrid does not
reliably help and can slightly hurt — so it is a hypothesis to test against our
own eval set, not a default.

Index is inner-product over L2-normalised vectors, i.e. cosine similarity. Flat
(exhaustive) rather than approximate: at ~10k chunks an ANN index would trade
recall for a speedup we do not need.

Run (from backend/, ainshonglap conda env active):

    python scripts/build_index.py              # all acts into one index
    python scripts/build_index.py --batch 8    # smaller batches if VRAM is tight
"""

from __future__ import annotations

import argparse
import json
import pathlib
import time

import numpy as np

LAWS = pathlib.Path(__file__).resolve().parents[2] / "Laws" / "main"
CHUNKS = LAWS / "chunks"
INDEX = LAWS / "index"
MODEL = "BAAI/bge-m3"
ACTS = ["crpc", "penal", "evidence"]


def load_chunks() -> list[dict]:
    out: list[dict] = []
    for a in ACTS:
        p = CHUNKS / f"{a}.json"
        if p.exists():
            out.extend(json.loads(p.read_text(encoding="utf-8")))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--device", default=None)
    args = ap.parse_args()

    import faiss
    import torch

    INDEX.mkdir(parents=True, exist_ok=True)
    chunks = load_chunks()
    print(f"chunks: {len(chunks)}")

    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    print(f"loading {MODEL} on {device} ...")
    t0 = time.time()
    # transformers directly rather than sentence-transformers: 6.x tries to load
    # an AutoProcessor for this repo and fails. BGE-M3's dense vector is simply
    # the CLS token, L2-normalised, so the wrapper buys nothing here.
    from embed import embed_texts, load_encoder
    tok, model = load_encoder(MODEL, device)
    print(f"  loaded in {time.time()-t0:.0f}s")

    texts = [c["text"] for c in chunks]
    t0 = time.time()
    emb = embed_texts(texts, tok, model, device, batch=args.batch, progress=True)
    print(f"  embedded {len(texts)} chunks in {time.time()-t0:.0f}s  dim={emb.shape[1]}")

    index = faiss.IndexFlatIP(emb.shape[1])
    index.add(emb)
    faiss.write_index(index, str(INDEX / "chunks.faiss"))
    np.save(INDEX / "embeddings.npy", emb)
    (INDEX / "chunks.json").write_text(
        json.dumps(chunks, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (INDEX / "meta.json").write_text(
        json.dumps(
            {"model": MODEL, "dim": int(emb.shape[1]), "count": len(chunks),
             "normalised": True, "metric": "inner_product"},
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"  -> {INDEX}  ({index.ntotal} vectors)")


if __name__ == "__main__":
    main()
