import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.citations import router as citations_router
from app.api.routes.query import router as query_router
from app.api.routes.retrieve import router as retrieve_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load the index and both models before the first request.

    Cold-loading BGE-M3 and the cross-encoder inside a request costs ~30s. It
    also makes a missing or stale index surface as a 500 mid-query instead of
    at boot. RAG_WARMUP=0 skips it when only the mock endpoints are wanted.
    """
    if os.getenv("RAG_WARMUP", "1") != "0":
        from app.rag.pipeline import warmup

        warmup()
    yield


app = FastAPI(title="AinShonglap API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(query_router)
app.include_router(citations_router)
app.include_router(retrieve_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
