"""BGE-M3 dense encoding, via transformers directly.

sentence-transformers 6.x cannot load this repo (it looks for an AutoProcessor
and raises). BGE-M3's dense representation is the CLS token of the last hidden
state, L2-normalised, so going direct costs nothing and removes a dependency
that was actively in the way.

Normalised vectors mean inner product == cosine similarity, which is what the
FAISS IndexFlatIP in build_index.py expects.
"""

from __future__ import annotations

import numpy as np
import torch
from transformers import AutoModel, AutoTokenizer

MAX_LEN = 1024   # our largest chunk is ~945 tokens; 8192 is available if needed


def load_encoder(name: str, device: str):
    tok = AutoTokenizer.from_pretrained(name)
    model = AutoModel.from_pretrained(name).to(device).eval()
    return tok, model


@torch.no_grad()
def embed_texts(texts, tok, model, device, batch=16, progress=False):
    out = []
    for i in range(0, len(texts), batch):
        chunk = texts[i : i + batch]
        enc = tok(chunk, padding=True, truncation=True, max_length=MAX_LEN,
                  return_tensors="pt").to(device)
        hidden = model(**enc).last_hidden_state[:, 0]        # CLS
        hidden = torch.nn.functional.normalize(hidden, p=2, dim=-1)
        out.append(hidden.cpu().numpy())
        if progress and (i // batch) % 10 == 0:
            print(f"    {min(i+batch, len(texts))}/{len(texts)}", flush=True)
    return np.vstack(out).astype("float32")
