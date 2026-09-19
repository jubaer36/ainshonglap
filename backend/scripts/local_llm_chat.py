"""Interactive REPL for the local LLM-only baseline — loads model once,
then lets you type questions and see answers until you quit.

Run (from backend/, with the ainshonglap conda env active):

    python experiments/local_llm_chat.py

Type a question and hit enter. Empty line / Ctrl-D / "exit" to quit.
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


def main() -> None:
    print(f"Loading {MODEL_ID} ...")
    t0 = time.time()
    processor = AutoProcessor.from_pretrained(MODEL_ID)
    model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
        MODEL_ID,
        dtype=torch.float16,
        device_map="cuda",
    )
    print(f"Loaded in {time.time() - t0:.1f}s. Ask away (empty line to quit).\n")

    while True:
        try:
            question = input("Q: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not question or question.lower() in {"exit", "quit"}:
            break

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
            generated, skip_special_tokens=True, clean_up_tokenization_spaces=False
        )[0]

        print(f"A ({elapsed:.1f}s): {answer.strip()}\n")


if __name__ == "__main__":
    main()
