"""AinShonglap API.

  POST /api/query          question -> cited answer, or a refusal
  GET  /api/retrieve       raw retrieval, no generation (inspection/debugging)
  GET  /api/acts           indexed acts
  GET  /api/sections       browse sections
  GET  /api/sections/{key} one section, by citation key
  GET  /health             liveness + what is loaded

Retrieval is measured (see retrieval-baseline.md / experiments.md); generation
is verified per-claim at request time rather than trusted.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.citations import router as citations_router
from app.api.routes.query import router as query_router
from app.api.routes.retrieve import router as retrieve_router

log = logging.getLogger(__name__)

# Set once at startup so /health can report why the API is degraded instead of
# failing per request with the same error.
STATE: dict[str, object] = {"ready": False, "error": None, "index": None}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load the index and both retrieval models before serving.

    Cold-loading BGE-M3 and the cross-encoder inside a request costs ~30s, and a
    missing index should surface at boot rather than as a 500 mid-query. Warmup
    failure is recorded rather than raised: the corpus endpoints still work
    without a generator, and /health says what is wrong.
    """
    if os.getenv("RAG_WARMUP", "1") != "0":
        try:
            from app.rag.pipeline import warmup
            from app.rag.store import load_store

            warmup()
            index, chunks, meta = load_store()
            STATE.update(ready=True, index={
                "vectors": int(index.ntotal),
                "sections": meta.get("sections"),
                "children": meta.get("children"),
                "granularity": meta.get("granularity"),
                "embedder": meta.get("model"),
            })
        except Exception as exc:               # noqa: BLE001 - reported, not raised
            log.exception("warmup failed")
            STATE.update(ready=False, error=f"{type(exc).__name__}: {exc}")
    yield


app = FastAPI(
    title="AinShonglap API",
    version="0.2.0",
    description="Source-grounded Q&A over Bangladesh criminal law.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(query_router)
app.include_router(citations_router)
app.include_router(retrieve_router)


@app.get("/health", tags=["ops"])
async def health() -> dict:
    from app.rag import config

    return {
        "status": "ok" if STATE["ready"] else "degraded",
        "error": STATE["error"],
        "index": STATE["index"],
        "reranker": config.RERANK_MODEL,
        "generator": {
            "backend": os.getenv("RAG_LLM_BACKEND", "openrouter"),
            "model": os.getenv("RAG_OPENROUTER_MODEL",
                               "deepseek/deepseek-v4-flash-0731:free"),
        },
    }
