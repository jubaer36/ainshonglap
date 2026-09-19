"""One interface over the generator, so the model is a configuration choice.

Two backends. Gemini 2.5 Flash is the default — E1 scored it 5/5 with correct
citations where a local 3B model fabricated a quotation of CrPC s.154, and Mina
(arXiv 2511.08605) reports Gemini-2.5-Flash at 77-81.8% on the 2022-23
Bangladesh Bar Council exams, the closest published jurisdiction match there is.
The local backend exists because the brief allows self-hosting, and because
using the generator to check its own output is circular — a verifier should be
a different model.

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
from typing import Any, Protocol

DEFAULT_GEMINI = os.getenv("RAG_GEMINI_MODEL", "gemini-2.5-flash")
DEFAULT_OPENROUTER = os.getenv("RAG_OPENROUTER_MODEL",
                               "deepseek/deepseek-v4-flash-0731:free")
ENV_FILE = pathlib.Path(__file__).resolve().parents[2] / ".env.local"


class LLM(Protocol):
    name: str

    def generate_json(self, system: str, prompt: str, schema: dict) -> dict: ...


class GeminiLLM:
    def __init__(self, model: str = DEFAULT_GEMINI, temperature: float = 0.0):
        from dotenv import load_dotenv
        from google import genai

        # .env.local sits next to app/, and the API may be started from anywhere
        load_dotenv(ENV_FILE, override=True)
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY not set (expected in backend/.env.local)")
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


class OpenRouterLLM:
    """OpenAI-compatible REST endpoint, via `requests`.

    Verified on `deepseek/deepseek-v4-flash-0731:free`: the route advertises
    both `response_format` and `structured_outputs`, and returns schema-valid
    JSON, so the schema is enforced rather than merely suggested. That matters —
    OpenRouter serves one model through several providers and only some of them
    enforce schemas, so support is a property of the route, not of the model.

    It is a reasoning model: roughly three quarters of its completion tokens are
    reasoning, so `max_tokens` has to leave room for the thinking as well as the
    answer or the JSON arrives truncated.
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
        # Every free route tried here is reasoning-capable, and reasoning tokens
        # come out of the same budget as the answer. At 4096 roughly a fifth of
        # calls returned an empty string: the model had spent the whole budget
        # thinking and had nothing left to emit the JSON with. The ceiling is
        # generous rather than tuned — an unused ceiling costs nothing, a tight
        # one costs the call.
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
        # in prose — which is how google/gemma-4-31b-it returned an essay to a
        # schema-constrained call.
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
    """OpenRouter strict mode wants `additionalProperties: false` on every
    object. Ours are written without it, so add it rather than duplicating the
    schema per backend — Gemini ignores the key, so one schema serves both."""
    if schema.get("type") == "object":
        schema = {**schema, "additionalProperties": False,
                  "properties": {k: _strict(v)
                                 for k, v in schema.get("properties", {}).items()}}
    elif schema.get("type") == "array" and "items" in schema:
        schema = {**schema, "items": _strict(schema["items"])}
    return schema


class LocalLLM:
    """transformers-backed fallback. No constrained decoding, so the JSON is
    coaxed by prompt and parsed defensively — a local model that emits prose
    around its JSON is common, and losing the whole answer to that would be a
    harness failure rather than a model failure."""

    def __init__(self, model: str | None = None, temperature: float = 0.0):
        self.name = model or os.getenv("RAG_LOCAL_MODEL",
                                       "meta-llama/Llama-3.1-8B-Instruct")
        self.temperature = temperature
        self._tok = None
        self._model = None

    def _load(self):
        if self._model is None:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer

            from app.rag import config

            self._tok = AutoTokenizer.from_pretrained(self.name)
            self._model = AutoModelForCausalLM.from_pretrained(
                self.name, torch_dtype=torch.bfloat16
            ).to(config.device()).eval()
        return self._tok, self._model

    def generate_json(self, system: str, prompt: str, schema: dict) -> dict:
        import torch

        tok, model = self._load()
        msgs = [
            {"role": "system",
             "content": f"{system}\n\nReply with JSON matching this schema and "
                        f"nothing else:\n{json.dumps(schema)}"},
            {"role": "user", "content": prompt},
        ]
        text = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
        enc = tok(text, return_tensors="pt").to(model.device)
        with torch.no_grad():
            out = model.generate(**enc, max_new_tokens=1024,
                                 do_sample=self.temperature > 0,
                                 temperature=self.temperature or None,
                                 pad_token_id=tok.eos_token_id)
        raw = tok.decode(out[0][enc["input_ids"].shape[1]:], skip_special_tokens=True)
        return _loads_lenient(raw)


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
    backend = backend or os.getenv("RAG_LLM_BACKEND", "openrouter")
    if backend == "openrouter":
        return OpenRouterLLM(model or DEFAULT_OPENROUTER)
    if backend == "gemini":
        return GeminiLLM(model or DEFAULT_GEMINI)
    if backend == "local":
        return LocalLLM(model)
    raise ValueError(
        f"unknown LLM backend {backend!r} (expected openrouter, gemini or local)")
