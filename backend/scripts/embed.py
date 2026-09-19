"""Backwards-compatible shim.

The real implementation moved to `app/rag/encoder.py` so that the API and the
eval scripts encode identically by construction. Kept because several scripts
import `from embed import ...`.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.rag.config import MAX_SEQ_LEN as MAX_LEN  # noqa: E402,F401
from app.rag.encoder import embed_texts  # noqa: E402,F401
from app.rag.encoder import load_encoder as _load_encoder  # noqa: E402


def load_encoder(name: str, device: str):
    """(tok, model) — the old two-value signature."""
    tok, model, _ = _load_encoder(name, device)
    return tok, model
