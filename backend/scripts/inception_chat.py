"""Chat with or probe Inception's Mercury models; --json checks schema support.
Run: python scripts/inception_chat.py --json
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
API = "https://api.inceptionlabs.ai/v1/chat/completions"
TIMEOUT = 120

# docs.inceptionlabs.ai/get-started/models. Edit 2 is a fill-in-the-middle code
# model with no structured-output support, so it is not listed: it could not
# serve either role in this pipeline.
MODELS = {
    "mercury": "mercury-2.5",      # 260K context, structured outputs + tools
    "mercury-2": "mercury-2",      # 128K context, previous generation
}
DEFAULT_MODEL = "mercury"

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


def ask(key: str, model: str, question: str, *, structured: bool = False,
        max_tokens: int = 4096) -> tuple[str, dict]:
    body: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ],
        "temperature": 0.0,
        "max_completion_tokens": max_tokens,
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
        raise RuntimeError(f"Inception {response.status_code}: {response.text[:500]}")

    data = response.json()
    if data.get("error"):
        raise RuntimeError(f"Inception: {json.dumps(data['error'])[:500]}")
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"Inception returned no choices: {json.dumps(data)[:500]}")
    choice = choices[0]
    content = (choice.get("message") or {}).get("content")
    if not (content or "").strip():
        # Names the finish reason: a length cutoff and a refusal are different
        # failures, and "empty response" alone sent us looking in the wrong
        # place once already (E11's reasoning-token exhaustion).
        raise RuntimeError(
            f"Inception returned empty content "
            f"(finish_reason={choice.get('finish_reason')})")
    return content, data.get("usage") or {}


def print_result(text: str, usage: dict, elapsed: float) -> None:
    total = usage.get("total_tokens", "?")
    prompt = usage.get("prompt_tokens", "?")
    completion = usage.get("completion_tokens", "?")
    rate = ""
    if isinstance(completion, int) and elapsed > 0:
        rate = f", {completion / elapsed:.0f} tok/s out"
    print(f"A ({elapsed:.2f}s; {total} tokens: {prompt} in, {completion} out{rate}):")
    print(text.strip())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"alias or Inception model id (aliases: {', '.join(MODELS)})",
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
            print(f"  {alias:<12} {model_id}")
        return

    load_dotenv(ENV_FILE, override=True)
    key = os.environ.get("INCEPTION_API_KEY")
    if not key:
        raise SystemExit("INCEPTION_API_KEY not set (expected in backend/.env.local)")

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
            raise SystemExit(
                f"FAILED: Inception returned non-JSON output: {text[:300]!r}") from exc
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

    print(f"Using {model} through Inception. Empty line exits.\n")
    while True:
        try:
            question = input("Q: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not question or question.lower() in {"exit", "quit"}:
            return
        started = time.perf_counter()
        try:
            text, usage = ask(key, model, question)
        except RuntimeError as exc:
            print(f"error: {exc}\n")
            continue
        print_result(text, usage, time.perf_counter() - started)
        print()


if __name__ == "__main__":
    main()
