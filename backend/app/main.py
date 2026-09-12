from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.citations import router as citations_router
from app.api.routes.query import router as query_router

app = FastAPI(title="AinShonglap API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(query_router)
app.include_router(citations_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
