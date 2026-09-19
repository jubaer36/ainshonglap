"""BGE-M3 dense encoding.

transformers directly rather than sentence-transformers: 6.x tries to load an
AutoProcessor for this repo and raises. BGE-M3's dense vector is simply the CLS
token of the last hidden state, L2-normalised, so the wrapper buys nothing.

Normalised vectors mean inner product == cosine, which is what the FAISS
IndexFlatIP expects. Query and document vectors MUST come from this same
function — a different pooling on either side makes the inner product noise.
"""

from __future__ import annotations

import functools

import numpy as np
import torch

from app.rag import config


@functools.lru_cache(maxsize=2)
def load_encoder(name: str | None = None, device: str | None = None):
    """Tokenizer + model, loaded once per process.

    Cached because the API would otherwise pay ~20s of model load per request.
    """
    from transformers import AutoModel, AutoTokenizer

    name = name or config.EMBED_MODEL
    device = device or config.device()
    tok = AutoTokenizer.from_pretrained(name)
    model = AutoModel.from_pretrained(name).to(device).eval()
    return tok, model, device


@torch.no_grad()
def embed_texts(texts, tok, model, device, batch=None, progress=False):
    batch = batch or config.EMBED_BATCH
    out = []
    for i in range(0, len(texts), batch):
        enc = tok(texts[i : i + batch], padding=True, truncation=True,
                  max_length=config.MAX_SEQ_LEN, return_tensors="pt").to(device)
        hidden = model(**enc).last_hidden_state[:, 0]          # CLS
        hidden = torch.nn.functional.normalize(hidden, p=2, dim=-1)
        out.append(hidden.cpu().numpy())
        if progress and (i // batch) % 10 == 0:
            print(f"    {min(i+batch, len(texts))}/{len(texts)}", flush=True)
    return np.vstack(out).astype("float32")


def embed(texts: list[str], batch: int | None = None) -> np.ndarray:
    tok, model, device = load_encoder()
    return embed_texts(texts, tok, model, device, batch=batch)
