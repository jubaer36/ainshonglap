"""Retrieval pipeline shared by the API and the evaluation scripts.

This package exists because the two had drifted: `experiments.md` logged 41.9%
Recall@1 for dense + cross-encoder reranking, but reranking lived only inside
`scripts/rerank.py` as measurement scaffolding, so nothing servable could do
it. Anything measured here is what actually runs.
"""

from app.rag.pipeline import Retrieved, retrieve, warmup

__all__ = ["Retrieved", "retrieve", "warmup"]
