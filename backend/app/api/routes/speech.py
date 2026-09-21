"""Voice input: audio in, a transcript out — deliberately not an answer."""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool

from app.models.schemas import Transcription
from app.rag.speech import SpeechUnavailable, transcribe

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
