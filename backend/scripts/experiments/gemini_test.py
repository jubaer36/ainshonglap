"""Batch smoke test for the Gemini 2.5 Flash baseline — same 5 questions as
local_llm_test.py, so results are directly comparable against the local
Qwen2.5-VL-3B baseline.

Run (from backend/, with the ainshonglap conda env active):

    python scripts/experiments/gemini_test.py
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
ENV_FILE = pathlib.Path(__file__).resolve().parents[2] / ".env.local"

MODEL_ID = "gemini-3.5-flash-lite"

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

TEST_QUESTIONS = [
    "What is the process for filing an FIR under the CrPC?",
    "What does Section 154 of the CrPC say?",
    "Can the police arrest someone without a warrant, and under what section?",
    "What is the punishment for murder under Bangladeshi law?",
    "Is a confession made to a police officer admissible as evidence?",
]


def main() -> None:
    load_dotenv(ENV_FILE, override=True)
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise SystemExit("GEMINI_API_KEY not set (expected in backend/.env.local)")

    client = genai.Client(api_key=api_key)

    for question in TEST_QUESTIONS:
        t0 = time.time()
        response = client.models.generate_content(
            model=MODEL_ID,
            contents=question,
            config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT),
        )
        elapsed = time.time() - t0

        print("=" * 80)
        print(f"Q: {question}")
        print(f"A ({elapsed:.1f}s): {response.text.strip()}")
        print()


if __name__ == "__main__":
    main()
