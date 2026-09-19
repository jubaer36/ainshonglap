"""Interactive REPL for the Gemini 2.5 Flash baseline (API-based, no local
weights). Same shape as local_llm_chat.py so results are comparable
side-by-side against the Qwen2.5-VL-3B local baseline.

Run (from backend/, with the ainshonglap conda env active):

    python scripts/gemini_chat.py

Type a question and hit enter. Empty line / Ctrl-D / "exit" to quit.
Reads GEMINI_API_KEY from backend/.env.local.
"""

from __future__ import annotations

import os
import pathlib
import time

from dotenv import load_dotenv
from google import genai
from google.genai import types

# backend/.env.local, resolved from this file so the script runs
# from any working directory rather than only from backend/.
ENV_FILE = pathlib.Path(__file__).resolve().parents[1] / ".env.local"

MODEL_ID = "gemini-3.5-flash"

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


def main() -> None:
    load_dotenv(ENV_FILE, override=True)
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise SystemExit("GEMINI_API_KEY not set (expected in backend/.env.local)")

    client = genai.Client(api_key=api_key)
    print(f"Using {MODEL_ID} via Google AI Studio. Ask away (empty line to quit).\n")

    while True:
        try:
            question = input("Q: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not question or question.lower() in {"exit", "quit"}:
            break

        t0 = time.time()
        response = client.models.generate_content(
            model=MODEL_ID,
            contents=question,
            config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT),
        )
        elapsed = time.time() - t0

        print(f"A ({elapsed:.1f}s): {response.text.strip()}\n")


if __name__ == "__main__":
    main()
