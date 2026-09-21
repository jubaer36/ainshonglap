"""Saved conversations, for signed-in users.

Signing in is optional. Without a token these endpoints report that there is
nothing stored and the browser keeps its own history, which is how the
assistant works for someone who has not signed in and for a deployment with no
Supabase at all.

The user id always comes from a **verified** access token. Nothing here reads an
id from the request body: that would be a request to impersonate, and the
service-role key would grant it.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException

from app.db import supabase
from app.models.schemas import (ConversationDetail, ConversationSummary,
                                ImportRequest)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/conversations", tags=["conversations"])


def _caller(authorization: str | None) -> str:
    """The signed-in user, or a 401. Never trusts anything but the token."""
    if not supabase.configured():
        raise HTTPException(503, "conversation storage is not configured")
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1]
    user = supabase.user_id(token)
    if not user:
        # 401 rather than 403: the request is not forbidden, it is
        # unauthenticated, and the client's response is to sign in.
        raise HTTPException(401, "sign in to use saved conversations")
    return user


@router.get("", response_model=list[ConversationSummary])
def list_conversations(authorization: str | None = Header(None)):
    from app.db import conversations as store

    user = _caller(authorization)
    return [ConversationSummary(**row) for row in store.list_conversations(user)]


@router.get("/{conversation_id}", response_model=ConversationDetail)
def get_conversation(conversation_id: str, authorization: str | None = Header(None)):
    from app.db import conversations as store

    user = _caller(authorization)
    found = store.get_conversation(user, conversation_id)
    if not found:
        # 404 for someone else's conversation as well as for one that does not
        # exist. Distinguishing them would confirm that a given id is real.
        raise HTTPException(404, "no such conversation")
    return ConversationDetail(**found)


@router.delete("/{conversation_id}")
def delete_conversation(conversation_id: str, authorization: str | None = Header(None)):
    from app.db import conversations as store

    user = _caller(authorization)
    if not store.delete_conversation(user, conversation_id):
        raise HTTPException(404, "no such conversation")
    return {"deleted": conversation_id}


@router.post("/import")
def import_conversations(payload: ImportRequest,
                         authorization: str | None = Header(None)):
    """Adopt conversations built before signing in.

    Anonymous history lives in the browser; signing in should keep it rather
    than stranding it. Ids are reassigned server-side — one chosen by the client
    is one the client could collide with someone else's.
    """
    from app.db import conversations as store

    user = _caller(authorization)
    try:
        count = store.import_conversations(user, [c.model_dump() for c in payload.conversations])
    except Exception as exc:                        # pragma: no cover - defensive
        log.exception("import failed")
        raise HTTPException(502, f"import failed: {type(exc).__name__}") from exc
    return {"imported": count}
