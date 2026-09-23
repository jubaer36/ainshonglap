"""Chunk index + vectors: chunks.json, and the embedder's vectors from OpenRouter.

No model runs locally. Vectors come from nemotron-3-embed on OpenRouter
(`config.EMBEDDER`, E21) and are written to `embeddings.<name>.npy`, row for row
with `chunks.json`. Only chunks whose text has no vector yet are sent — each
request counts against the free-model daily cap (1,000 per account with credits
bought, 50 without), and adding one Act costs roughly its chunk count / 32.

Exact inner product over unit vectors (cosine), no approximate index: at this
size an exact scan costs milliseconds and keeps results reproducible.

Run (from backend/, ainshonglap conda env active):

    python scripts/build_index.py              # chunks + nemotron vectors (new text only)
    python scripts/build_index.py --full       # re-embed every chunk (~73 requests)
    python scripts/build_index.py --embedder nemotron-3-embed --seed X.npy   # reuse a cache
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
          batch: int | None = None, embedder: str | None = None) -> dict:
    """Write chunks.json and meta.json, then the embedder's vectors. Returns meta.

    No model runs here: the vectors come from OpenRouter (`build_alt`), for new
    or changed text only — every known vector is reused — so adding one Act
    costs roughly its chunk count / 32 requests, and removing one costs none.
    The order is fixed — Acts by slug, each Act's sections then its
    subsections — so a rebuild lines up row for row.

    `embeddings.npy` (BGE-M3, from `main`) is carried along row for row where a
    chunk's text already has one, and NaN where it does not. Nothing here reads
    it; it exists so `load_supabase.py` keeps `law_chunks` intact for `main`,
    which shares the database. A new chunk has no BGE vector and is simply not
    written there.
    """
    config.INDEX_DIR.mkdir(parents=True, exist_ok=True)
    chunks = load_chunks(granularity)
    n_child = sum(1 for c in chunks if c["metadata"].get("granularity") == "child")
    log(f"chunks: {len(chunks)}  ({len(chunks)-n_child} section, {n_child} child)")

    bge_path = config.INDEX_DIR / "embeddings.npy"
    old_path = config.INDEX_DIR / "chunks.json"
    if bge_path.exists() and old_path.exists():
        old = json.loads(old_path.read_text(encoding="utf-8"))
        old_emb = np.load(bge_path)
        if len(old) == len(old_emb):
            known = {c["text"]: old_emb[i] for i, c in enumerate(old)}
            dim = old_emb.shape[1]
            emb = np.vstack([known.get(c["text"], np.full(dim, np.nan, "float32"))
                             for c in chunks]).astype("float32")
            np.save(bge_path, emb)
            log(f"  BGE-M3 vectors carried for {int(np.isfinite(emb[:, 0]).sum())} "
                f"of {len(chunks)} chunks (main's law_chunks only)")

    embed_name = embedder or config.EMBEDDER
    (config.INDEX_DIR / "chunks.json").write_text(
        json.dumps(chunks, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    meta = {"embedder": embed_name, "count": len(chunks),
            "sections": len(chunks) - n_child, "children": n_child,
            "granularity": granularity, "normalised": True, "metric": "inner_product"}
    (config.INDEX_DIR / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    # Reuses by text from its own meta file, so writing chunks.json first is safe.
    build_alt(embed_name, reuse=reuse, log=log)
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
    build(args.granularity, reuse=not args.full)


if __name__ == "__main__":
    main()
