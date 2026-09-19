"""Which generators this deployment can use."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.rag import config

router = APIRouter(prefix="/api", tags=["ops"])


class GeneratorInfo(BaseModel):
    alias: str
    backend: str
    model: str
    enforces_schema: bool | None = None
    note: str | None = None
    default: bool = False


@router.get("/models", response_model=list[GeneratorInfo])
async def list_models() -> list[GeneratorInfo]:
    """Pass any `alias` as `model` on POST /api/query.

    `enforces_schema` is the one field that matters for correctness: the
    pipeline needs JSON with a verbatim quote per claim, and a route that
    ignores the schema returns prose, which surfaces as a false refusal. false =
    measured as not enforcing; null = advertised but unverified.
    """
    return [
        GeneratorInfo(alias=a, default=(a == config.DEFAULT_GENERATOR), **g)
        for a, g in config.GENERATORS.items()
    ]
