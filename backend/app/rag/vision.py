"""Image input: a photo of one page (max 3 MB) -> text, read by Groq
qwen3.8-27b. The text is shown to the user to check; it is never an answer and
never cited.
"""

from __future__ import annotations

import base64
import os
import pathlib

API = "https://api.groq.com/openai/v1/chat/completions"
ENV_FILE = pathlib.Path(__file__).resolve().parents[2] / ".env.local"

# Groq's qwen/qwen3.8-27b — its model record lists image input. Earlier
# OpenRouter defaults were withdrawn from the free tier (ling-3.0-flash-vl) or
# replaced. Runs on the shared GROQ_API_KEY* pool, in rotation.
MODEL = os.getenv("RAG_VISION_MODEL", "qwen/qwen3.8-27b")
# A reasoning model. Copying printed text needs no thinking, and thinking would
# spend the output budget, so it is switched off and any reasoning is hidden.
# Empty leaves both to the model.
REASONING_EFFORT = os.getenv("RAG_VISION_REASONING", "none")
TIMEOUT = int(os.getenv("RAG_VISION_TIMEOUT", "180"))

# One page as a photo or screenshot. An allowlist rather than a blocklist: the
# question is what we can honestly read, not what we can refuse.
ALLOWED_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp"}
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
# 3 MB, and not configurable upward: Groq refuses a base64 image over 4 MB, and
# base64 adds a third — so 3 MB is the largest file that can actually be read.
# Enforced in the browser, in the upload route (read no further than this), and
# again in `check`.
MAX_BYTES = min(int(os.getenv("RAG_VISION_MAX_MB", "3")), 3) * 1024 * 1024

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


class ImageTooLarge(UnsupportedImage):
    """Over MAX_BYTES — reported as 413 rather than 415."""


def available() -> bool:
    from dotenv import load_dotenv

    load_dotenv(ENV_FILE, override=True)
    return bool(os.environ.get("GROQ_API_KEY"))


def check(data: bytes, content_type: str | None, filename: str | None) -> str:
    """Validate and return the media type. Raises `UnsupportedImage`.

    Checked on **content** as well as on the declared type, because a browser
    will happily label anything, and a PDF renamed `.png` should be refused for
    what it is rather than sent to a model that will describe its first page.
    """
    if not data:
        raise UnsupportedImage("the file is empty")
    if len(data) > MAX_BYTES:
        raise ImageTooLarge(
            f"image is larger than {MAX_BYTES // (1024 * 1024)} MB. Send a smaller "
            f"photo or a screenshot of the page")

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

    from app.rag import keyring

    load_dotenv(ENV_FILE, override=True)
    slots = keyring.rotation("GROQ_API_KEY")
    if not slots:
        raise VisionUnavailable("GROQ_API_KEY not set (expected in backend/.env.local)")

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
    if REASONING_EFFORT:
        body["reasoning_effort"] = REASONING_EFFORT
        body["reasoning_format"] = "hidden"

    def post(slot):
        try:
            return requests.post(
                API, json=body, timeout=TIMEOUT,
                headers={"Authorization": f"Bearer {slot.value}",
                         "Content-Type": "application/json"})
        except requests.RequestException as exc:
            raise VisionUnavailable(f"groq unreachable: {type(exc).__name__}") from exc

    # Every GROQ_API_KEY*, in turn: a key at its rate or daily limit (429) moves
    # to the next. Anything else is the request or the model, and another key
    # would fail the same way.
    resp = None
    for slot in slots:
        resp = post(slot)
        if resp.status_code == 400 and "reasoning" in resp.text.lower() and \
                "reasoning_effort" in body:
            # The model does not take these settings: ask again without them.
            body.pop("reasoning_effort")
            body.pop("reasoning_format")
            resp = post(slot)
        if resp.status_code != 429:
            break

    if resp.status_code != 200:
        raise VisionUnavailable(f"groq {resp.status_code}: {resp.text[:200]}")

    data_json = resp.json()
    choice = (data_json.get("choices") or [{}])[0]
    text = ((choice.get("message") or {}).get("content") or "")
    # Some providers inline the thinking instead of separating it.
    import re
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    if not text:
        why = choice.get("finish_reason")
        hint = " — the reasoning used up the token budget" if why == "length" else ""
        raise VisionUnavailable(f"the model returned nothing (finish_reason={why}){hint}")
    return {"text": text, "model": body["model"], "chars": len(text)}
