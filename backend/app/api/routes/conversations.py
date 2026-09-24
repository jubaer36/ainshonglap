"""Saved conversations for signed-in users: list, open, rename, delete, name.
Guests get nothing stored.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException

from app.db import supabase
from app.models.schemas import (ConversationDetail, ConversationSummary,
                                ImportRequest, NameRequest, RenameRequest)

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


@router.post("/{conversation_id}/name")
def name_conversation(conversation_id: str, payload: NameRequest,
                      authorization: str | None = Header(None)) -> dict:
    """Name a conversation from its summary, if it has earned one.

    Its own request, not part of `POST /api/query`: naming is not on the path to
    an answer. Its own model and key too — see `app/rag/naming.py`.

    Returns `{"status", "conversation"}`. `status` is "named", "kept" (the
    title is the reader's or already named — stop asking), "not_yet" (too few
    answered turns) or "failed" (the naming model was unreachable). On the last
    two the client asks again after the next answer.
    """
    from app.db import conversations as store

    user = _caller(authorization)
    memory = payload.memory.model_dump() if payload.memory else None
    status, row = store.name_conversation(user, conversation_id, memory)
    return {"status": status,
            "conversation": ConversationSummary(**row).model_dump() if row else None}


@router.patch("/{conversation_id}", response_model=ConversationSummary)
def rename_conversation(conversation_id: str, payload: RenameRequest,
                        authorization: str | None = Header(None)):
    """Retitle a conversation.

    Titles are the reader's, not the system's. The default is the first question
    asked, which is a reasonable guess and often the wrong label for a
    conversation that moved on.
    """
    from app.db import conversations as store

    user = _caller(authorization)
    renamed = store.rename_conversation(user, conversation_id, payload.title)
    if not renamed:
        # 404 for someone else's conversation as well as for one that does not
        # exist, for the same reason as the read: distinguishing them would
        # confirm that a given id is real.
        raise HTTPException(404, "no such conversation")
    return ConversationSummary(**renamed)


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
