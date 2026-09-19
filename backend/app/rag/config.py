"""Single source of truth for model names and paths.

These were previously hardcoded in four scripts, which is how the evaluated
pipeline and the servable one drifted apart: the log reported 41.9% (dense +
reranking) while nothing outside an eval script could rerank at all.
"""

from __future__ import annotations

import os
import pathlib

BACKEND = pathlib.Path(__file__).resolve().parents[2]
ROOT = BACKEND.parent
LAWS = ROOT / "Laws" / "main"

PARSED_DIR = LAWS / "parsed"
CHUNKS_DIR = LAWS / "chunks"
INDEX_DIR = LAWS / "index"
QA_DIR = ROOT / "Laws" / "bangladesh-legal-qa-dataset" / "qa-splits"

ACTS = ("crpc", "penal", "evidence")

# E4: bge-reranker-v2-m3 measured +9.2pp R@1 and +4.9pp Act accuracy over dense
# alone. E6 could not validly measure the decoder-based alternatives (loading a
# causal LM through AutoModelForSequenceClassification attaches a random head),
# so they are unmeasured rather than rejected.
EMBED_MODEL = os.getenv("RAG_EMBED_MODEL", "BAAI/bge-m3")
RERANK_MODEL = os.getenv("RAG_RERANK_MODEL", "BAAI/bge-reranker-v2-m3")

# Candidates fetched densely, then reranked down. 20 was what E4 measured.
DENSE_K = int(os.getenv("RAG_DENSE_K", "20"))
FINAL_K = int(os.getenv("RAG_FINAL_K", "5"))

# E5 indexes sections AND their subsections. Children below this are too thin
# to carry meaning on their own.
MIN_CHILD_TOKENS = 25

EMBED_BATCH = int(os.getenv("RAG_EMBED_BATCH", "8"))
RERANK_BATCH = int(os.getenv("RAG_RERANK_BATCH", "16"))
MAX_SEQ_LEN = 1024


def device() -> str:
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"
