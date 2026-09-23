"""Embedding and reranking on OpenRouter (E21) — the only retrieval models.

Keys are every `OPENROUTER_API_KEY*`, in rotation with failover (`keyring`),
the same way the Groq generators use theirs. Anything that goes wrong raises
`Unavailable`: for embedding that means meaning search cannot run (there is no
local model), and the pipeline says so; for reranking, the search order is used.
That includes the free-model daily cap — 1,000 requests a day per account with
credits bought, 50 without, shared by every `:free` model.
"""

from __future__ import annotations

import numpy as np

from app.rag import config

API = "https://openrouter.ai/api/v1"
PREFIX = "OPENROUTER_API_KEY"


class Unavailable(RuntimeError):
    """OpenRouter could not serve this call; use the local models."""


def _post(path: str, body: dict, timeout: float | None = None) -> dict:
    import requests

    from app.rag import keyring

    slots = keyring.rotation(PREFIX)
    if not slots:
        raise Unavailable(f"no {PREFIX} configured")
    last = "no attempt"
    for slot in slots:
        try:
            r = requests.post(f"{API}/{path}", json=body,
                              timeout=timeout or config.REMOTE_TIMEOUT,
                              headers={"Authorization": f"Bearer {slot.value}"})
        except requests.RequestException as exc:
            last = f"{slot.env}: {type(exc).__name__}"
            continue
        if r.status_code == 200:
            data = r.json()
            if "error" not in data:
                return data
            last = f"{slot.env}: {str(data['error'])[:160]}"
        else:
            last = f"{slot.env}: HTTP {r.status_code} {r.text[:160]}"
        # A 400 is the request, not the key — another key would fail the same way.
        if r.status_code == 400:
            break
    raise Unavailable(last)


def embed(texts: list[str], name: str, side: str, *,
          batch: int = 32, timeout: float | None = None) -> np.ndarray:
    """Unit vectors for `texts` from a remote embedder. side: "query" | "doc"."""
    spec = config.REMOTE_EMBEDDERS[name]
    how = spec[side]
    out = []
    for i in range(0, len(texts), batch):
        body = {"model": spec["model"],
                "input": [how.get("prefix", "") + t for t in texts[i:i + batch]]}
        if "input_type" in how:
            body["input_type"] = how["input_type"]
        data = _post("embeddings", body, timeout)
        out.extend(x["embedding"] for x in sorted(data["data"], key=lambda x: x["index"]))
    emb = np.asarray(out, dtype="float32")
    if emb.shape != (len(texts), spec["dim"]):
        raise Unavailable(f"{name} returned shape {emb.shape}")
    return emb / np.linalg.norm(emb, axis=1, keepdims=True)


def rerank(query: str, documents: list[str]) -> list[float]:
    """Relevance per document, in the order given — higher is better."""
    if not documents:
        return []
    data = _post("rerank", {"model": config.REMOTE_RERANK_MODEL,
                            "query": query, "documents": documents})
    scores = [float("-inf")] * len(documents)
    for r in data.get("results", []):
        scores[int(r["index"])] = float(r["relevance_score"])
    if float("-inf") in scores:
        raise Unavailable("reranker returned fewer scores than documents")
    return scores


def configured() -> bool:
    from app.rag import keyring

    return keyring.count(PREFIX) > 0
