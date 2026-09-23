"""The local copy of the vectors — the fallback when Supabase cannot search.

Only the remote embedder's vectors (`embeddings.<name>.npy`, row for row with
`chunks.json`). No model and no FAISS: the question's vector comes from
OpenRouter either way, and an exact scan of ~2,300 rows is one matrix product.

`meta.<name>.json` records a digest of the chunk texts the vectors were built
from: add a source without rebuilding them and the digest stops matching, so
they are refused rather than searched with the new Act silently missing.
"""

from __future__ import annotations

import functools
import json

from app.rag import config


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
    """(unit vectors, chunks) for an embedder, checked against chunks.json."""
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
    return np.load(emb_path).astype("float32"), chunks


def search_alt(name: str, qemb, n: int):
    """Per query: (chunks, scores, ids), best first — exact inner product."""
    import numpy as np

    emb, chunks = load_alt(name)
    sims = np.asarray(qemb, dtype="float32") @ emb.T
    n = min(n, len(chunks))
    out = []
    for row in sims:
        top = np.argpartition(-row, n - 1)[:n]
        top = top[np.argsort(-row[top], kind="stable")]
        out.append((chunks, [float(row[i]) for i in top], [int(i) for i in top]))
    return out
