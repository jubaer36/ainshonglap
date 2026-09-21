"""Planner regression set — question, expected category, expected coordinates.

The planner decides what every question becomes, so it is the easiest part of
the system to break silently: a misplan does not raise, it just answers the
wrong way. These cases are pinned so a prompt edit that helps one phrasing and
breaks another shows up as a number.

Four things are measured separately, because they fail separately:

  category    meta / structural / semantic / general
  scope       the act, chapter, sections and ranges named. A plan can land in
              the right category and still fetch the wrong chapter.
  clarify     asked for clarification exactly when the request cannot be
              executed as written — an ambiguous section number, a subjective
              selection. Over-asking is as wrong as guessing.
  invalid     a coordinate the catalog does not have is rejected, never
              silently swapped for a nearby one.

The first 27 cases are the ones that were pinned against the regex router, with
its six intents mapped onto the four categories (browse/summarize/lookup all
become `structural` — they differ by operation, not by machine). Keeping them
means the planner has to beat what it replaces on the same questions.

Run (from backend/, ainshonglap conda env active):

    python scripts/experiments/plan_cases.py
    python scripts/experiments/plan_cases.py --model super --sleep 3
    python scripts/experiments/plan_cases.py --only scope
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import time

BACKEND = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

# (category, question, expectations). Expectation keys, all optional:
#   act, chapter          exact match after validation
#   sections              exact list
#   range                 (from, to)
#   operation             fetch | enumerate | overview
#   clarify               True  = must ask; False = must NOT ask
#   note_contains         validation must have recorded this
CASES: list[tuple[str, str, dict]] = [
    # ---- general: not law at all. Answered plainly, no citations.
    ("general", "What is the capital of France?", {}),
    ("general", "Explain what a vector database is.", {}),
    ("general", "How do I make good tea?", {}),
    ("general", "What's 17 times 23?", {}),

    # ---- meta: about the assistant
    ("meta", "Who are you?", {}),
    ("meta", "Demonstrate your capabilities", {}),
    ("meta", "What can you do?", {}),
    ("meta", "hello", {}),
    ("meta", "Where do your answers come from?", {}),

    # ---- structural / enumerate: list provisions
    ("structural", "State the penal code laws", {"act": "penal"}),
    ("structural", "List the offences in the Penal Code", {"act": "penal"}),
    ("structural", "What sections are in the Evidence Act?", {"act": "evidence"}),
    ("structural", "State all the sections of the Penal Code", {"act": "penal"}),

    # ---- structural / overview: what a whole Act covers
    ("structural", "Summarize the Evidence Act",
     {"act": "evidence", "operation": "overview"}),
    ("structural", "Give me an overview of the Penal Code",
     {"act": "penal", "operation": "overview"}),
    ("structural", "What does the CrPC cover?",
     {"act": "crpc", "operation": "overview"}),
    ("structural", "What is the Evidence Act about?",
     {"act": "evidence", "operation": "overview"}),

    # ---- structural / fetch: the citation is given
    ("structural", "What does section 302 of the Penal Code say?",
     {"act": "penal", "sections": ["302"], "operation": "fetch"}),
    ("structural", "What does CrPC section 154 say?",
     {"act": "crpc", "sections": ["154"], "operation": "fetch"}),
    ("structural", "Show me Evidence Act s.25",
     {"act": "evidence", "sections": ["25"], "operation": "fetch"}),
    ("structural", "penal code 379",
     {"act": "penal", "sections": ["379"]}),

    # ---- semantic: a scenario whose provisions must be found
    ("semantic", "What if I steal in the middle of the night from a homeless person?", {}),
    ("semantic", "Someone broke into my shop at night and took my cash box.", {}),
    ("semantic", "Can the police arrest someone without a warrant?", {}),
    ("semantic", "Is a confession made to a police officer admissible?", {}),
    ("semantic", "My neighbour forged my signature on a document.", {}),
    ("semantic", "What is the punishment for theft?", {}),

    # ---- chapter named by NUMBER
    ("structural", "List all sections in Chapter XVI of the Penal Code",
     {"act": "penal", "chapter": "XVI"}),
    ("structural", "Show me chapter 16 of the penal code",
     {"act": "penal", "chapter": "XVI"}),
    ("structural", "what sections are in chapter 5 of the evidence act",
     {"act": "evidence", "chapter": "V"}),

    # ---- chapter named by SUBJECT. The regex router could not reach these:
    # no pattern can know that "documentary evidence" is a chapter title.
    ("structural", "show me the sections about documentary evidence",
     {"act": "evidence", "chapter": "V"}),
    ("structural", "state the laws from the murder chapter",
     {"act": "penal", "chapter": "XVI"}),
    ("structural", "list the chapter on offences against property",
     {"act": "penal", "chapter": "XVII"}),

    # ---- ranges
    ("structural", "List sections 378 to 382 of the Penal Code",
     {"act": "penal", "range": ("378", "382")}),
    ("structural", "show me penal code sections 299 through 302",
     {"act": "penal", "range": ("299", "302")}),

    # ---- ambiguity: must ask, never guess
    ("structural", "Show me section 25", {"clarify": True}),
    ("structural", "What does section 100 say?", {"clarify": True}),

    # ---- subjective selection: "important" depends on purpose
    ("structural", "List the most important Penal Code laws", {"clarify": True}),

    # ---- invalid coordinates: rejected, not silently replaced
    ("structural", "Show me Chapter XCIX of the Penal Code",
     {"chapter": None, "note_contains": "no chapter"}),
    ("structural", "What does Penal Code section 9999 say?",
     {"sections": [], "note_contains": "no section"}),

    # ---- breadth is not ambiguity: these must NOT ask for clarification
    ("semantic", "What are all the laws about theft?", {"clarify": False}),
    ("semantic", "Which provisions deal with police custody?", {"clarify": False}),

    # ---- scenarios: messy facts, no section named, several provisions engaged.
    # These are the questions the product is for, and they must reach `semantic`
    # however the facts are worded.
    ("semantic", "My brother and two friends beat a man outside a shop at "
                 "midnight. The man died in hospital. They were arrested "
                 "without a warrant. Was the arrest lawful?", {"clarify": False}),
    ("semantic", "A police officer recorded my confession at the station. "
                 "Can it be used against me at trial?", {"clarify": False}),
    ("semantic", "A shopkeeper refused to return my deposit and threatened me "
                 "when I asked for it.", {"clarify": False}),

    # ---- out_of_scope: a LEGAL question about law this corpus does not hold.
    # Kept apart from `general` because the failure is asymmetric: answering
    # "what does the Dowry Prohibition Act say" from memory is an ungrounded
    # legal answer, which is the E1 failure. Answering "capital of France" from
    # memory is fine.
    ("out_of_scope", "What does the Dowry Prohibition Act say about demanding dowry?", {}),
    ("out_of_scope", "What is the punishment under the Digital Security Act "
                     "for defamation online?", {}),
    ("out_of_scope", "My employer has not paid me for three months. "
                     "What are my rights?", {}),
    ("out_of_scope", "How do I file for divorce?", {}),
]


def check(plan, want: dict) -> list[str]:
    """Every expectation that the plan failed to meet."""
    bad: list[str] = []
    if "act" in want and plan.act != want["act"]:
        bad.append(f"act={plan.act!r} want {want['act']!r}")
    if "chapter" in want and plan.chapter != want["chapter"]:
        bad.append(f"chapter={plan.chapter!r} want {want['chapter']!r}")
    if "sections" in want and plan.sections != want["sections"]:
        bad.append(f"sections={plan.sections} want {want['sections']}")
    if "operation" in want and plan.operation != want["operation"]:
        bad.append(f"op={plan.operation!r} want {want['operation']!r}")
    if "range" in want:
        got = (plan.section_from, plan.section_to)
        # A range may legitimately arrive as an enumerated list instead.
        lo, hi = want["range"]
        listed = plan.sections and plan.sections[0] == lo and plan.sections[-1] == hi
        if got != want["range"] and not listed:
            bad.append(f"range={got} want {want['range']}")
    if "clarify" in want and bool(plan.needs_clarification) != want["clarify"]:
        bad.append(f"clarify={plan.needs_clarification} want {want['clarify']}")
    if "note_contains" in want:
        if not any(want["note_contains"] in n for n in plan.notes):
            bad.append(f"notes={plan.notes} want one containing "
                       f"{want['note_contains']!r}")
    return bad


def preview(plan, n: int) -> list[str]:
    """Execute a structural plan against the catalog and describe the result.

    This is the deterministic half of the system, end to end: the model named
    coordinates, and these are the records those coordinates actually select.
    Nothing is ranked and nothing is generated, so what prints here is what a
    reader would be shown.

    The other three categories have no executor yet (stage 3), so they say so
    rather than printing a plausible-looking blank.
    """
    from app.rag import catalog

    if plan.needs_clarification:
        return [f"-> asks: {plan.clarification}"]
    if plan.category != "structural":
        return [f"-> {plan.category}: no executor yet"]

    if plan.operation == "overview" and plan.act:
        o = catalog.outline(plan.act)
        if not o:
            return ["-> no such act"]
        out = [f"-> {o['act']} — {o['sections']} sections in "
               f"{len(o['chapters'])} chapters"]
        for c in o["chapters"][:n]:
            out.append(f"     {c['number']:5} {c['title']} ({c['n_sections']})")
        return out

    total, rows = catalog.sections(
        act_slug=plan.act, chapter_number=plan.chapter,
        numbers=plan.sections or None, number_from=plan.section_from,
        number_to=plan.section_to, limit=n)
    if not total:
        return ["-> 0 sections (coordinates matched nothing)"]
    head = f"-> {total} section(s)"
    if plan.act:
        head += f" from {catalog.act(plan.act)['name']}"
    if plan.chapter:
        ch = catalog.chapter(plan.act, plan.chapter)
        head += f", Chapter {plan.chapter}" + (f" — {ch['title']}" if ch else "")
    out = [head]
    for r in rows:
        out.append(f"     s.{r['number']:6} {r['title'] or '(untitled)'}")
    if total > len(rows):
        out.append(f"     ... {total - len(rows)} more")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None, help="planner alias (default: config)")
    ap.add_argument("--sleep", type=float, default=1.5,
                    help="pause between calls; Groq's free tier limits input "
                         "tokens per minute")
    ap.add_argument("--only", default=None,
                    help="substring filter on the question")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--show", type=int, default=0, metavar="N",
                    help="also EXECUTE each structural plan against the catalog "
                         "and print the first N sections it fetches. Semantic, "
                         "meta and general plans have no executor yet, so they "
                         "print the plan only.")
    args = ap.parse_args()

    from app.rag import config
    from app.rag.plan import make_plan

    cases = [c for c in CASES
             if not args.only or args.only.lower() in c[1].lower()]
    model = args.model or config.PLANNER_MODEL
    print(f"planner: {model}   cases: {len(cases)}\n")

    cat_ok = scope_ok = scope_n = 0
    failures: list[str] = []
    errors = 0
    t0 = time.time()

    for want_cat, q, want in cases:
        plan = make_plan(q, model=args.model)
        if plan.error:
            errors += 1
        cat_hit = plan.category == want_cat
        cat_ok += cat_hit
        bad = check(plan, want)
        if want:
            scope_n += 1
            scope_ok += not bad

        mark = "ok  " if cat_hit and not bad else "FAIL"
        print(f"{mark} [{plan.category:10}] {q}")
        if args.verbose or not (cat_hit and not bad):
            detail = []
            if not cat_hit:
                detail.append(f"category want {want_cat!r}")
            detail += bad
            if plan.error:
                detail.append(f"error={plan.error[:90]}")
            if detail:
                print(f"       {'; '.join(detail)}")
            if args.verbose and plan.notes:
                print(f"       notes={plan.notes}")
        if args.show:
            for line in preview(plan, args.show):
                print(f"       {line}")
        if not (cat_hit and not bad):
            failures.append(q)
        time.sleep(args.sleep)

    n = len(cases)
    print(f"\ncategory  {cat_ok}/{n} ({100*cat_ok/n:.0f}%)")
    if scope_n:
        print(f"scope     {scope_ok}/{scope_n} ({100*scope_ok/scope_n:.0f}%)"
              "   (cases with expected coordinates)")
    if errors:
        print(f"errors    {errors} call(s) fell back — numbers above understate")
    print(f"elapsed   {time.time()-t0:.0f}s")
    if failures:
        print(f"\n{len(failures)} failing:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)


if __name__ == "__main__":
    main()
