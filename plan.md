# Plan

High-level status and what comes next. Design detail lives in
`query-routing-plan.md`; measurements and their configurations live in
`experiments.md`. Neither is repeated here.

## Objective

A source-grounded Bangladesh criminal-law assistant that picks the right
operation for a question, retrieves or enumerates the statutory material,
produces reviewable citations, and refuses unsupported legal claims.

## Foundations — done

Corpus, parsing, verification, chunking, retrieval, reranking, a shared servable
pipeline, grounded generation with citation gates, and an API and frontend over
all of it. Numbers and the negative results behind each choice: `progress.md`
for the summary, `experiments.md` for the runs.

## Architecture: plan, then answer

Two LLM calls, not one. A **planner** decides what the question is and names
where to look; an **answerer** writes from whatever the fetcher returned. Code
owns every fact in between — the model never emits statutory text or SQL.

```
voice ──► /api/transcribe ──► transcript shown to the reader, never auto-sent
                                   |
question + conversation memory ────┘
      |
      v
   PLANNER (strict JSON, coordinates only)
      |  follow_up? -> rewrite into a standalone question
      |
      +-- meta          capability answer from the catalog manifest
      +-- structural    SQL by coordinates: section, range, chapter, Act
      +-- semantic      retrieve wide, rerank, keep what clears a threshold
      +-- out_of_scope  a legal question outside these Acts: say so, cite nothing
      +-- general       plain answer, no citations, no grounding claimed
      |
      v
   ANSWERER (question + fetched evidence -> cited answer)
      |
      v
   answer + rolling summary -> client, returned next turn
```

Five categories replaced six intents: browse, summarize and lookup are all
`structural`, differing by operation. `out_of_scope` was split out of `general`
because answering from memory about a statute we do not hold is the E1 failure.

## Phases

| | | |
|---|---|---|
| 1 | structural catalog | **done** (E12) |
| 2 | planner and validator | **done** (E13) |
| 3 | validated executors | **done** |
| 4 | threshold instead of top-k | **next — the biggest gap** |
| 5 | clarification as a visible answer | **done** |
| 6 | conversation memory | **done**, as a rolling summary rather than typed scope |
| 6b | accounts and session storage | **done** |
| 7 | evaluation and rollout | **the standing gap** |
| 8 | corpus in Supabase (Postgres + pgvector) | **served** — Supabase primary, local files as a logged fallback; 15-question check next |

Two rules hold across every phase, each learned from a failure. **`act` filters
a structural plan but is only a hint on a semantic one** — a scenario planner
answers `act=crpc` for the arrest while the death is Penal Code, and filtering
would drop it. And **a structural fetch quotes rather than paraphrases**, so no
model can alter text the reader asked for by name.

## Phase 4 — threshold instead of top-k

`RAG_FINAL_K` stops being an answer-shaping constant. Relevance decides how many
provisions are shown; the prompt budget separately decides how many reach the
generator (cap 10, and say "showing 10 of 41" rather than truncating silently).

- Retrieve wide, collapse children to sections, rerank all.
- Cut on a calibrated score, not a rank: an absolute floor **and** a relative
  floor against the top hit. Absolute alone swings between easy and hard
  queries; relative alone dumps thirty sections when one obviously wins.
- Fit on the 608-question set and publish the curve, labelled **unvalidated for
  multi-section recall** — one gold section per question cannot say whether the
  seventh result belonged.

**Facets are as necessary as the threshold.** A scenario with two legal issues
collapses into one embedding query and the dominant wording takes every slot:
"three men beat a man and he died" retrieves *hurt* sections and never reaches
culpable homicide. One query per issue, merged.

**The threshold is also the abstention mechanism**, and the harder half of "say
you don't know". `out_of_scope` catches a question naming a statute we do not
hold — easy, the planner reads the name. It cannot catch a question that
*sounds* like our Acts but whose answer is not in them. Nothing above the
threshold must mean "not found here", not "here are the closest five". E10
fitted such a curve at AUC 0.727 — weak, which is why no default was set.

`FINAL_K` is currently 8, raised from 5 and **unmeasured**. E17 showed the raise
does not close the gap it was aimed at: the extra slots took noise, and the
section that would have answered the traced question sat outside the candidate
pool entirely. Raising k is not the fix — choosing *which query to embed* is.
E17 found the planner rewrite and the raw question retrieve near-disjoint sets
whose union contains the answer. Merging two retrievals is the cheapest thing to
try here.

## Phase 7 — what is built but not measured

`plan_cases.py` covers planning only (42/42 category, 27/27 scope), and **those
numbers were measured on Groq Qwen while two planners have served since** —
re-running it is cheap and comes first. E18 is the warning: the first evidence
the number had gone stale was a user hitting a misclassification, not the suite. The executors, memory, voice, Groq key rotation,
quota fallback and `FINAL_K=8` have no pinned numbers at all.

Voice has the sharpest missing metric: **section-number accuracy**, not WER,
because a 9% WER transcript that turns 379 into 279 is a different offence.

Still to measure: planner accuracy on the current model; multi-label precision
and recall for comprehensive retrieval; citation validity, claim entailment and
refusal in both directions; latency and token usage per stage.

## Phase 8 — the corpus in Supabase

Answers read `catalog.sqlite`; meaning search reads FAISS + `chunks.json`; the
side panels read `chunks.json` again. Three files, and two copies of the text.
Moving them into Postgres gives one copy, and turns "add an Act" from rebuild +
restart into a load — which is the brief's *incremental document-update
demonstration*.

It does not make search better or faster: 2,320 vectors in-process take 0.17ms,
and the models (embedding, reranking, generation) still run in the backend.

| step | what | gate |
|---|---|---|
| A | `db/migrations/002_corpus.sql` — `law_*` tables, `vector(1024)`, full-text, `law_match_chunks` | applies cleanly |
| B | `scripts/load_supabase.py` — per Act, from the local read models, no re-embedding | — |
| C | `scripts/verify/verify_supabase.py` — counts, text bytes, vectors, search parity | all pass |
| D | Supabase primary in `catalog.py`, `pipeline.py`, `library.py`; local only on failure, logged | outage simulated |
| E | same results from both stores | **17/17 catalog, 10/10 retrieval identical** |
| F | `scripts/check_questions.py` — 15 questions, every planner path | read by hand |

Rules: **exact vector search** (no HNSW — approximate search would move the
numbers); the small fixed tables cached at startup so planner validation makes
no per-check network call; the PDFs and parsed JSON stay the source of law.

## Backlog

- Finish E8 — the over-refusal it was meant to measure is now visible in
  ordinary use, and E17 traced one instance to the generator prompt.
- Drop planner search terms with no lexical support in the catalog (E17 found
  the planner emitting `presumption of innocence`, a phrase absent from all
  three Acts).
- Voice: section-number accuracy on real speech; cost of the Bangla/English
  language choice (`deepgram_stt.py --compare`).
- Parser gaps from E12: five continuation fragments re-detected as sections, and
  a missing `crpc-s99A`.
- Measure query rewriting — 28.6% of gold sections are never retrieved.
- Evaluate whether C4 entailment checking catches failures beyond quote fidelity.
- Parse the remaining in-scope Acts after deduplicating source PDFs.
- Attach parsed gloss titles to their subsections; address 39 oversized chunks.
- Source the missing CrPC Second Schedule.
- Stream stage events so the frontend's waiting messages become observed rather
  than timed.
- Retention policy for stored conversations — currently indefinite, which is a
  decision rather than a default.
- Add `AI_USAGE.md`, architecture diagram and deployment/demo instructions.

## Guardrails

- PDFs remain the content source; the bdlaws oracle is validation-only.
- SQLite and FAISS are rebuildable read models, not new sources of law.
- The model never writes executable SQL or unvalidated statutory coordinates.
- Exact requests never silently degrade to nearest-neighbour retrieval.
- Nothing ships without a pinned evaluation and a before/after result. At n=608
  the 95% confidence interval is ±3.7pp, so smaller gaps are not results.
