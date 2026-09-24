"""Show what the planner decides for each question, and what the catalog fetches.
Run: python scripts/plan_demo.py
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

QUESTIONS = [
    "Who are you?",
    "What is the capital of France?",
    "What does section 302 of the Penal Code say?",
    "show me the sections about documentary evidence",
    "state the laws from the murder chapter",
    "List all sections in Chapter XVI of the Penal Code",
    "Show me chapter 16 of the penal code",
    "Summarize the Evidence Act",
    "State the penal code laws",
    "List sections 378 to 382 of the Penal Code",
    "Show me section 25",
    "List the most important Penal Code laws",
    "Show me Chapter XCIX of the Penal Code",
    "Someone broke into my shop at night and took my cash box.",
    "What is the punishment for theft?",
    "What are all the laws about theft?",
    "My brother and two friends beat a man outside a shop at midnight. The man "
    "died in hospital. They were arrested without a warrant. Was the arrest lawful?",
    "What does the Dowry Prohibition Act say about demanding dowry?",
    "My employer has not paid me for three months. What are my rights?",
]

RULE = "=" * 78


def show_result(plan, limit: int) -> None:
    """Execute the plan's coordinates and print what came back."""
    from app.rag import catalog

    if plan.needs_clarification:
        print(f"  result  ASKS BACK: {plan.clarification}")
        return
    if plan.category == "out_of_scope":
        print("  result  [out_of_scope] would say the corpus does not cover "
              "this, name what it does cover, and cite nothing")
        return
    if plan.category != "structural":
        print(f"  result  [{plan.category}] no executor yet — stage 3 builds "
              "the semantic, meta and general answerers")
        return

    if plan.operation == "overview" and plan.act:
        o = catalog.outline(plan.act)
        if not o:
            print("  result  no such act")
            return
        print(f"  result  {o['act']} — {o['sections']} sections, "
              f"{len(o['chapters'])} chapters")
        for c in o["chapters"][:limit]:
            print(f"            {c['number']:5} {c['title']} "
                  f"({c['n_sections']} sections)")
        if len(o["chapters"]) > limit:
            print(f"            ... {len(o['chapters']) - limit} more chapters")
        return

    total, rows = catalog.sections(
        act_slug=plan.act, chapter_number=plan.chapter,
        numbers=plan.sections or None, number_from=plan.section_from,
        number_to=plan.section_to, limit=limit)
    if not total:
        print("  result  0 sections — the coordinates matched nothing "
              "(this is the intended outcome for a chapter that does not exist)")
        return
    head = f"{total} section(s)"
    if plan.act:
        head += f" of {catalog.act(plan.act)['name']}"
    if plan.chapter:
        ch = catalog.chapter(plan.act, plan.chapter)
        head += f", Chapter {plan.chapter}"
        if ch and ch["title"]:
            head += f" — {ch['title']}"
    print(f"  result  {head}")
    for r in rows:
        print(f"            s.{r['number']:6} {r['title'] or '(untitled)'}")
    if total > len(rows):
        print(f"            ... {total - len(rows)} more")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-q", "--question", action="append", default=None,
                    help="ask this instead of the built-in list; repeatable")
    ap.add_argument("--file", default=None,
                    help="a file of questions, one per line")
    ap.add_argument("--gap", type=float, default=10.0,
                    help="seconds between questions (default 10)")
    ap.add_argument("--model", default=None, help="planner alias")
    ap.add_argument("--limit", type=int, default=6,
                    help="how many fetched sections to print (default 6)")
    args = ap.parse_args()

    from app.rag import config
    from app.rag.plan import make_plan

    questions = args.question or QUESTIONS
    if args.file:
        questions = [ln.strip() for ln
                     in pathlib.Path(args.file).read_text().splitlines()
                     if ln.strip() and not ln.startswith("#")]

    print(f"planner: {args.model or config.PLANNER_MODEL}   "
          f"questions: {len(questions)}   gap: {args.gap:g}s\n")

    for i, q in enumerate(questions, 1):
        print(RULE)
        print(f"[{i}/{len(questions)}] {q}")
        print(RULE)

        t0 = time.time()
        plan = make_plan(q, model=args.model)
        dt = time.time() - t0

        if plan.error:
            print(f"  ERROR   {plan.error}")
            print(f"          fell back to: category={plan.category}")
        else:
            raw = {"category": plan.category, "operation": plan.operation,
                   "act": plan.act, "chapter": plan.chapter,
                   "sections": plan.sections,
                   "section_from": plan.section_from,
                   "section_to": plan.section_to,
                   "search_query": plan.search_query, "terms": plan.terms,
                   "needs_clarification": plan.needs_clarification,
                   "clarification": plan.clarification}
            print("  model   " + json.dumps(
                {k: v for k, v in raw.items() if v not in ("", [], None, False)},
                ensure_ascii=False))
            if plan.notes:
                # What validation changed, and why. An empty list means the
                # model's coordinates were taken exactly as given.
                for n in plan.notes:
                    print(f"  checked {n}")
            show_result(plan, args.limit)
        print(f"  ({dt:.1f}s)\n")

        if i < len(questions):
            time.sleep(args.gap)


if __name__ == "__main__":
    main()
