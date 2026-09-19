"""Interactive REPL for a model served through OpenRouter.

Same shape as gemini_chat.py and local_llm_chat.py so the three baselines are
directly comparable — same system prompt, same no-retrieval condition.

Uses `requests` against the REST endpoint rather than the OpenAI SDK: the API
is OpenAI-compatible, and one POST avoids adding a dependency for four lines of
work. Swap to the SDK if streaming or tool calls are ever needed.

Run (from backend/, with the ainshonglap conda env active):

    python scripts/openrouter_chat.py                 # REPL, default model
    python scripts/openrouter_chat.py --model lfm     # alias, or a full id
    python scripts/openrouter_chat.py --list          # known aliases
    python scripts/openrouter_chat.py --check --model all   # verify every route (free)
    python scripts/openrouter_chat.py --json --model gemma  # structured-output probe

Inside the REPL, `/model <alias>` switches without restarting and `/models`
lists them.

Reads OPENROUTER_API_KEY from backend/.env.local.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import time

import requests
from dotenv import load_dotenv

# backend/.env.local, resolved from this file so the script runs
# from any working directory rather than only from backend/.
ENV_FILE = pathlib.Path(__file__).resolve().parents[1] / ".env.local"

API = "https://openrouter.ai/api/v1"
TIMEOUT = 300

# Short aliases for the free routes under consideration, so switching model is
# `--model lfm` rather than pasting an id. Order is roughly small-to-large;
# `--list` prints it, and any full OpenRouter id still works in place of an alias.
MODELS = {
    "deepseek":  "deepseek/deepseek-v4-flash-0731:free",
    "lfm":       "liquid/lfm-2.5-2.6b:free",
    "qwen":      "qwen/qwen3.8-27b:free",
    "gemma":     "google/gemma-4-31b-it:free",
    "nemotron":  "nvidia/nemotron-3-ultra-550b-a55b:free",
    "super":     "nvidia/nemotron-3-super-120b-a12b:free",
    "lightning": "nvidia/nemotron-3.5-lightning:free",
    "ling":      "inclusionai/ling-3.0-flash-fin:free",
}
DEFAULT = "deepseek"
MODEL_ID = MODELS[DEFAULT]


def resolve_model(name: str) -> str:
    """Alias or full id -> full id. Unknown names pass through, so a route that
    is not in the table above can still be tried without editing this file."""
    return MODELS.get(name, name)

SYSTEM_PROMPT = (
    "You are AinShonglap, a legal information assistant for Bangladesh "
    "criminal law: the Code of Criminal Procedure, 1898 and related acts "
    "(Penal Code 1860, Evidence Act 1872, Police Act 1861, etc.). "
    "You are currently running with no document retrieval, answering "
    "from your own training knowledge only. Say plainly when you are "
    "unsure rather than inventing section numbers, case names, or "
    "quotations. State clearly that this is legal information, not "
    "legal advice."
)

# Minimal schema used by --json, mirroring the shape app/rag/answer.py needs:
# a refusal flag plus claims that each carry a section number and a quote.
# Worth probing per model — OpenRouter routes one model to several providers and
# only some of them enforce schemas, so support is a property of the route, not
# just the model.
PROBE_SCHEMA = {
    "name": "legal_answer",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "sufficient": {"type": "boolean"},
            "summary": {"type": "string"},
            "sections_cited": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["sufficient", "summary", "sections_cited"],
        "additionalProperties": False,
    },
}


def headers(key: str) -> dict:
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        # Optional, for OpenRouter's leaderboard attribution.
        "HTTP-Referer": "http://localhost:3000",
        "X-Title": "AinShonglap",
    }


def ask(key: str, model: str, question: str, schema: dict | None = None) -> tuple[str, dict]:
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ],
        "temperature": 0.0,
    }
    if schema:
        body["response_format"] = {"type": "json_schema", "json_schema": schema}
        # OpenRouter serves one model from several providers, and the model's
        # advertised `structured_outputs` is the union across them. Without this,
        # the request can land on a provider that treats the schema as a hint and
        # returns prose — google/gemma-4-31b-it did exactly that. This restricts
        # routing to providers that actually support every parameter sent.
        body["provider"] = {"require_parameters": True}

    r = requests.post(f"{API}/chat/completions", headers=headers(key),
                      json=body, timeout=TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"{r.status_code}: {r.text[:400]}")
    data = r.json()
    # OpenRouter returns upstream provider errors inside a 200 body as well.
    if "error" in data:
        raise RuntimeError(json.dumps(data["error"])[:400])
    return data["choices"][0]["message"]["content"], data.get("usage", {})


def check(key: str, model: str) -> None:
    """Confirm the model id exists and report what the route supports."""
    r = requests.get(f"{API}/models", headers=headers(key), timeout=TIMEOUT)
    r.raise_for_status()
    models = r.json()["data"]
    hit = next((m for m in models if m["id"] == model), None)
    if not hit:
        print(f"model {model!r} NOT found on /models")
        near = [m["id"] for m in models if model.split("/")[-1][:8] in m["id"]][:10]
        if near:
            print("  closest ids:")
            for n in near:
                print(f"    {n}")
        return
    params = hit.get("supported_parameters") or []
    pricing = hit.get("pricing", {})
    print(f"model      {hit['id']}")
    print(f"context    {hit.get('context_length')}")
    print(f"pricing    prompt={pricing.get('prompt')} completion={pricing.get('completion')}")
    print(f"structured {'response_format' in params or 'structured_outputs' in params}"
          f"   (supported_parameters: {', '.join(params) or 'none listed'})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT,
                    help=f"alias or full id (aliases: {', '.join(MODELS)})")
    ap.add_argument("--list", action="store_true",
                    help="show the known aliases and exit")
    ap.add_argument("--check", action="store_true",
                    help="verify the model, or every known model with --model all")
    ap.add_argument("--json", action="store_true",
                    help="one structured-output probe against PROBE_SCHEMA, then exit")
    args = ap.parse_args()

    if args.list:
        for alias, mid in MODELS.items():
            print(f"  {alias:<10} {mid}")
        return

    load_dotenv(ENV_FILE, override=True)
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise SystemExit("OPENROUTER_API_KEY not set (expected in backend/.env.local)")

    model = resolve_model(args.model)

    if args.check:
        # --check costs nothing: /models is a GET, so checking all of them
        # before spending inference calls is free.
        for mid in (MODELS.values() if args.model == "all" else [model]):
            check(key, mid)
            print()
        return

    if args.json:
        q = "Is a confession made to a police officer admissible as evidence?"
        print(f"probing structured output on {model} ...\nQ: {q}")
        try:
            text, usage = ask(key, model, q, schema=PROBE_SCHEMA)
        except Exception as e:
            msg = str(e)
            # A 404 is the capability verdict: no provider for this route
            # implements the parameters sent. 429/503 are the provider being
            # busy and say nothing about whether it enforces schemas — reporting
            # them as "unsupported" would retire a usable model on a bad minute.
            if "404" in msg:
                print("ENFORCED?  NO — no provider for this route supports "
                      "response_format (404 under require_parameters)")
            elif any(c in msg for c in ("429", "503", "overloaded", "rate-limited")):
                print(f"ENFORCED?  unknown — provider unavailable right now, retry "
                      f"later: {msg[:160]}")
            else:
                print(f"ENFORCED?  unknown — request failed: {msg[:200]}")
            return
        # Three outcomes, and they need different responses, so name which it is
        # rather than leaving a JSONDecodeError to be interpreted.
        try:
            parsed = json.loads(text)
            print("ENFORCED?  yes — schema-valid JSON returned")
            print(json.dumps(parsed, indent=2)[:700])
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            if start != -1 and end > start:
                print("ENFORCED?  partly — JSON embedded in prose, needs the "
                      "lenient parser")
                print(text[:300])
            else:
                print("ENFORCED?  NO — schema ignored, prose returned. This route "
                      "cannot drive app/rag/answer.py, which requires per-claim "
                      "quotes in enforced JSON.")
                print(text[:300])
        print("\nusage:", usage)
        return

    print(f"Using {model} via OpenRouter. Ask away (empty line to quit).")
    print(f"  /model <alias|id>   switch model      /models   list aliases\n")
    while True:
        try:
            question = input(f"[{args.model}] Q: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not question or question.lower() in {"exit", "quit"}:
            break

        # Switching mid-session matters for comparing models: the same question
        # asked twice in a row is the comparison, and retyping it is friction.
        if question.startswith("/model"):
            parts = question.split(maxsplit=1)
            if len(parts) == 2:
                args.model = parts[1].strip()
                model = resolve_model(args.model)
                print(f"-> {model}\n")
            else:
                print(f"current: {model}\n")
            continue
        if question in {"/models", "/list"}:
            for alias, mid in MODELS.items():
                print(f"  {alias:<10} {mid}")
            print()
            continue

        t0 = time.time()
        try:
            text, usage = ask(key, model, question)
        except Exception as e:
            print(f"! {e}\n")
            continue
        tok = usage.get("total_tokens", "?")
        print(f"A ({time.time()-t0:.1f}s, {tok} tok): {text.strip()}\n")


if __name__ == "__main__":
    main()
