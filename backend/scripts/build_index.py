"""Embed the chunks with BGE-M3 and build a FAISS index (retrieval-plan.md).

BGE-M3 because: its 8192-token window takes our oversized chunks without
truncation, it runs locally so ingestion has no API dependency, and it is one
of the two models the statute-retrieval literature actually evaluated. A paid
legal-tuned model is an upgrade to be measured against this, not assumed.

Dense-only, no BM25. For statutory corpora the evidence is that hybrid does not
reliably help and can slightly hurt — a hypothesis to test against our own eval
set, not a default.

Index is inner-product over L2-normalised vectors, i.e. cosine similarity. Flat
(exhaustive) rather than approximate: at this corpus size an ANN index would
trade recall for a speedup we do not need.

Run (from backend/, ainshonglap conda env active):

    python scripts/build_index.py              # all acts into one index
    python scripts/build_index.py --batch 8    # smaller batches if VRAM is tight
    python scripts/build_index.py --granularity section   # E5 off, for A/B
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from app.rag import config  # noqa: E402
from app.rag.encoder import embed_texts, load_encoder  # noqa: E402
from chunk_sections import hierarchy_path, ntok  # noqa: E402


def load_chunks(granularity: str = "both") -> list[dict]:
    """Section chunks plus subsection-level children (E5: dual granularity).

    E5 measured section-only, subsection-only and both. Indexing both lifts
    Recall@20 from 66.6% to 70.4% — and R@20 is the ceiling a reranker cannot
    raise for itself, since reranking only reorders what was already found.
    Subsection-only was worse at R@20 (65.6%): the finest unit loses the
    whole-section context some questions need.

    Children carry their PARENT section's metadata, so a child hit scores as a
    section hit and citations still point at the section.
    """
    out: list[dict] = []
    for a in config.acts():
        p = config.CHUNKS_DIR / f"{a}.json"
        if p.exists():
            out.extend(json.loads(p.read_text(encoding="utf-8")))
        if granularity == "section":
            continue
        pp = config.PARSED_DIR / f"{a}.json"
        if not pp.exists():
            continue
        doc = json.loads(pp.read_text(encoding="utf-8"))
        act = doc["act"]["name"]
        for ch in doc["chapters"]:
            for sec in ch["sections"]:
                path = hierarchy_path(act, sec)
                for c in (sec.get("children") or []):
                    if c["type"] not in ("subsection", "clause"):
                        continue
                    if ntok(c["text"]) < config.MIN_CHILD_TOKENS:
                        continue
                    out.append({
                        "id": f"{a}_s{sec['section_number']}_child{c['number']}",
                        "text": f"{path} > ({c['number']})\n\n{c['text']}",
                        "metadata": {
                            "act": act, "act_no": doc["act"]["act_no"],
                            "part": sec.get("part"), "chapter": sec.get("chapter"),
                            "chapter_title": sec.get("chapter_title"),
                            "section": sec["section_number"],
                            "section_title": sec.get("section_title"),
                            "subsection": c["number"],
                            "page_start": sec["page_start"], "page_end": sec["page_end"],
                            "source_pdf": doc["act"]["source_pdf"],
                            "tokens": ntok(c["text"]), "granularity": "child",
                        },
                    })
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--device", default=None)
    ap.add_argument("--granularity", choices=["both", "section"], default="both",
                    help="both = E5 dual granularity (default); section = E5 off")
    args = ap.parse_args()

    import faiss

    config.INDEX_DIR.mkdir(parents=True, exist_ok=True)
    chunks = load_chunks(args.granularity)
    n_child = sum(1 for c in chunks if c["metadata"].get("granularity") == "child")
    print(f"chunks: {len(chunks)}  ({len(chunks)-n_child} section, {n_child} child)")

    device = args.device or config.device()
    print(f"loading {config.EMBED_MODEL} on {device} ...")
    t0 = time.time()
    tok, model, device = load_encoder(config.EMBED_MODEL, device)
    print(f"  loaded in {time.time()-t0:.0f}s")

    texts = [c["text"] for c in chunks]
    t0 = time.time()
    emb = embed_texts(texts, tok, model, device, batch=args.batch, progress=True)
    print(f"  embedded {len(texts)} chunks in {time.time()-t0:.0f}s  dim={emb.shape[1]}")

    index = faiss.IndexFlatIP(emb.shape[1])
    index.add(emb)
    faiss.write_index(index, str(config.INDEX_DIR / "chunks.faiss"))
    np.save(config.INDEX_DIR / "embeddings.npy", emb)
    (config.INDEX_DIR / "chunks.json").write_text(
        json.dumps(chunks, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (config.INDEX_DIR / "meta.json").write_text(
        json.dumps(
            {"model": config.EMBED_MODEL, "dim": int(emb.shape[1]),
             "count": len(chunks), "sections": len(chunks) - n_child,
             "children": n_child, "granularity": args.granularity,
             "normalised": True, "metric": "inner_product"},
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"  -> {config.INDEX_DIR}  ({index.ntotal} vectors)")


if __name__ == "__main__":
    main()
