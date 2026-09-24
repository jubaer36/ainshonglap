"""Voice input: audio -> text with Deepgram. The text is shown to the user to
check; it is never sent as a question automatically.
"""

from __future__ import annotations

import functools
import os
import pathlib
import urllib.parse

API = "https://api.deepgram.com/v1/listen"
ENV_FILE = pathlib.Path(__file__).resolve().parents[2] / ".env.local"

MODEL = os.getenv("RAG_STT_MODEL", "nova-3")
LANGUAGE = os.getenv("RAG_STT_LANGUAGE", "en")
TIMEOUT = int(os.getenv("RAG_STT_TIMEOUT", "60"))

# Statutory vocabulary a general-purpose model has no reason to prefer. These
# are the words whose loss silently changes the question rather than degrading
# it visibly.
LEGAL_TERMS = [
    "cognizable", "non-cognizable", "bailable", "non-bailable", "compoundable",
    "First Information Report", "FIR", "magistrate", "sessions judge",
    "cognizance", "remand", "bail", "charge sheet", "summons", "warrant",
    "culpable homicide", "house-breaking", "criminal breach of trust",
    "wrongful restraint", "wrongful confinement", "abetment",
]


class SpeechUnavailable(RuntimeError):
    """No API key, or the provider refused the request."""


@functools.lru_cache(maxsize=1)
def keyterms(limit: int = 40) -> tuple[str, ...]:
    """Boost terms, built from the catalog so they cannot drift from the corpus.

    Deepgram recommends 20-50; the hard ceiling is 500 tokens per request.
    """
    terms = list(LEGAL_TERMS)
    try:
        from app.rag import catalog

        terms += [a["name"] for a in catalog.manifest()["acts"]]
    except Exception:            # catalog not built: the static list still works
        pass
    return tuple(terms[:limit])


def _key() -> str:
    from dotenv import load_dotenv

    load_dotenv(ENV_FILE, override=True)
    key = os.environ.get("DEEPGRAM_API_KEY")
    if not key:
        raise SpeechUnavailable(
            "DEEPGRAM_API_KEY not set (expected in backend/.env.local)")
    return key


def available() -> bool:
    """Whether voice input can work at all — for /health and the UI."""
    try:
        _key()
        return True
    except SpeechUnavailable:
        return False


def transcribe(audio: bytes, content_type: str = "audio/webm", *,
               language: str | None = None, model: str | None = None,
               boost: bool = True) -> dict:
    """Audio bytes -> {transcript, confidence, duration, ...}.

    Raises `SpeechUnavailable` rather than returning an empty transcript when
    the provider fails: "I heard nothing" and "the service is down" call for
    different responses, and an empty string would be sent on as a question.
    """
    import requests

    params: list[tuple[str, str]] = [
        ("model", model or MODEL),
        ("language", language or LANGUAGE),
        ("smart_format", "true"),
        ("punctuate", "true"),
    ]
    if boost:
        # Repeated parameters, not comma-separated: the API rejects the latter.
        params += [("keyterm", t) for t in keyterms()]

    try:
        resp = requests.post(
            f"{API}?{urllib.parse.urlencode(params)}",
            data=audio, timeout=TIMEOUT,
            headers={"Authorization": f"Token {_key()}",
                     "Content-Type": content_type or "application/octet-stream"},
        )
    except requests.RequestException as exc:
        raise SpeechUnavailable(f"deepgram unreachable: {type(exc).__name__}") from exc

    if resp.status_code != 200:
        raise SpeechUnavailable(f"deepgram {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    alt = (((data.get("results") or {}).get("channels") or [{}])[0]
           .get("alternatives") or [{}])[0]
    meta = data.get("metadata") or {}
    return {
        "transcript": (alt.get("transcript") or "").strip(),
        "confidence": alt.get("confidence"),
        "duration": meta.get("duration"),
        "model": (meta.get("models") or [None])[0],
        "language": language or LANGUAGE,
        "request_id": meta.get("request_id"),
    }
