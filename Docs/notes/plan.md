# Plan

What is done and what comes next. Details: `progress.md` (what is built),
`experiments.md` (measurements).

## Goal

A Bangladesh criminal-law assistant that picks the right operation for each
question, answers from the statute text with checkable citations, and refuses
what the Acts do not support.

## Phases

| | phase | status |
|---|---|---|
| 1 | structural catalog (exact section / chapter / Act lookup) | done (E12) |
| 2 | planner + validator | done (E13) |
| 3 | one executor per question type | done |
| 4 | relevance threshold instead of a fixed top-k | **next — biggest gap** |
| 5 | clarifying questions as answers | done |
| 6 | conversation memory, accounts, stored sessions | done |
| 7 | evaluation beyond retrieval | **open** |
| 8 | corpus served from Supabase | done (E19) |
| 9 | sources added / removed from the app | done |
| 10 | three retrieval profiles | done (E21) |
| 11 | Docker image + GCP deployment | image done; deployment in `gcp-deployment-plan.md` |

## Next: phase 4

The answer path takes a fixed 8 sections. That fails two ways: a scenario with
two legal issues fills every slot with the dominant one, and a question with
no answer in the Acts still gets 8 "closest" sections.

- Split a scenario into one query per issue and merge the results.
- Keep sections above a fitted score (absolute and relative to the top hit),
  not a fixed count. Nothing above it means "not found here".
- Fit on the 608 questions; the fit cannot validate multi-section recall.

## Open: phase 7

Only retrieval has numbers. Not yet measured:

- planner accuracy on the current model (last measured on an older one — rerun
  `plan_cases.py` first);
- answer quality: citation validity, entailment, refusals in both directions;
- `FINAL_K=8` vs 5; follow-ups with the shorter memory summary;
- voice: section-number accuracy on real speech.

## Backlog

- Finish E8 (over-refusal) — E17 traced one case to the generator prompt.
- E21: run nemotron-embed + nemotron reranker on all 608 (stopped at 119 by the
  daily cap); check the models for contamination by the public test set.
- Re-measure retrieval whenever sources are added.
- Record each source's document type, so Docling sources are cited by passage.
- Source the missing CrPC Second Schedule.
- Fix E12 parser gaps (five fragments, missing `crpc-s99A`).
- Retire the old router (`refine.py`, `RAG_PLAN=0`).
- Stream pipeline stages to the UI instead of timed waiting messages.
- Retention policy for stored conversations.
- `AI_USAGE.md`, demo video, deployment link.

## Rules

- The PDFs are the only source of law; `bdlaws.jsonl` only validates.
- Supabase and the local files are rebuildable read models.
- The core Acts are never removed.
- Models never write SQL or unvalidated coordinates; exact requests never fall
  back to "nearest match".
- Nothing ships without a before/after number on the 608 questions (±3.7pp).
