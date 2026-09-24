"""Voice and image input: media in, text out — deliberately never an answer."""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, Header, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool

from app.models.schemas import ImageReading, Transcription
from app.rag.speech import SpeechUnavailable, transcribe
from app.rag.vision import (MAX_BYTES as IMAGE_MAX_BYTES, ImageTooLarge,
                             UnsupportedImage, VisionUnavailable, read_image)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["assistant"])

# Generous for a spoken question, small enough that a stray file upload cannot
# tie up the request. ~60s of Opus at 32 kbit/s is around 250 KB.
MAX_BYTES = 10 * 1024 * 1024


@router.post("/transcribe", response_model=Transcription)
async def transcribe_audio(
    audio: UploadFile = File(..., description="recorded audio; webm/opus, wav, mp3, m4a"),
    language: str | None = Query(None, description="en | bn (nova-3 only) | multi"),
) -> Transcription:
    """Transcribe a recording. **This does not answer the question.**

    The separation is the safety property, not an implementation detail. A
    misheard section number produces a perfectly grounded answer about the wrong
    offence: the planner names coordinates that exist, the catalog fetches them,
    the quote verifies against the section that was fetched. Every gate passes.
    Nothing downstream can detect it, because nothing downstream knows what was
    said.

    So the transcript goes back to the reader first, and they send it — or
    correct it — themselves. One extra click buys the only check that exists on
    this path.
    """
    raw = await audio.read()
    if not raw:
        raise HTTPException(422, "empty audio")
    if len(raw) > MAX_BYTES:
        raise HTTPException(413, f"audio larger than {MAX_BYTES // (1024*1024)} MB")

    try:
        # `transcribe` is a synchronous HTTPS call and would block the event
        # loop for the length of the upload plus Deepgram's processing. This
        # handler has to stay `async` because it awaits the upload, so the
        # blocking part is pushed to a worker thread explicitly.
        result = await run_in_threadpool(
            transcribe, raw, audio.content_type or "audio/webm",
            language=language)
    except SpeechUnavailable as exc:
        # 503, not 500: the rest of the assistant is fine and typing still works.
        log.warning("transcription unavailable: %s", exc)
        raise HTTPException(503, f"speech-to-text unavailable: {exc}") from exc
    except Exception as exc:                        # pragma: no cover - defensive
        log.exception("transcription failed")
        raise HTTPException(502, f"transcription failed: {type(exc).__name__}") from exc

    return Transcription(**result)


@router.post("/read-image", response_model=ImageReading)
async def read_image_endpoint(
    image: UploadFile = File(..., description="ONE page as PNG, JPEG or WebP"),
    conversation_id: str | None = Query(None),
    authorization: str | None = Header(None),
) -> ImageReading:
    """Read the text off **one page image**. This does not answer anything.

    Accepts a single PNG, JPEG or WebP, up to 3 MB (413 above that). **PDFs and documents are
    refused**, not converted: a PDF carries a text layer that
    `scripts/parse_law_pdf.py` reads with page numbers, fonts and column
    positions, and routing it through a vision model would swap verified
    extraction for a generative guess. The honest use of this endpoint is the
    case with no text layer at all — a photo of a page, or a scan.

    The transcription comes back for the reader to check and send. A vision
    model is not OCR: it fails plausibly rather than visibly, quietly turning
    `302A` into `302`, and nothing downstream could detect that. Nothing it
    produces is ever cited — an answer about this text is still retrieved from
    the indexed Acts and verified against them.
    """
    # Read one byte past the limit and no further: an oversized upload is
    # refused without holding the whole file in memory.
    raw = await image.read(IMAGE_MAX_BYTES + 1)
    if len(raw) > IMAGE_MAX_BYTES:
        raise HTTPException(413, f"image is larger than {IMAGE_MAX_BYTES // (1024 * 1024)} MB. "
                                 "Send a smaller photo or a screenshot of the page")
    try:
        result = await run_in_threadpool(
            read_image, raw, image.content_type, image.filename)
    except ImageTooLarge as exc:
        raise HTTPException(413, str(exc)) from exc
    except UnsupportedImage as exc:
        # 415, not 400: the request was well-formed, the media type is the
        # problem, and the message says exactly what to send instead.
        raise HTTPException(415, str(exc)) from exc
    except VisionUnavailable as exc:
        log.warning("image reading unavailable: %s", exc)
        raise HTTPException(503, f"image reading unavailable: {exc}") from exc
    except Exception as exc:                        # pragma: no cover - defensive
        log.exception("image reading failed")
        raise HTTPException(502, f"image reading failed: {type(exc).__name__}") from exc

    # Keep the page with the session that used it — but only for a signed-in
    # user. A guest's image is read and discarded with the rest of their
    # session, which is what they chose on the way in.
    stored = await run_in_threadpool(
        _store_image, authorization, raw, image.content_type,
        conversation_id, result.get("text"))
    if stored:
        result["upload_id"] = stored.get("id")
        result["url"] = stored.get("url")
    return ImageReading(**result)


def _store_image(authorization: str | None, data: bytes, content_type: str | None,
                 conversation_id: str | None, transcript: str | None) -> dict | None:
    """File the image, when there is a verified user to file it for."""
    from app.db import supabase

    if not supabase.configured():
        return None
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1]
    user = supabase.user_id(token)
    if not user:
        return None

    from app.db import uploads
    from app.rag.vision import _sniff

    return uploads.store(user, data, _sniff(data) or content_type or "image/png",
                         conversation_id=conversation_id, transcript=transcript)
