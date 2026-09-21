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
    # False when this route is out of quota. The picker greys it out rather
    # than offering a choice that is known to fail.
    available: bool = True
    unavailable_reason: str | None = None
    seconds_remaining: int | None = None
    # True for the route that answers when the chosen one is unavailable.
    fallback: bool = False


@router.get("/models", response_model=list[GeneratorInfo])
async def list_models() -> list[GeneratorInfo]:
    """Pass any `alias` as `model` on POST /api/query.

    `enforces_schema` is the one field that matters for correctness: the
    pipeline needs JSON with a verbatim quote per claim, and a route that
    ignores the schema returns prose, which surfaces as a false refusal. false =
    measured as not enforcing; null = advertised but unverified.
    """
    from app.rag import quota

    out: list[GeneratorInfo] = []
    for alias, spec in config.GENERATORS.items():
        blocked = quota.status(spec["model"])
        out.append(GeneratorInfo(
            alias=alias,
            default=(alias == config.DEFAULT_GENERATOR),
            fallback=(alias == config.FALLBACK_GENERATOR),
            available=blocked is None,
            unavailable_reason=blocked["reason"] if blocked else None,
            seconds_remaining=blocked["seconds_remaining"] if blocked else None,
            **spec,
        ))
    return out
