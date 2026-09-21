"""Interactive and one-shot smoke test for models served directly by Groq.

This deliberately tests Groq rather than Groq models routed through OpenRouter,
so latency, rate limits and structured-output support describe the provider the
application would actually call.

Run from backend/ (with the ainshonglap conda environment active):

    python scripts/groq_chat.py
    python scripts/groq_chat.py --prompt "What is the punishment for theft?"
    python scripts/groq_chat.py --json
    python scripts/groq_chat.py --list

Reads GROQ_API_KEY from backend/.env.local. The key is never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import time

import requests
from dotenv import load_dotenv

ENV_FILE = pathlib.Path(__file__).resolve().parents[1] / ".env.local"
API = "https://api.groq.com/openai/v1/chat/completions"
TIMEOUT = 120

MODELS = {
    "qwen": "qwen/qwen3.8-27b",
    "gpt-oss-20b": "openai/gpt-oss-20b",
    "gpt-oss-120b": "openai/gpt-oss-120b",
}
DEFAULT_MODEL = "qwen"

SYSTEM_PROMPT = (
    "You are AinShonglap, a legal information assistant for Bangladesh "
    "criminal law: the Code of Criminal Procedure, 1898, the Penal Code, "
    "1860, and the Evidence Act, 1872. This is a no-retrieval model probe. "
    "Say plainly when you are unsure rather than inventing section numbers or "
    "quotations. State that your response is legal information, not legal advice."
)

# A small version of the shape app/rag/answer.py requires. Passing this with
# strict=true distinguishes schema enforcement from a model merely attempting
# to follow JSON instructions in its prompt.
PROBE_SCHEMA = {
    "name": "legal_answer",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "sufficient": {"type": "boolean"},
            "summary": {"type": "string"},
            "sections_cited": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": ["sufficient", "summary", "sections_cited"],
        "additionalProperties": False,
    },
}


def resolve_model(name: str) -> str:
    return MODELS.get(name, name)


def ask(key: str, model: str, question: str, *, structured: bool = False) -> tuple[str, dict]:
    body: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ],
        "temperature": 0.0,
    }
    if structured:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": PROBE_SCHEMA,
        }

    response = requests.post(
        API,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Groq {response.status_code}: {response.text[:500]}")

    data = response.json()
    if data.get("error"):
        raise RuntimeError(f"Groq: {json.dumps(data['error'])[:500]}")
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"Groq returned no choices: {json.dumps(data)[:500]}")
    content = (choices[0].get("message") or {}).get("content")
    if not (content or "").strip():
        raise RuntimeError("Groq returned an empty response")
    return content, data.get("usage") or {}


def print_result(text: str, usage: dict, elapsed: float) -> None:
    total = usage.get("total_tokens", "?")
    prompt = usage.get("prompt_tokens", "?")
    completion = usage.get("completion_tokens", "?")
    print(f"A ({elapsed:.2f}s; {total} tokens: {prompt} in, {completion} out):")
    print(text.strip())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"alias or Groq model id (aliases: {', '.join(MODELS)})",
    )
    parser.add_argument("--list", action="store_true", help="list model aliases and exit")
    parser.add_argument("--prompt", help="send one question and exit")
    parser.add_argument(
        "--json",
        action="store_true",
        help="test strict JSON-schema output with a fixed legal question",
    )
    args = parser.parse_args()

    if args.list:
        for alias, model_id in MODELS.items():
            print(f"  {alias:<14} {model_id}")
        return

    load_dotenv(ENV_FILE, override=True)
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise SystemExit("GROQ_API_KEY not set (expected in backend/.env.local)")

    model = resolve_model(args.model)
    if args.json:
        question = "Is a confession made to a police officer admissible as evidence?"
        print(f"Testing strict structured output on {model}\nQ: {question}")
        started = time.perf_counter()
        text, usage = ask(key, model, question, structured=True)
        elapsed = time.perf_counter() - started
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"FAILED: Groq returned non-JSON output: {text[:300]!r}") from exc
        required = {"sufficient", "summary", "sections_cited"}
        if set(parsed) != required:
            raise SystemExit(
                f"FAILED: response keys {sorted(parsed)} do not match {sorted(required)}"
            )
        print("PASSED: strict schema-shaped JSON returned")
        print_result(json.dumps(parsed, indent=2), usage, elapsed)
        return

    if args.prompt:
        started = time.perf_counter()
        text, usage = ask(key, model, args.prompt)
        print_result(text, usage, time.perf_counter() - started)
        return

    print(f"Using {model} directly through Groq. Empty line exits.\n")
    while True:
        try:
            question = input("Q: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not question or question.lower() in {"exit", "quit"}:
            break
        started = time.perf_counter()
        try:
            text, usage = ask(key, model, question)
        except Exception as exc:  # keep the REPL alive after a transient 429
            print(f"! {exc}\n")
            continue
        print_result(text, usage, time.perf_counter() - started)
        print()


if __name__ == "__main__":
    main()
