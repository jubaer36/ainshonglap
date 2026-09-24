"""Fifteen questions, one per planner path, printed step by step for a person
to judge.  Run: python scripts/check_questions.py --store supabase
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import textwrap
import time

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

# (expected category, question, what to look for when reading the answer)
QUESTIONS = [
    ("meta", "Who are you and what can you answer?",
     "describes the assistant and the three Acts; cites nothing"),
    ("meta", "Where do your answers come from?",
     "names the three Acts as the only source"),
    ("general", "What is 17 times 23?",
     "391; labelled as not from the Acts"),
    ("general", "Thanks, that was helpful!",
     "a short natural reply, no statute lookup at all"),
    ("out_of_scope", "What does the Dowry Prohibition Act say about demanding dowry?",
     "says that Act is not held; must NOT answer from memory"),
    ("out_of_scope", "My employer has not paid my salary for three months. What can I do?",
     "labour law — outside the three Acts; nothing cited"),
    ("structural", "What does section 302 of the Penal Code say?",
     "s.302 quoted verbatim — no model touched the text"),
    ("structural", "State the first five sections of the Evidence Act and the "
                   "Code of Criminal Procedure.",
     "BOTH Acts, sections 1-5 each (CrPC has a 4A, so 11 in all)"),
    ("structural", "List the sections in the chapter about documentary evidence.",
     "Evidence Act Chapter V, found by subject rather than number"),
    ("structural", "Summarise the Evidence Act.",
     "an overview built from the chapter outline, not from 5 random sections"),
    ("structural", "Show me section 25.",
     "asks WHICH Act — section 25 exists in all three"),
    ("semantic", "What is the punishment for theft?",
     "Penal Code s.379, every claim quoting its section"),
    ("semantic", "Three men beat a man outside a shop at midnight and he died in "
                 "hospital. They were arrested without a warrant. Was the arrest lawful?",
     "a two-issue scenario: arrest (CrPC s.54) AND the death (Penal Code) — "
     "the known weak spot"),
    ("semantic", "If I can't prove myself innocent in court, am I guilty?",
     "should correct the premise from Evidence ss.101-103; refused before (E17)"),
    ("document", "Case brief — Facts: The accused was arrested by police under "
                 "Section 54 of the Code of Criminal Procedure on suspicion alone "
                 "and detained for four days before being produced before a "
                 "Magistrate. Argument: the detention violated Section 167 because "
                 "remand was not sought within twenty-four hours. Held: the "
                 "detention beyond 24 hours without a Magistrate's order was "
                 "unlawful. Question: what do these sections actually require?",
     "reads the pasted brief; explains CrPC s.54 and s.167"),
]

RULE = "═" * 78
THIN = "─" * 78


def wrap(text: str, indent: str = "      ", width: int = 76) -> str:
    return "\n".join(textwrap.fill(line, width, initial_indent=indent,
                                   subsequent_indent=indent) if line.strip() else ""
                     for line in str(text).split("\n"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", type=int, nargs="+", help="question numbers, 1-15")
    ap.add_argument("--gap", type=float, default=6.0, help="seconds between questions")
    ap.add_argument("--model", default=None, help="generator alias")
    ap.add_argument("--list", action="store_true", help="print the questions and exit")
    args = ap.parse_args()

    if args.list:
        for i, (cat, q, look) in enumerate(QUESTIONS, 1):
            print(f"{i:2d}. [{cat}] {q[:90]}\n      look for: {look}")
        return

    os.environ.setdefault("RAG_TRACE", "1")

    from app.rag import config, plan as planmod
    from app.rag.execute import answer_question

    # Keep the planner's raw reply. `make_plan` validates it into a Plan and
    # the raw form is not kept anywhere else — but the difference between the
    # two is exactly what is worth seeing. Wrapped here, not in the app.
    captured: dict = {}
    original = planmod._ask_planner

    def recording(*a, **kw):
        raw, used = original(*a, **kw)
        captured["raw"], captured["used"] = raw, used
        return raw, used

    planmod._ask_planner = recording

    picked = [(i, *QUESTIONS[i - 1]) for i in (args.only or range(1, len(QUESTIONS) + 1))]
    print(f"store: supabase   planner: {config.PLANNER_MODEL}   "
          f"generator: {args.model or config.DEFAULT_GENERATOR}   "
          f"questions: {len(picked)}\n")

    summary = []
    for n, (i, expected, q, look) in enumerate(picked):
        captured.clear()
        print(RULE)
        print(f" Q{i}  [expected: {expected}]")
        print(wrap(q, indent=" "))
        print(f" look for: {look}")
        print(RULE)
        t0 = time.time()
        try:
            a = answer_question(q, model=args.model)
        except Exception as exc:
            print(f"\n  EXCEPTION  {type(exc).__name__}: {exc}\n")
            summary.append((i, expected, "ERROR", "-", True, time.time() - t0))
            continue
        dt = time.time() - t0
        p = a.plan

        print(f"\n ① PLANNER  ({captured.get('used', '?')})")
        raw = captured.get("raw")
        if raw is None:
            print("      (no model call — planner fell back or was skipped)")
        else:
            shown = {k: v for k, v in raw.items() if v not in ("", [], None, False, "none")}
            print(wrap(json.dumps(shown, ensure_ascii=False)))

        print("\n ② PLAN  (after validation)")
        if p is not None:
            mark = "✓" if p.category == expected else "✗ expected " + expected
            print(f"      category   {p.category}  {mark}")
            for label, val in (("operation", p.operation), ("acts", p.acts),
                               ("chapter", p.chapter), ("sections", p.sections),
                               ("range", f"{p.section_from}-{p.section_to}"
                                if p.section_from else None),
                               ("rewrite", p.search_query), ("terms", p.terms),
                               ("clarify", p.clarification if p.needs_clarification else None),
                               ("notes", p.notes)):
                if val:
                    print(f"      {label:10s} {val}")

        print("\n ③ DATA")
        if a.sources:
            for s in a.sources[:12]:
                score = f"rerank {s.score:6.2f}  dense {s.dense_score:.3f}" \
                        if getattr(s, "dense_score", None) else ""
                print(f"      {s.key:16s} {score}  {(s.section_title or '')[:38]}")
            if len(a.sources) > 12:
                print(f"      ... {len(a.sources) - 12} more")
        else:
            print("      (nothing fetched)")

        print(f"\n ④ ANSWER  kind={a.kind}  model={a.model or '-'}  {dt:.1f}s")
        if a.refused:
            print(f"      REFUSED — {a.reason}")
            if a.clarification:
                print(wrap(f"asks: {a.clarification}"))
        if a.summary:
            print(wrap(a.summary))
        for c in a.claims:
            sec = f"s.{c['section']}" if c.get("section") else "-"
            print(wrap(f"• {sec}  {c['text']}", indent="      "))
            if c.get("quote") and c["quote"] != c["text"]:
                print(wrap(f'quote: "{c["quote"][:220]}"', indent="          "))
        if a.dropped:
            print(f"      dropped by the citation gates ({len(a.dropped)}):")
            for d in a.dropped:
                print(f"        ✗ {d}")
        print()

        summary.append((i, expected, p.category if p else "-", a.kind, a.refused, dt))
        if n < len(picked) - 1:
            time.sleep(args.gap)

    print(THIN)
    print(f" {'#':>3}  {'expected':12s} {'planned':12s} {'kind':12s} {'refused':8s} time")
    for i, exp, got, kind, refused, dt in summary:
        flag = " " if exp == got else "✗"
        print(f" {i:3d}{flag} {exp:12s} {got:12s} {kind:12s} {str(refused):8s} {dt:4.1f}s")
    ok = sum(1 for _, e, g, *_ in summary if e == g)
    from app.rag import fallback

    used = fallback.active()
    print(f"\n planner category matched {ok}/{len(summary)}")
    print(" store: " + ("supabase — no fallback used" if not used else
                         f"FALLBACK USED for {', '.join(used)}: {next(iter(used.values()))['reason']}"))


if __name__ == "__main__":
    main()
