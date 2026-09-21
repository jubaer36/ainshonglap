"""Image input — a photo of one page, read into text. Never an answer.

Same shape as `speech.py`, for the same reason: the image is turned into text,
the text is shown to the reader, and only then does it become an ordinary
question. A vision model that answered directly would be an ungrounded legal
answer with a picture attached.

**Strictly one page, strictly an image.** PDFs are rejected rather than
converted, and that is a product decision rather than a missing feature:

  * a PDF has a text layer, and `scripts/parse_law_pdf.py` reads it with page
    numbers, font sizes and column positions — everything a VLM throws away.
    Routing a PDF through a vision model would replace verified extraction with
    a generative guess.
  * multi-page input invites "summarise this Act", which is a different
    operation with a different failure mode, and nothing here paginates.
  * the honest use is the case with no text layer at all: a photograph of a
    page, a scan someone was handed.

**What a VLM does, and does not, guarantee.** It is not OCR. OCR extracts
characters and fails visibly; a VLM describes what it saw and fails *plausibly*
— `302A` becomes `302`, a proviso judged redundant disappears, and the result
still reads like clean statute. So the transcription is treated as a draft of a
question, never as a source. Nothing it produces is ever cited: if the reader
asks about the text, the answer is retrieved from the indexed Acts and verified
against them, exactly as a typed question would be.
"""

from __future__ import annotations

import base64
import os
import pathlib

API = "https://openrouter.ai/api/v1/chat/completions"
ENV_FILE = pathlib.Path(__file__).resolve().parents[2] / ".env.local"

# Confirmed image-capable through OpenRouter's models API.
MODEL = os.getenv("RAG_VISION_MODEL", "inclusionai/ling-3.0-flash-vl:free")
TIMEOUT = int(os.getenv("RAG_VISION_TIMEOUT", "180"))

# One page as a photo or screenshot. An allowlist rather than a blocklist: the
# question is what we can honestly read, not what we can refuse.
ALLOWED_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp"}
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
MAX_BYTES = int(os.getenv("RAG_VISION_MAX_MB", "5")) * 1024 * 1024

PROMPT = (
    "Transcribe ALL text in this image exactly as printed, in reading order.\n\n"
    "Rules:\n"
    "- Copy section numbers EXACTLY: '302A' is not '302', '99B' is not '99'.\n"
    "- Do not summarise, paraphrase, correct, modernise or reorder anything.\n"
    "- Keep every proviso, explanation, illustration and footnote marker.\n"
    "- Preserve line and paragraph breaks.\n"
    "- If a word is genuinely illegible, write [illegible] rather than guessing "
    "a plausible one.\n"
    "- Output only the transcription. No preamble, no commentary."
)


class VisionUnavailable(RuntimeError):
    """No API key, or the provider refused the request."""


class UnsupportedImage(ValueError):
    """Not a single-page image this can honestly read."""


def available() -> bool:
    from dotenv import load_dotenv

    load_dotenv(ENV_FILE, override=True)
    return bool(os.environ.get("OPENROUTER_API_KEY"))


def check(data: bytes, content_type: str | None, filename: str | None) -> str:
    """Validate and return the media type. Raises `UnsupportedImage`.

    Checked on **content** as well as on the declared type, because a browser
    will happily label anything, and a PDF renamed `.png` should be refused for
    what it is rather than sent to a model that will describe its first page.
    """
    if not data:
        raise UnsupportedImage("the file is empty")
    if len(data) > MAX_BYTES:
        raise UnsupportedImage(
            f"image is larger than {MAX_BYTES // (1024 * 1024)} MB")

    # Magic bytes first — the only part of this the sender does not control.
    if data[:5] == b"%PDF-":
        raise UnsupportedImage(
            "PDFs are not accepted here. A PDF has a text layer, and "
            "scripts/parse_law_pdf.py reads it with page numbers and layout — "
            "far more reliably than a vision model can. Send one page as an "
            "image, or ingest the PDF properly.")
    if data[:4] in (b"PK\x03\x04",) or data[:2] == b"\xd0\xcf":
        raise UnsupportedImage(
            "Office documents are not accepted. Send one page as an image.")

    sniffed = _sniff(data)
    if sniffed is None:
        raise UnsupportedImage(
            "that does not look like a PNG, JPEG or WebP image")

    declared = (content_type or "").split(";")[0].strip().lower()
    suffix = pathlib.Path(filename or "").suffix.lower()
    if declared and declared not in ALLOWED_TYPES:
        raise UnsupportedImage(f"unsupported type {declared!r}; send a PNG, JPEG or WebP")
    if suffix and suffix not in ALLOWED_EXTENSIONS:
        raise UnsupportedImage(f"unsupported file {suffix!r}; send a PNG, JPEG or WebP")
    return sniffed


def _sniff(data: bytes) -> str | None:
    """Media type from the bytes themselves."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def read_image(data: bytes, content_type: str | None = None,
               filename: str | None = None, *, model: str | None = None) -> dict:
    """One image -> {text, model, chars}. Raises rather than returning silence.

    The caller is expected to show `text` to the reader for confirmation before
    it becomes a question — see `POST /api/read-image`, which has no path to an
    answer.
    """
    import requests
    from dotenv import load_dotenv

    mime = check(data, content_type, filename)

    load_dotenv(ENV_FILE, override=True)
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise VisionUnavailable(
            "OPENROUTER_API_KEY not set (expected in backend/.env.local)")

    url = f"data:{mime};base64,{base64.b64encode(data).decode()}"
    body = {
        "model": model or MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": url}},
            ],
        }],
        "temperature": 0.0,
        "max_tokens": 8192,
    }

    try:
        resp = requests.post(
            API, json=body, timeout=TIMEOUT,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"})
    except requests.RequestException as exc:
        raise VisionUnavailable(f"openrouter unreachable: {type(exc).__name__}") from exc

    if resp.status_code != 200:
        raise VisionUnavailable(f"openrouter {resp.status_code}: {resp.text[:200]}")

    data_json = resp.json()
    choice = (data_json.get("choices") or [{}])[0]
    text = ((choice.get("message") or {}).get("content") or "").strip()
    if not text:
        raise VisionUnavailable(
            f"the model returned nothing (finish_reason={choice.get('finish_reason')})")
    return {"text": text, "model": body["model"], "chars": len(text)}
