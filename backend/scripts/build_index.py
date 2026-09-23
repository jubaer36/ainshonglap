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
    python scripts/build_index.py --embedder nemotron-3-embed   # E21 vectors

`--embedder` builds another model's vectors for the SAME chunks.json
(`embeddings.<name>.npy`), for the `nemotron-embed` retrieval profile. That model
runs on OpenRouter: each call counts against the free-model daily cap (1,000
requests, shared by every :free model on the account), so only chunks without a
vector are sent — adding one Act costs roughly its chunk count / 32 requests.
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


def build(granularity: str = "both", reuse: bool = True, log=print,
          batch: int | None = None) -> dict:
    """Write chunks.json, embeddings.npy, chunks.faiss and meta.json. Returns meta.

    `reuse`: a chunk whose exact text already has a vector in the current index
    keeps that vector; only new or changed text is embedded. Adding one Act then
    embeds that Act alone instead of all ~2,300 chunks again, and removing one
    embeds nothing. The order of the output is the same as a full rebuild —
    Acts by slug, each Act's sections then its subsections — so a reused index
    and a rebuilt one line up row for row.

    Embeds through `encoder.embed`, the same cached model the API loads, so
    calling this from the running server does not load a second copy onto the
    GPU.
    """
    import faiss

    from app.rag.encoder import embed

    config.INDEX_DIR.mkdir(parents=True, exist_ok=True)
    chunks = load_chunks(granularity)
    n_child = sum(1 for c in chunks if c["metadata"].get("granularity") == "child")
    log(f"chunks: {len(chunks)}  ({len(chunks)-n_child} section, {n_child} child)")

    known: dict[str, np.ndarray] = {}
    if reuse and (config.INDEX_DIR / "embeddings.npy").exists():
        old = json.loads((config.INDEX_DIR / "chunks.json").read_text(encoding="utf-8"))
        old_emb = np.load(config.INDEX_DIR / "embeddings.npy")
        if len(old) == len(old_emb):
            known = {c["text"]: old_emb[i] for i, c in enumerate(old)}

    texts = [c["text"] for c in chunks]
    todo = [t for t in dict.fromkeys(texts) if t not in known]
    log(f"  reusing {len(texts) - len(todo)} vectors, embedding {len(todo)} new")
    t0 = time.time()
    if todo:
        fresh = embed(todo, batch=batch)
        known.update(zip(todo, fresh))
    emb = np.vstack([known[t] for t in texts]).astype("float32") if texts \
        else np.zeros((0, 1024), dtype="float32")
    log(f"  embedded in {time.time()-t0:.0f}s  dim={emb.shape[1]}")

    index = faiss.IndexFlatIP(emb.shape[1])
    index.add(emb)
    faiss.write_index(index, str(config.INDEX_DIR / "chunks.faiss"))
    np.save(config.INDEX_DIR / "embeddings.npy", emb)
    (config.INDEX_DIR / "chunks.json").write_text(
        json.dumps(chunks, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    meta = {"model": config.EMBED_MODEL, "dim": int(emb.shape[1]),
            "count": len(chunks), "sections": len(chunks) - n_child,
            "children": n_child, "granularity": granularity,
            "normalised": True, "metric": "inner_product"}
    (config.INDEX_DIR / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    log(f"  -> {config.INDEX_DIR}  ({index.ntotal} vectors)")
    return meta


def build_alt(name: str, reuse: bool = True, log=print, batch: int = 32,
              seed: pathlib.Path | None = None) -> int:
    """`embeddings.<name>.npy` for the current chunks.json. Returns API calls made.

    Run after `build` — it reads the chunks.json that wrote. A chunk whose exact
    text already has a vector (in the previous file, or in `seed`, a matching
    .npy) keeps it, so a rebuild after adding an Act embeds that Act alone.
    """
    from app.rag import remote_retrieval
    from app.rag.store import alt_paths, texts_digest

    chunks = json.loads((config.INDEX_DIR / "chunks.json").read_text(encoding="utf-8"))
    texts = [c["text"] for c in chunks]
    emb_path, meta_path = alt_paths(name)
    known: dict[str, np.ndarray] = {}
    if reuse and emb_path.exists() and meta_path.exists():
        old = json.loads(meta_path.read_text(encoding="utf-8")).get("texts", [])
        old_emb = np.load(emb_path)
        if len(old) == len(old_emb):
            known = dict(zip(old, old_emb))
    if seed is not None:
        # Vectors computed elsewhere for this same chunks.json, row for row —
        # the E21 experiment cache. Checked by count only; the texts are ours.
        seeded = np.load(seed)
        if len(seeded) != len(texts):
            raise SystemExit(f"{seed} has {len(seeded)} rows, chunks.json {len(texts)}")
        known.update({t: seeded[i] for i, t in enumerate(texts) if t not in known})

    todo = [t for t in dict.fromkeys(texts) if t not in known]
    calls = -(-len(todo) // batch)
    log(f"  {name}: reusing {len(texts) - len(todo)} vectors, embedding {len(todo)} "
        f"new ({calls} OpenRouter requests)")
    if todo:
        fresh = remote_retrieval.embed(todo, name, "doc", batch=batch, timeout=120)
        known.update(zip(todo, fresh))
    emb = np.vstack([known[t] for t in texts]).astype("float32")
    emb /= np.linalg.norm(emb, axis=1, keepdims=True)
    np.save(emb_path, emb)
    meta_path.write_text(json.dumps({
        "model": config.REMOTE_EMBEDDERS[name]["model"], "embedder": name,
        "dim": int(emb.shape[1]), "count": len(texts),
        "texts_digest": texts_digest(chunks), "texts": texts}, ensure_ascii=False),
        encoding="utf-8")
    log(f"  -> {emb_path.name}  ({len(texts)} x {emb.shape[1]})")
    return calls


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--granularity", choices=["both", "section"], default="both",
                    help="both = E5 dual granularity (default); section = E5 off")
    ap.add_argument("--full", action="store_true",
                    help="re-embed every chunk instead of reusing unchanged vectors")
    ap.add_argument("--embedder", choices=list(config.REMOTE_EMBEDDERS),
                    help="build this remote model's vectors for the current "
                         "chunks.json instead (costs OpenRouter requests)")
    ap.add_argument("--seed", type=pathlib.Path,
                    help="with --embedder: reuse a matching .npy (e.g. the E21 cache)")
    args = ap.parse_args()
    if args.embedder:
        build_alt(args.embedder, reuse=not args.full, seed=args.seed)
        return
    build(args.granularity, reuse=not args.full, batch=args.batch)


if __name__ == "__main__":
    main()
