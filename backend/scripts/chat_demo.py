"""Hold a multi-turn conversation against the running API and print how the
memory changes.  Run: python scripts/chat_demo.py
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
import time

import requests

# A scenario, two follow-ups that need it, then a hard topic switch that must
# NOT inherit any of it — the case that catches context poisoning.
TURNS = [
    "Three men beat a man outside a shop at midnight and he died in hospital.",
    "What if he had survived with only injuries instead?",
    "Was it lawful to arrest them without a warrant?",
    "What is the capital of France?",
]


def show(label: str, text: str, indent: str = "      ") -> None:
    if not text:
        return
    print(f"  {label}")
    print(textwrap.fill(text, 92, initial_indent=indent, subsequent_indent=indent))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("-q", "--question", action="append", default=None)
    ap.add_argument("--interactive", action="store_true")
    ap.add_argument("--gap", type=float, default=2.0)
    ap.add_argument("--model", default=None)
    args = ap.parse_args()

    memory = None
    turns = args.question or TURNS

    def ask(question: str) -> None:
        nonlocal memory
        body = {"question": question, "memory": memory}
        if args.model:
            body["model"] = args.model
        t0 = time.perf_counter()
        try:
            resp = requests.post(f"{args.api}/api/query", json=body, timeout=300)
        except requests.RequestException as exc:
            print(f"  ERROR unreachable: {exc}")
            return
        dt = time.perf_counter() - t0
        if resp.status_code != 200:
            print(f"  ERROR {resp.status_code}: {resp.text[:200]}")
            return
        data = resp.json()

        print(f"  kind      {data['kind']}   ({dt:.1f}s)")
        if data.get("resolved_question"):
            # Printed prominently: this is the question that was actually
            # answered, and it is not the one the user typed.
            show("RESOLVED  (executed instead of what was typed)",
                 data["resolved_question"])
        show("answer", data.get("heading", ""))
        cites = list((data.get("citations") or {}).keys())
        if cites:
            print(f"      cited: {', '.join(cites[:8])}")

        memory = data.get("memory")
        if memory:
            show(f"memory    (turn {memory.get('turns')})", memory.get("summary", ""))
            if memory.get("sections"):
                print(f"      sections kept: {', '.join(memory['sections'])}")
            if memory.get("error"):
                print(f"      memory error: {memory['error']}")
        print()

    if args.interactive:
        print("Conversation with memory. Empty line exits.\n")
        while True:
            try:
                q = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return
            if not q:
                return
            ask(q)
        return

    for i, q in enumerate(turns, 1):
        print("=" * 96)
        print(f"[{i}/{len(turns)}] You: {q}")
        print("=" * 96)
        ask(q)
        if i < len(turns):
            time.sleep(args.gap)


if __name__ == "__main__":
    main()
