"""The FAISS index and the chunk records that sit behind it.

Loaded lazily and kept for the process lifetime: the index is a few MB and
re-reading it per request would dominate query latency.
"""

from __future__ import annotations

import functools
import json

from app.rag import config


@functools.lru_cache(maxsize=1)
def load_store():
    """(faiss index, chunks, meta).

    Chunk order is the vector order — `chunks[i]` is the record for vector `i`.
    build_index.py writes both from the same list, so the only way they can
    disagree is if one is rebuilt without the other.
    """
    import faiss

    idx_path = config.INDEX_DIR / "chunks.faiss"
    if not idx_path.exists():
        raise FileNotFoundError(
            f"no index at {idx_path} — run `python scripts/build_index.py` first"
        )
    index = faiss.read_index(str(idx_path))
    chunks = json.loads((config.INDEX_DIR / "chunks.json").read_text(encoding="utf-8"))
    meta = json.loads((config.INDEX_DIR / "meta.json").read_text(encoding="utf-8"))
    if index.ntotal != len(chunks):
        raise RuntimeError(
            f"index/chunks mismatch: {index.ntotal} vectors vs {len(chunks)} records "
            "— rebuild with scripts/build_index.py"
        )
    if meta.get("model") != config.EMBED_MODEL:
        raise RuntimeError(
            f"index was built with {meta.get('model')} but config says "
            f"{config.EMBED_MODEL}; query and document vectors must match"
        )
    return index, chunks, meta


# ── alternate embedders (E21) ───────────────────────────────────────────────
# A remote embedder's vectors for the SAME chunks.json, row for row, in
# `embeddings.<name>.npy`. `meta.<name>.json` records a digest of the chunk
# texts they were built from: add a source without rebuilding these and the
# digest stops matching, so they are refused rather than searched with the new
# Act silently missing.

def alt_paths(name: str):
    return (config.INDEX_DIR / f"embeddings.{name}.npy",
            config.INDEX_DIR / f"meta.{name}.json")


def texts_digest(chunks: list[dict]) -> str:
    import hashlib

    h = hashlib.sha256()
    for c in chunks:
        h.update(c["text"].encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


class AltMissing(RuntimeError):
    """No usable vectors for this embedder: absent, or built for another corpus."""


@functools.lru_cache(maxsize=4)
def load_alt(name: str):
    """(faiss index, chunks) for a remote embedder, checked against chunks.json."""
    import faiss
    import numpy as np

    emb_path, meta_path = alt_paths(name)
    if not emb_path.exists() or not meta_path.exists():
        raise AltMissing(f"no {emb_path.name} — run "
                         f"`python scripts/build_index.py --embedder {name}`")
    chunks = json.loads((config.INDEX_DIR / "chunks.json").read_text(encoding="utf-8"))
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("texts_digest") != texts_digest(chunks):
        raise AltMissing(f"{emb_path.name} was built for a different corpus — "
                         f"rebuild with `build_index.py --embedder {name}`")
    emb = np.load(emb_path).astype("float32")
    index = faiss.IndexFlatIP(emb.shape[1])
    index.add(emb)
    return index, chunks
