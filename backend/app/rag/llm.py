"""One interface over the hosted generators used by the application.

Groq Qwen is the default because it is fast and enforces the JSON schema this
pipeline requires. Gemini and the measured schema-capable OpenRouter routes
remain available as fallbacks and experiment controls.

Everything returns parsed JSON against a supplied schema. Structured output is
not a formatting nicety here: it is what makes a missing citation a schema
violation instead of a prose judgement call. Note the limit, from
JSONSchemaBench — constrained decoding guarantees the shape, never the truth.
The gates in `verify.py` are what check the content.
"""

from __future__ import annotations

import functools
import json
import os
import pathlib
import time
from typing import Any, Protocol

from app.rag import keyring, quota, trace

DEFAULT_GEMINI = os.getenv("RAG_GEMINI_MODEL", "gemini-3.5-flash-lite")
DEFAULT_GROQ = os.getenv("RAG_GROQ_MODEL", "qwen/qwen3.8-27b")
DEFAULT_OPENROUTER = os.getenv("RAG_OPENROUTER_MODEL",
                               "liquid/lfm-2.5-2.6b:free")
DEFAULT_INCEPTION = os.getenv("RAG_INCEPTION_MODEL", "mercury-2.5")
ENV_FILE = pathlib.Path(__file__).resolve().parents[2] / ".env.local"


class LLM(Protocol):
    name: str

    def generate_json(self, system: str, prompt: str, schema: dict) -> dict: ...


class GeminiLLM:
    # Overridable so a second deployment key can drive a separate workload
    # without competing for the first one's quota — see rag/memory.py, which
    # runs a summarisation call on every turn.
    KEY_ENV = "GEMINI_API_KEY"

    def __init__(self, model: str = DEFAULT_GEMINI, temperature: float = 0.0,
                 key_env: str | None = None):
        from dotenv import load_dotenv
        from google import genai

        # .env.local sits next to app/, and the API may be started from anywhere
        load_dotenv(ENV_FILE, override=True)
        env = key_env or self.KEY_ENV
        key = os.environ.get(env)
        if not key:
            raise RuntimeError(f"{env} not set (expected in backend/.env.local)")
        self.name = model
        self.temperature = temperature
        self._client = genai.Client(api_key=key)

    def generate_json(self, system: str, prompt: str, schema: dict) -> dict:
        from google.genai import types

        resp = self._client.models.generate_content(
            model=self.name,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
                response_schema=schema,
                temperature=self.temperature,
            ),
        )
        return json.loads(resp.text)


class GroqLLM:
    """Groq's OpenAI-compatible endpoint with strict structured output.

    The three class attributes below are the whole of what makes this Groq
    rather than any other OpenAI-compatible provider, so a sibling only has to
    override them — see InceptionLLM.
    """

    API = "https://api.groq.com/openai/v1/chat/completions"
    KEY_ENV = "GROQ_API_KEY"
    LABEL = "groq"

    def __init__(self, model: str = DEFAULT_GROQ, temperature: float = 0.0,
                 max_tokens: int = 4096, timeout: int = 120):
        if not keyring.count(self.KEY_ENV):
            raise RuntimeError(
                f"{self.KEY_ENV} not set (expected in backend/.env.local)")
        self.name = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        # Which key actually answered. Read after the call, for the quota record
        # and the trace line — a rotation nobody can see is a rotation nobody
        # can debug.
        self.used_key: str | None = None

    @property
    def fleet(self) -> int:
        """How many keys this provider currently has configured."""
        return max(keyring.count(self.KEY_ENV), 1)

    def _post(self, body: dict, attempts: int = 4):
        """POST, rotating keys first and only then waiting.

        Groq's free tier limits input tokens per minute per key, and the planner
        runs on every question — a burst hits the ceiling and every call after it
        fails instantly. With one key the only remedy was to sleep on Groq's
        retry-after header, which turned a rate limit into latency.

        With several keys there is a better move available first: a 429 on key 1
        says nothing about key 2, so the request moves to the next key
        immediately and costs nothing. Sleeping is what happens after every key
        has refused, which is the only situation where waiting is the real
        answer. Keys already recorded as out of budget are tried last rather than
        skipped outright, since that record can be stale — it is a cache of a
        429, not a fact about the account.
        """
        import requests

        order = keyring.rotation(self.KEY_ENV)
        if not order:
            raise RuntimeError(
                f"{self.KEY_ENV} not set (expected in backend/.env.local)")
        fresh = [s for s in order if quota.slot_available(self.name, s.env)]
        spent = [s for s in order if not quota.slot_available(self.name, s.env)]
        order = fresh + spent

        response = None
        for i in range(attempts):
            for slot in order:
                self.used_key = slot.env
                response = requests.post(
                    self.API,
                    timeout=self.timeout,
                    headers={"Authorization": f"Bearer {slot.value}",
                             "Content-Type": "application/json"},
                    json=body,
                )
                if response.status_code != 429:
                    return response
                # This key is at its ceiling. Record it against (model, key) so
                # the next question starts elsewhere, then try the next key now.
                quota.mark_exhausted(self.name, response.text[:300],
                                     slot=slot.env, fleet=self.fleet)
                if len(order) > 1:
                    trace.step("key", f"{slot.env} 429 -> next key")
            if i == attempts - 1:
                break
            # Every key refused. Now waiting is the only thing left.
            raw = response.headers.get("retry-after") if response else None
            try:
                delay = float(raw) if raw else 2.0 * (2 ** i)
            except ValueError:
                delay = 2.0 * (2 ** i)
            trace.step("key", f"all {len(order)} keys at limit, waiting "
                              f"{min(delay, 30.0):.0f}s")
            time.sleep(min(delay, 30.0))
        return response

    def generate_json(self, system: str, prompt: str, schema: dict) -> dict:
        body = {
            "model": self.name,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": prompt}],
            "temperature": self.temperature,
            "max_completion_tokens": self.max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "answer", "strict": True,
                                "schema": _strict(schema)},
            },
        }
        response = self._post(body)
        if response.status_code != 200:
            detail = f"{self.LABEL} {response.status_code}: {response.text[:300]}"
            # A 429 that survived the backoff is a budget, not a blip. Record it
            # so the model picker can grey this route out and /api/query can
            # fall back instead of handing the caller a 502.
            if response.status_code == 429 or quota.looks_like_quota(detail):
                quota.mark_exhausted(self.name, detail, slot=self.used_key,
                                     fleet=self.fleet)
            raise RuntimeError(detail)
        quota.clear(self.name, slot=self.used_key)
        data = response.json()
        if data.get("error"):
            raise RuntimeError(f"{self.LABEL}: {json.dumps(data['error'])[:300]}")
        choice = (data.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content")
        if not (content or "").strip():
            raise RuntimeError(
                f"empty content from {self.name} "
                f"(finish_reason={choice.get('finish_reason')})")
        return json.loads(content)


class InceptionLLM(GroqLLM):
    """Inception's Mercury models — OpenAI-compatible, so the Groq client fits.

    Mercury is a diffusion language model rather than an autoregressive one.
    Measured here at 469 output tok/s on the schema probe against Groq Qwen's
    ~1.3s total, which is worth something on this pipeline specifically: the
    planner runs before anything else on every question, so its latency is paid
    even by questions that end in a refusal.

    Only the endpoint and the key differ, so everything else — strict schema,
    429 backoff, the finish_reason in the empty-response error — is inherited.
    """

    API = "https://api.inceptionlabs.ai/v1/chat/completions"
    KEY_ENV = "INCEPTION_API_KEY"
    LABEL = "inception"

    def __init__(self, model: str = DEFAULT_INCEPTION, **kw):
        super().__init__(model, **kw)


class OpenRouterLLM:
    """OpenAI-compatible REST endpoint, via `requests`.

    OpenRouter serves one model through several providers and only some of them
    enforce schemas, so support is a property of the route, not just the model.
    """

    API = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self, model: str = DEFAULT_OPENROUTER, temperature: float = 0.0,
                 max_tokens: int = 16384, timeout: int = 300,
                 reasoning_effort: str | None = None):
        from dotenv import load_dotenv

        load_dotenv(ENV_FILE, override=True)
        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise RuntimeError(
                "OPENROUTER_API_KEY not set (expected in backend/.env.local)")
        self.name = model
        self.temperature = temperature
        # Reasoning tokens come out of the same budget as the answer, so leave
        # enough room for the schema-constrained JSON to be emitted.
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.reasoning_effort = reasoning_effort
        self._key = key

    def generate_json(self, system: str, prompt: str, schema: dict) -> dict:
        import requests

        body = {
            "model": self.name,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": prompt}],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "answer", "strict": True,
                                "schema": _strict(schema)},
            },
        }
        # Restrict routing to providers that actually implement every parameter
        # sent, response_format included. OpenRouter reports a model's
        # `structured_outputs` as the union across its providers, so without this
        # a request can land on one that treats the schema as a hint and answers
        # in prose.
        body["provider"] = {"require_parameters": True}
        if self.reasoning_effort:
            body["reasoning"] = {"effort": self.reasoning_effort}
        r = requests.post(
            self.API, timeout=self.timeout,
            headers={"Authorization": f"Bearer {self._key}",
                     "Content-Type": "application/json",
                     "HTTP-Referer": "http://localhost:3000",
                     "X-Title": "AinShonglap"},
            json=body,
        )
        if r.status_code != 200:
            raise RuntimeError(f"openrouter {r.status_code}: {r.text[:300]}")
        data = r.json()
        # Upstream provider errors can arrive inside a 200 body.
        if "error" in data:
            raise RuntimeError(f"openrouter: {json.dumps(data['error'])[:300]}")
        choice = (data.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content")
        if not (content or "").strip():
            # Distinguish "ran out of budget while reasoning" from "refused to
            # answer" — they look identical at the call site and need opposite
            # responses. `finish_reason` and the usage breakdown say which.
            usage = data.get("usage") or {}
            reasoning = (usage.get("completion_tokens_details") or {}).get(
                "reasoning_tokens")
            raise RuntimeError(
                f"empty content from {self.name} "
                f"(finish_reason={choice.get('finish_reason')}, "
                f"completion_tokens={usage.get('completion_tokens')}, "
                f"reasoning_tokens={reasoning}, max_tokens={self.max_tokens})")
        return _loads_lenient(content)


def _strict(schema: dict) -> dict:
    """Add strict mode's `additionalProperties: false` recursively."""
    if schema.get("type") == "object":
        schema = {**schema, "additionalProperties": False,
                  "properties": {k: _strict(v)
                                 for k, v in schema.get("properties", {}).items()}}
    elif schema.get("type") == "array" and "items" in schema:
        schema = {**schema, "items": _strict(schema["items"])}
    return schema


def _loads_lenient(raw: str) -> dict:
    """Parse JSON that may be wrapped in prose or a fenced code block."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        return json.loads(raw[start : end + 1])
    raise ValueError(f"no JSON object in model output: {raw[:200]!r}")


@functools.lru_cache(maxsize=8)
def get_llm(backend: str | None = None, model: str | None = None) -> Any:
    """Resolve an alias, a `backend:model` pair, or a bare id into a client.

    Cached per (backend, model) so switching generators mid-session costs one
    construction, not one per request.
    """
    from app.rag import config

    key_env = config.generator_key_env(model)
    if model or backend is None:
        resolved_backend, resolved_model = config.resolve_generator(model)
        backend = backend or resolved_backend
        model = resolved_model
    if backend == "groq":
        return GroqLLM(model or DEFAULT_GROQ)
    if backend == "openrouter":
        return OpenRouterLLM(model or DEFAULT_OPENROUTER)
    if backend == "gemini":
        return GeminiLLM(model or DEFAULT_GEMINI, key_env=key_env)
    if backend == "inception":
        return InceptionLLM(model or DEFAULT_INCEPTION)
    raise ValueError(
        f"unknown LLM backend {backend!r} "
        "(expected groq, openrouter, gemini or inception)")
