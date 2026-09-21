"""Router regression set — one line per question, expected route beside it.

The router decides whether a question needs retrieval at all, and it is the
easiest part of the system to break silently: a misroute does not raise, it just
answers the wrong way. Small models have already flip-flopped on identical
phrasings between two prompt versions here, so the cases are pinned.

Five categories, each a different destination:

  general   not law at all -> answer normally, no retrieval, no citations
  browse    "list some laws" -> enumerate from the parsed table of contents
  summarize "what does this Act cover" -> describe it from its chapter structure
  lookup    "what does s.302 say" -> fetch that section directly; semantic
            search is the wrong tool when the citation is already given
  legal     a scenario needing a provision -> retrieve, generate, cite
  meta      about the assistant itself

Run (from backend/, ainshonglap conda env active):

    python scripts/experiments/router_cases.py            # route only, cheap
    python scripts/experiments/router_cases.py --answer   # full pipeline
    python scripts/experiments/router_cases.py --model nexmini
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import time

BACKEND = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

CASES: list[tuple[str, str]] = [
    # --- general: not law. Should be answered, not refused.
    ("general", "What is the capital of France?"),
    ("general", "Explain what a vector database is."),
    ("general", "How do I make good tea?"),
    ("general", "What's 17 times 23?"),

    # --- browse: enumerate provisions
    ("browse", "State the penal code laws"),
    ("browse", "State some laws"),
    ("browse", "List the offences in the Penal Code"),
    ("browse", "What sections are in the Evidence Act?"),
    ("browse", "State all the sections of the Penal Code"),

    # --- summarize: what a whole Act covers. Retrieval cannot answer this —
    # 556 sections is not a top-5 — so it is answered from parsed structure.
    ("summarize", "Summarize the Evidence Act"),
    ("summarize", "Give me an overview of the Penal Code"),
    ("summarize", "What does the CrPC cover?"),
    ("summarize", "What is the Evidence Act about?"),

    # --- lookup: the citation is given; fetch it
    ("lookup", "What does section 302 of the Penal Code say?"),
    ("lookup", "What does CrPC section 154 say?"),
    ("lookup", "Show me Evidence Act s.25"),
    ("lookup", "penal code 379"),

    # --- legal: a scenario that needs a provision found
    ("legal", "What if I steal in the middle of the night from a homeless person?"),
    ("legal", "Someone broke into my shop at night and took my cash box."),
    ("legal", "Can the police arrest someone without a warrant?"),
    ("legal", "Is a confession made to a police officer admissible?"),
    ("legal", "My neighbour forged my signature on a document."),

    # --- meta: about the assistant
    ("meta", "Who are you?"),
    ("meta", "Demonstrate your capabilities"),
    ("meta", "What can you do?"),
    ("meta", "hello"),
    ("meta", "Where do your answers come from?"),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None, help="router model alias")
    ap.add_argument("--answer", action="store_true",
                    help="run the full pipeline, not just routing")
    ap.add_argument("--only", help="run one category")
    args = ap.parse_args()

    from app.rag.refine import refine

    cases = [c for c in CASES if not args.only or c[0] == args.only]
    wrong = 0
    t0 = time.time()
    print(f"{'want':<9}{'got':<12}{'by':<9}{'':2}question")
    print("-" * 78)
    for want, q in cases:
        r = refine(q, model=args.model)
        got = r.intent
        ok = got == want
        wrong += not ok
        print(f"{want:<9}{got:<12}{r.by:<9}{'  ' if ok else ' X'}{q[:46]}")
        if r.failed:
            print(f"{'':32}! router error: {r.error}")
        elif args.answer and ok and want == "legal":
            print(f"{'':32}-> {r.query[:80]}")

    n = len(cases)
    print(f"\n{n - wrong}/{n} routed correctly   ({time.time()-t0:.0f}s)")
    if wrong:
        print("A misroute is silent in production: the question is answered the "
              "wrong way, not rejected.")


if __name__ == "__main__":
    main()
