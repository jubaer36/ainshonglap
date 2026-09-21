"""Page images kept with the session that used them.

An image read into a question is evidence, not a transient input. The
transcription that reaches the pipeline is a **vision model's reading** of the
page — generative, and wrong in ways that read as correct (`302A` quietly
becoming `302`). Storing the page alongside its reading is what makes that
checkable later; storing only the text would leave a reopened session asserting
things with nothing to verify them against.

Stored only for signed-in users. A guest's image is read and discarded with
everything else from their session, which is what they chose on the way in.

Files are private and served through short-lived signed URLs. A public bucket
would mean a permanent unauthenticated link to a photograph of somebody's legal
document.
"""

from __future__ import annotations

import logging
import uuid

from app.db.supabase import bucket, client

log = logging.getLogger(__name__)

SIGNED_URL_TTL = 3600

EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


def store(user_id: str, data: bytes, mime: str, *,
          conversation_id: str | None = None,
          transcript: str | None = None) -> dict | None:
    """Put one page image in the bucket and record it. Never raises.

    Returns None on failure: an image that could not be filed must not cost the
    reader the transcription they already have.

    The path is namespaced by user id. That is organisation rather than
    protection — the bucket is private and access goes through the API — but it
    keeps one person's files from being enumerable alongside another's if the
    bucket is ever browsed directly.
    """
    extension = EXTENSIONS.get(mime, "bin")
    path = f"{user_id}/{uuid.uuid4().hex}.{extension}"
    try:
        client().storage.from_(bucket()).upload(
            path, data, {"content-type": mime, "upsert": "false"})
    except Exception:
        log.exception("uploading the page image failed")
        return None

    row = {
        "user_id": user_id,
        "conversation_id": conversation_id,
        "path": path,
        "bytes": len(data),
        "mime": mime,
        "chars": len(transcript or "") or None,
        "transcript": transcript,
    }
    try:
        created = client().table("uploads").insert(row).execute()
        record = (created.data or [row])[0]
    except Exception as exc:
        from app.db.conversations import _missing_column

        if _missing_column(exc):
            # `db/migrations/001_session_fidelity.sql` has not been applied.
            # Keep the image and record what the original schema can hold:
            # losing the provenance columns is far better than throwing away the
            # page, which is the one thing that can check the transcription.
            log.warning("uploads.conversation_id/message_id/transcript missing "
                        "— storing without them. Apply "
                        "db/migrations/001_session_fidelity.sql.")
            try:
                minimal = {k: v for k, v in row.items()
                           if k not in ("conversation_id", "message_id", "transcript")}
                created = client().table("uploads").insert(minimal).execute()
                record = (created.data or [minimal])[0]
            except Exception:
                log.exception("recording the page image failed")
                _remove(path)
                return None
        else:
            # The file is in the bucket but unrecorded, which would orphan it.
            # Remove it rather than leave a file nothing points at.
            log.exception("recording the page image failed")
            _remove(path)
            return None

    record["url"] = signed_url(path)
    return record


def _remove(path: str) -> None:
    try:
        client().storage.from_(bucket()).remove([path])
    except Exception:
        log.warning("could not remove the orphaned upload %s", path)


def signed_url(path: str, ttl: int = SIGNED_URL_TTL) -> str | None:
    try:
        signed = client().storage.from_(bucket()).create_signed_url(path, ttl)
        return signed.get("signedURL") or signed.get("signedUrl")
    except Exception:
        return None
