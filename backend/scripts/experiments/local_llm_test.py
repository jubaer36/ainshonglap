"""Standalone smoke test for the local LLM-only baseline (step 4).

Loads Qwen2.5-VL-3B-Instruct from the local HF cache, asks it a handful of
Bangladesh criminal-law questions text-only (no image input), and prints
each answer for manual inspection. No retrieval, no backend wiring yet —
this is purely "can the model answer, and how well" before anything gets
plugged into the FastAPI route.

Run (from backend/, with the ainshonglap conda env active):

    python scripts/experiments/local_llm_test.py
"""

from __future__ import annotations

import time

import torch
from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration

MODEL_ID = "Qwen/Qwen2.5-VL-3B-Instruct"

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
    print(f"Loading {MODEL_ID} ...")
    t0 = time.time()
    processor = AutoProcessor.from_pretrained(MODEL_ID)
    model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
        MODEL_ID,
        dtype=torch.float16,
        device_map="cuda",
    )
    print(f"Loaded in {time.time() - t0:.1f}s\n")

    for question in TEST_QUESTIONS:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ]
        text = processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        inputs = processor(text=[text], return_tensors="pt").to(model.device)

        t0 = time.time()
        with torch.no_grad():
            output_ids = model.generate(**inputs, max_new_tokens=400)
        elapsed = time.time() - t0

        generated = output_ids[:, inputs["input_ids"].shape[1] :]
        answer = processor.batch_decode(
            generated, skip_special_tokens=True, clean_up_tokenization_spaces=True
        )[0]

        print("=" * 80)
        print(f"Q: {question}")
        print(f"A ({elapsed:.1f}s): {answer.strip()}")
        print()


if __name__ == "__main__":
    main()
