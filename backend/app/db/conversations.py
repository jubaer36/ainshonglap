"""Conversation storage in Supabase.

**This module is the security boundary.** The API connects with the service-role
key, which bypasses Row Level Security, so the policies in `db/schema.sql` are
not enforcing anything here — every function below filters by `user_id`, and
that filter is the only thing standing between one person's legal questions and
another's.

Two rules, applied without exception:

  1. every statement carries `.eq("user_id", user_id)`, including the ones that
     already look scoped by `conversation_id`. A conversation id is a uuid, not
     a capability: given one, an unfiltered read would return someone else's
     conversation.
  2. `user_id` always comes from a verified access token (`supabase.user_id`),
     never from the request body. A client-supplied id is a request to
     impersonate.

Anonymous use is unchanged: with no token there is no `user_id`, nothing is
written here, and the browser keeps its own history as before.
"""

from __future__ import annotations

import logging
from typing import Any

from app.db.supabase import client
from app.rag import trace

log = logging.getLogger(__name__)

MAX_TITLE = 120


def _missing_column(exc: Exception) -> bool:
    """Has the session-fidelity migration not been applied?

    PostgREST reports this two different ways and the difference is easy to
    miss: a SELECT of an unknown column comes back as Postgres `42703`
    ("column ... does not exist"), while an INSERT comes back as `PGRST204`
    ("Could not find the 'generator' column ... in the schema cache") — from
    PostgREST's own cache, never reaching Postgres. Matching only the first let
    every insert fail instead of falling back.
    """
    text = str(exc).lower()
    return ("42703" in text
            or "pgrst204" in text
            or "does not exist" in text
            or "could not find the" in text)


def _title(text: str) -> str:
    text = " ".join((text or "").split())
    if not text:
        return "New conversation"
    return text[: MAX_TITLE - 1] + "…" if len(text) > MAX_TITLE else text


def list_conversations(user_id: str, limit: int = 50) -> list[dict]:
    """This user's conversations, newest first."""
    rows = (client().table("conversations")
            .select("id, title, memory, created_at, updated_at")
            .eq("user_id", user_id)
            .order("updated_at", desc=True)
            .limit(limit).execute())
    return rows.data or []


def get_conversation(user_id: str, conversation_id: str) -> dict | None:
    """One conversation with its messages, or None.

    Both queries filter on `user_id`. Filtering only on `conversation_id` would
    hand over any conversation whose id was guessed or leaked, because the
    service-role key answers regardless of owner.
    """
    found = (client().table("conversations")
             .select("id, title, memory, created_at, updated_at")
             .eq("user_id", user_id).eq("id", conversation_id)
             .limit(1).execute())
    if not found.data:
        return None
    conversation = found.data[0]
    try:
        messages = (client().table("messages")
                    .select("id, role, content, memory, generator, plan, seq")
                    .eq("user_id", user_id).eq("conversation_id", conversation_id)
                    .order("seq").execute())
    except Exception as exc:
        if not _missing_column(exc):
            raise
        messages = (client().table("messages")
                    .select("id, role, content, seq")
                    .eq("user_id", user_id).eq("conversation_id", conversation_id)
                    .order("seq").execute())
    rows = messages.data or []

    images = _images(user_id, conversation_id)
    conversation["messages"] = [
        # The message as it was sent, plus what it was produced against. The
        # client renders `content`; the rest is what makes the session
        # inspectable rather than merely re-readable.
        dict(row["content"], _memory=row.get("memory"),
             _generator=row.get("generator"), _plan=row.get("plan"),
             _images=images.get(row["id"], []))
        for row in rows
    ]
    conversation["images"] = [img for group in images.values() for img in group]
    return conversation


def _images(user_id: str, conversation_id: str) -> dict[str, list[dict]]:
    """Signed URLs for this conversation's page images, keyed by message id.

    Signed rather than public: the bucket holds photographs of documents people
    uploaded, and a public URL is a permanent unauthenticated link to one.
    """
    try:
        rows = (client().table("uploads")
                .select("id, message_id, path, mime, bytes, chars, transcript, created_at")
                .eq("user_id", user_id).eq("conversation_id", conversation_id)
                .order("created_at").execute()).data or []
    except Exception:                               # pragma: no cover - defensive
        return {}

    from app.db.supabase import bucket

    grouped: dict[str, list[dict]] = {}
    for row in rows:
        try:
            signed = client().storage.from_(bucket()).create_signed_url(
                row["path"], 3600)
            row["url"] = signed.get("signedURL") or signed.get("signedUrl")
        except Exception:
            row["url"] = None
        grouped.setdefault(row.get("message_id") or "", []).append(row)
    return grouped


def name_conversation(user_id: str, conversation_id: str,
                      memory: dict | None) -> dict | None:
    """Give a conversation a name of its own, once it has earned one.

    Returns the conversation row when the title changed, None when it did not —
    which is the ordinary outcome, since this is safe to call more than once.

    **A title the reader typed is never overwritten.** The guard is that the
    stored title still equals the one derived from the first question. If it
    does not, either they renamed it or naming already ran, and both outcomes
    are theirs to keep. No extra column is needed to know that, which matters
    because the last migration is still waiting to be applied by hand.

    Never raises. A conversation with the wrong name is a much smaller problem
    than the request it was called from.
    """
    from app.rag import memory as mem
    from app.rag import naming

    parsed = mem.Memory.from_dict(memory)
    if not naming.earned(parsed):
        return None
    try:
        row = (client().table("conversations").select("*")
               .eq("user_id", user_id).eq("id", conversation_id)
               .limit(1).execute())
        if not row.data:
            return None
        current = row.data[0].get("title") or ""

        first = (client().table("messages").select("content")
                 .eq("user_id", user_id).eq("conversation_id", conversation_id)
                 .eq("role", "user").order("seq").limit(1).execute())
        if not first.data:
            return None
        opening = _title((first.data[0].get("content") or {}).get("text", ""))
        if current != opening:
            trace.step("name", "title is the reader's, left alone")
            return None

        chosen = naming.suggest(parsed.summary)
        if not chosen or _title(chosen) == current:
            return None
        updated = (client().table("conversations")
                   .update({"title": _title(chosen)})
                   .eq("user_id", user_id).eq("id", conversation_id).execute())
        trace.step("supabase", f"named conversation «{chosen}»")
        return updated.data[0] if updated.data else None
    except Exception:                               # pragma: no cover - defensive
        log.warning("naming failed; the title is unchanged", exc_info=True)
        return None


def save_turn(user_id: str, conversation_id: str | None, question: dict,
              answer: dict, memory: dict | None) -> str:
    """Persist one exchange. Returns the conversation id.

    Creates the conversation on the first turn, titled from the question — the
    user's own words, which they will recognise, rather than a generated
    paraphrase that costs a model call to make recall harder.

    The memory snapshot is written onto the ASSISTANT message as well as onto
    the conversation. The conversation copy is what the next turn reads; the
    per-message copy is what makes a session reloadable *as it happened*, since
    an answer was produced against the context of its own moment and replaying
    it against a later summary would explain the wrong thing.
    """
    table = client().table("conversations")

    if conversation_id:
        # An UPDATE scoped to the owner: a conversation id belonging to someone
        # else matches no row and silently updates nothing, which is the
        # intended outcome.
        updated = (table.update({"memory": memory})
                   .eq("user_id", user_id).eq("id", conversation_id)
                   .execute())
        if not updated.data:
            conversation_id = None            # not theirs, or gone: start fresh

    if conversation_id:
        trace.step("supabase", f"updated conversation {conversation_id[:8]}")
    if not conversation_id:
        created = table.insert({
            "user_id": user_id,
            "title": _title(question.get("text", "")),
            "memory": memory,
        }).execute()
        conversation_id = created.data[0]["id"]
        trace.step("supabase", f"created conversation {conversation_id[:8]}")

    seq = _next_seq(user_id, conversation_id)
    rows = [
        {"conversation_id": conversation_id, "user_id": user_id,
         "role": "user", "content": question, "seq": seq},
        {"conversation_id": conversation_id, "user_id": user_id,
         "role": "assistant", "content": answer, "seq": seq + 1,
         # Lifted out of `content` so a session can be audited without
         # unpacking every row: which model answered, what the planner decided,
         # and the context this answer was produced against.
         "memory": memory,
         "generator": answer.get("generator"),
         "plan": _plan_of(answer)},
    ]
    try:
        inserted = client().table("messages").insert(rows).execute()
    except Exception as exc:
        # `db/migrations/001_session_fidelity.sql` has not been applied yet.
        # Storing the turn without its per-message snapshot is much better than
        # refusing to store it: the conversation still reloads, it just cannot
        # show the context each answer was produced against.
        if not _missing_column(exc):
            raise
        log.warning("messages.memory/generator/plan missing — saving without "
                    "them. Apply db/migrations/001_session_fidelity.sql.")
        inserted = client().table("messages").insert(
            [{k: v for k, v in row.items()
              if k not in ("memory", "generator", "plan")} for row in rows]
        ).execute()

    # Attach any images uploaded for this turn but not yet linked — they were
    # sent before the message existed, so they could not carry its id.
    trace.step("supabase", f"inserted 2 messages at seq {seq}-{seq + 1}")
    assistant_id = next((row["id"] for row in (inserted.data or [])
                         if row.get("role") == "assistant"), None)
    if assistant_id:
        _link_pending_uploads(user_id, conversation_id, assistant_id)
    return conversation_id


def _plan_of(answer: dict) -> dict | None:
    """The planner's decision, as a small flat record."""
    keys = ("resolved_question", "generator_substituted", "notice",
            "suggested_model", "kind")
    found = {k: answer.get(k) for k in keys if answer.get(k) is not None}
    return found or None


def _link_pending_uploads(user_id: str, conversation_id: str,
                          message_id: str) -> None:
    """Point this turn's images at the message they produced.

    An image is uploaded while the reader is still composing, so it cannot know
    its message id yet. Unlinked rows for this conversation are claimed by the
    turn that follows them.
    """
    try:
        (client().table("uploads")
         .update({"message_id": message_id})
         .eq("user_id", user_id)
         .eq("conversation_id", conversation_id)
         .is_("message_id", "null")
         .execute())
    except Exception:                               # pragma: no cover - defensive
        pass            # provenance is a nice-to-have; never fail a saved turn


def _next_seq(user_id: str, conversation_id: str) -> int:
    rows = (client().table("messages").select("seq")
            .eq("user_id", user_id).eq("conversation_id", conversation_id)
            .order("seq", desc=True).limit(1).execute())
    return (rows.data[0]["seq"] + 1) if rows.data else 0


def rename_conversation(user_id: str, conversation_id: str,
                        title: str) -> dict | None:
    """Give one conversation a new title. None when it is not this user's.

    Filtered by `user_id` as well as `id`, like every other statement here: the
    service-role key bypasses RLS, so the id alone would let anyone holding a
    uuid retitle a stranger's conversation.

    The title is passed through `_title`, so a renamed conversation obeys the
    same length cap as an auto-titled one and an empty rename falls back to the
    default rather than writing a blank row into the sidebar.
    """
    updated = (client().table("conversations")
               .update({"title": _title(title)})
               .eq("user_id", user_id).eq("id", conversation_id)
               .execute())
    if not updated.data:
        return None
    trace.step("renamed", conversation_id)
    return updated.data[0]


def delete_conversation(user_id: str, conversation_id: str) -> bool:
    """Delete one. Messages go with it, by `on delete cascade`."""
    deleted = (client().table("conversations").delete()
               .eq("user_id", user_id).eq("id", conversation_id)
               .execute())
    return bool(deleted.data)


def import_conversations(user_id: str, items: list[dict]) -> int:
    """Adopt conversations a user built before signing in.

    Anonymous history lives in the browser; signing in should keep it rather
    than silently stranding it. Each arrives with its messages and memory and is
    inserted as a new row — ids are reassigned, because an id chosen by the
    client is an id the client could collide with someone else's.
    """
    imported = 0
    for item in items:
        messages: list[dict[str, Any]] = item.get("messages") or []
        if not messages:
            continue
        created = client().table("conversations").insert({
            "user_id": user_id,
            "title": _title(item.get("title") or ""),
            "memory": item.get("memory"),
        }).execute()
        conversation_id = created.data[0]["id"]
        client().table("messages").insert([
            {"conversation_id": conversation_id, "user_id": user_id,
             "role": message.get("role", "user"), "content": message, "seq": index}
            for index, message in enumerate(messages)
        ]).execute()
        imported += 1
    return imported
