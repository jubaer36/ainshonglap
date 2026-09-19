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
