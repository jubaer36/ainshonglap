"""Ask questions end to end — plan, execute, answer — and print the result.

`plan_demo.py` shows what the planner decided. This runs the whole path and
shows what a reader would actually get: the answer, its citations, and what was
dropped on the way.

Every answer prints its provenance, because the difference between kinds is the
point of the architecture:

    structural    quoted from the catalog. No model saw the text.
    semantic      retrieved, generated, then every claim checked — the cited
                  section must have been retrieved and the quote must appear in
                  it verbatim. Claims that fail are dropped and shown below.
    meta          read from the catalog manifest.
    out_of_scope  a legal question this corpus cannot answer. Nothing cited.
    general       the model's own knowledge, labelled as such.
    clarify       a question back.

The pause between questions is Groq's input-tokens-per-minute limit; a semantic
answer costs two calls (plan, then generate).

Run (from backend/, ainshonglap conda env active):

    python scripts/answer_demo.py                     # the built-in questions
    python scripts/answer_demo.py -q "your question"
    python scripts/answer_demo.py --gap 5 --file mine.txt
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import time

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

QUESTIONS = [
    "Who are you?",
    "What does section 302 of the Penal Code say?",
    "show me the sections about documentary evidence",
    "Summarize the Evidence Act",
    "What is the punishment for theft?",
    "My brother and two friends beat a man outside a shop at midnight. The man "
    "died in hospital. They were arrested without a warrant. Was the arrest lawful?",
    "Is a confession made to a police officer admissible?",
    "What does the Dowry Prohibition Act say about demanding dowry?",
    "What is the capital of France?",
    "Show me section 25",
]

RULE = "=" * 78
PROVENANCE = {
    "lookup": "quoted verbatim from the catalog — no model touched this text",
    "outline": "from the parsed document structure",
    "meta": "from the catalog manifest",
    "general": "the model's own knowledge — NOT from the Acts, not cited",
    "out_of_scope": "refused: legal, but outside the indexed Acts",
    "clarify": "asked back",
    "legal": "retrieved, generated, every claim citation-checked",
    "structural": "exact fetch",
}


def wrap(text: str, width: int = 74, indent: str = "    ") -> str:
    import textwrap
    out = []
    for para in str(text).split("\n"):
        out.append(textwrap.fill(para, width, initial_indent=indent,
                                 subsequent_indent=indent) if para.strip()
                   else "")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-q", "--question", action="append", default=None)
    ap.add_argument("--file", default=None)
    ap.add_argument("--gap", type=float, default=10.0)
    ap.add_argument("--model", default=None, help="answering model alias")
    ap.add_argument("--planner", default=None, help="planner model alias")
    ap.add_argument("--claims", type=int, default=8,
                    help="how many claims/listed sections to print")
    args = ap.parse_args()

    from app.rag import config
    from app.rag.execute import answer_question

    questions = args.question or QUESTIONS
    if args.file:
        questions = [ln.strip() for ln
                     in pathlib.Path(args.file).read_text().splitlines()
                     if ln.strip() and not ln.startswith("#")]

    print(f"planner: {args.planner or config.PLANNER_MODEL}   "
          f"generator: {args.model or config.DEFAULT_GENERATOR}   "
          f"questions: {len(questions)}\n")

    for i, q in enumerate(questions, 1):
        print(RULE)
        print(f"[{i}/{len(questions)}] {q}")
        print(RULE)
        t0 = time.time()
        try:
            a = answer_question(q, model=args.model, planner=args.planner)
        except Exception as exc:
            print(f"  EXCEPTION  {type(exc).__name__}: {exc}\n")
            time.sleep(args.gap)
            continue
        dt = time.time() - t0

        p = a.plan
        if p is not None:
            bits = [f"category={p.category}"]
            for k in ("operation", "act", "chapter"):
                if (v := getattr(p, k, None)):
                    bits.append(f"{k}={v}")
            if p.sections:
                bits.append(f"sections={p.sections}")
            if p.section_from:
                bits.append(f"range={p.section_from}-{p.section_to}")
            print("  plan    " + "  ".join(bits))
            if p.search_query:
                print(f"  rewrite {p.search_query!r}")

        print(f"  kind    {a.kind}   ({PROVENANCE.get(a.kind, '?')})")
        if a.refused:
            print(f"  REFUSED {a.reason}")
        if a.summary:
            print("  answer")
            print(wrap(a.summary))
        if a.claims:
            print(f"  claims  ({len(a.claims)})")
            for c in a.claims[:args.claims]:
                sec = f"s.{c['section']}" if c["section"] else "-"
                print(wrap(f"{sec}  {c['text']}", indent="    "))
                if c.get("quote") and c["quote"] != c["text"]:
                    print(wrap(f'"{c["quote"][:160]}"', indent="        "))
            if len(a.claims) > args.claims:
                print(f"    ... {len(a.claims) - args.claims} more")
        if a.dropped:
            # Claims the citation gates killed. Printed because a silent drop is
            # indistinguishable from the model never having made the claim.
            print(f"  dropped ({len(a.dropped)}) — failed citation checks")
            for d in a.dropped:
                print(f"    {d}")
        if a.sources:
            print("  sources " + ", ".join(
                f"{s.key} (score {s.score:.2f})" if s.score else s.key
                for s in a.sources))
        print(f"  ({dt:.1f}s)\n")

        if i < len(questions):
            time.sleep(args.gap)


if __name__ == "__main__":
    main()
