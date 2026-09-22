"""AinShonglap API.

  POST /api/query          question -> cited answer, or a refusal
  GET  /api/plan           what the planner decided, without answering
  POST /api/transcribe     voice input -> transcript (never an answer)
  POST /api/read-image     one page image -> text (never an answer)
  GET  /api/retrieve       raw retrieval, no generation (inspection/debugging)
  GET  /api/models         selectable generators
  GET  /api/acts           indexed acts
  GET  /api/sections       browse sections
  GET  /api/sections/{key} one section, by citation key
  GET  /api/conversations  saved conversations (signed in)
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
from app.api.routes.conversations import router as conversations_router
from app.api.routes.models import router as models_router
from app.api.routes.query import router as query_router
from app.api.routes.speech import router as speech_router
from app.api.routes.retrieve import router as retrieve_router

log = logging.getLogger(__name__)

# uvicorn configures its own handlers and leaves other loggers at WARNING, so the
# per-step trace would be silently dropped. Attach one handler to the trace
# logger explicitly, and keep it off the root so nothing else is reformatted.
_trace_log = logging.getLogger("ainshonglap")
if not _trace_log.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    _trace_log.addHandler(_handler)
    _trace_log.propagate = False
_trace_log.setLevel(logging.INFO if os.getenv("RAG_TRACE", "1") == "1"
                    else logging.WARNING)

# Set once at startup so /health can report why the API is degraded instead of
# failing per request with the same error.
STATE: dict[str, object] = {"ready": False, "error": None, "index": None,
                            "catalog": None, "warming": False}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start serving immediately; load the retrieval models in the background.

    Cold-loading BGE-M3 and the cross-encoder costs ~30s. Doing that *before*
    yielding meant the socket was bound but nothing was answered for half a
    minute, so a browser that loaded the page in 368ms asked for the model list
    and timed out — the failure looked like a broken backend rather than one
    that was still waking up.

    Warmup now runs in a worker thread. Requests that need the index block only
    until it is ready (`load_store` and the encoders are cached, so they wait on
    the same load rather than starting a second one), while everything that does
    not — the model list, the catalog, the statute browser, /health — answers at
    once. `/health` reports `warming` until it finishes, so "not ready yet" is
    visible rather than inferred from a hang.
    """
    # The catalog is SQLite and opens in milliseconds, so it is checked here:
    # a missing catalog should surface at boot, and /health can report the
    # corpus before the retrieval models have finished loading.
    try:
        from app.rag import catalog

        STATE["catalog"] = catalog.manifest()
    except Exception as exc:                       # noqa: BLE001 - reported
        STATE["catalog"] = None
        log.warning("catalog unavailable: %s", exc)

    if os.getenv("RAG_WARMUP", "1") != "0":
        import asyncio

        STATE["warming"] = True
        asyncio.get_running_loop().run_in_executor(None, _warmup)
    yield


def _warmup() -> None:
    """Load the index and both retrieval models. Runs off the event loop."""
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
    except Exception as exc:                       # noqa: BLE001 - reported, not raised
        log.exception("warmup failed")
        STATE.update(ready=False, error=f"{type(exc).__name__}: {exc}")
    finally:
        STATE["warming"] = False


app = FastAPI(
    title="AinShonglap API",
    version="0.2.0",
    description="Source-grounded Q&A over Bangladesh criminal law.",
    lifespan=lifespan,
)

# `localhost` and `127.0.0.1` are DIFFERENT origins to the browser, even though
# they are the same machine. Allowing only one means opening the app at the
# other address makes every request fail CORS — which surfaces as "the backend
# is down" with a 200 sitting in the network tab, because the response is
# blocked after it arrives.
DEFAULT_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000"

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in
                   os.getenv("CORS_ORIGINS", DEFAULT_ORIGINS).split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(query_router)
app.include_router(citations_router)
app.include_router(retrieve_router)
app.include_router(models_router)
app.include_router(speech_router)
app.include_router(conversations_router)


def _quota_state() -> dict:
    """Models currently out of budget. Empty is the normal case."""
    from app.rag import quota

    return quota.snapshot()


def _naming_state() -> dict:
    """Whether conversation naming is configured, and on which key."""
    from app.rag import naming

    return {"available": naming.available(), "model": naming.MODEL,
            "key_env": naming.KEY_ENV, "after_answers": naming.AFTER_ANSWERS}


def _speech_state() -> dict:
    """Whether voice input is configured. Reported so a missing key shows up
    here rather than as a 503 the first time someone taps the microphone."""
    from app.rag import speech

    return {"available": speech.available(), "model": speech.MODEL,
            "language": speech.LANGUAGE}


def _database_state() -> dict:
    """Whether Supabase is wired up. Unconfigured is a supported state: the
    assistant runs anonymously and history stays in the browser."""
    from app.db import supabase

    return supabase.state()


def _vision_state() -> dict:
    """Whether image input is configured, and what it will accept."""
    from app.rag import vision

    return {"available": vision.available(), "model": vision.MODEL,
            "accepts": sorted(vision.ALLOWED_TYPES),
            "max_mb": vision.MAX_BYTES // (1024 * 1024), "pages": 1}


@app.get("/health", tags=["ops"])
async def health() -> dict:
    from app.rag import config

    return {
        "status": ("ok" if STATE["ready"]
                   else "warming" if STATE["warming"] else "degraded"),
        "error": STATE["error"],
        "index": STATE["index"],
        "catalog": STATE["catalog"],
        "reranker": config.RERANK_MODEL,
        "planner": {"model": config.PLANNER_MODEL,
                    "enabled": config.PLAN_QUERIES},
        # Reported because it is the one part of the system that fails
        # invisibly: without GEMINI_API_KEY_3 conversations simply keep their
        # first question as a title, which looks like a feature that was never
        # built rather than a key that was never set.
        "naming": _naming_state(),
        "speech": _speech_state(),
        "vision": _vision_state(),
        "database": _database_state(),
        "quota_blocked": _quota_state(),
        "generator": dict(zip(("backend", "model"),
                              config.resolve_generator(None))) |
                     {"alias": config.DEFAULT_GENERATOR,
                      "refine_queries": config.REFINE_QUERIES},
    }
