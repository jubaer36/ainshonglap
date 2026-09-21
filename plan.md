# Plan

## Objective

Build a source-grounded Bangladesh criminal-law assistant that can choose the
right operation for a question, retrieve or enumerate the relevant statutory
material, produce reviewable citations, and refuse unsupported legal claims.

## Completed foundations

1. **Canonical working corpus:** three main Acts selected under `Laws/main/`.
2. **Hierarchical parsing:** PDF -> Act/Part/Chapter/Section/Subsection JSON.
3. **Independent verification:** parsed text checked against PDFs and the bdlaws oracle.
4. **LLM-only baseline:** local Qwen and Gemini established the ungrounded failure mode.
5. **Verified chunking:** 1,321 section chunks from 1,247 sections.
6. **Measured retrieval:** BGE-M3 + FAISS evaluated on 608 external questions.
7. **Chunking experiments:** prefix and chunk budget measured as flat (E2/E3).
8. **Cross-encoder reranking:** R@1 improved from 32.7% to 41.9% (E4).
9. **Servable shared pipeline:** API and evaluation call the same retrieval code.
10. **Dual-granularity index:** 1,321 section + 999 child vectors; live R@20 71.4%.
11. **Grounded generation:** structured claims, C2/C3 gates, refusal and server disclaimer.
12. **Real API and frontend:** query, retrieval, models, corpus browsing and citation preview.
13. **Hosted generator migration:** Groq Qwen is default; strict-schema production path verified.
14. **Initial multi-intent handling:** meta, lookup, browse, summarize, general and legal paths.

## Current focus: model-authored query planning

Two LLM calls, not one. A **planner** decides what the question is and names
where to look; an **answerer** writes from whatever the fetcher returned. Code
owns every fact in between. Full design: `query-routing-plan.md`.

```
voice ──► /api/transcribe ──► transcript shown to the reader, never auto-sent
                                   |
question + conversation memory ────┘
      |
      v
   PLANNER (strict JSON, coordinates only, never text or SQL)
      |  follow_up? -> rewrite into a standalone question
      |
      +-- meta          capability answer from the catalog manifest
      +-- structural    SQL by coordinates: section, range, chapter, Act
      +-- semantic      retrieve wide, rerank, keep everything above a
      |                 score threshold — not a fixed top-k
      +-- out_of_scope  a legal question outside these Acts: say so, cite nothing
      +-- general       plain answer, no citations, no grounding claimed
      |
      v
   ANSWERER (question + fetched evidence -> cited answer)
      |
      v
   answer + rolling summary (Gemini Flash Lite) -> client, returned next turn
```

Five categories replace the old six intents: browse, summarize and lookup are
all `structural`, differing by operation rather than by machine; `out_of_scope`
was split out of `general` because answering a question about a statute we do
not hold, from memory, is the failure E1 recorded.

### Phase 1 — structural catalog — **done (E12)**

- `Laws/main/catalog.sqlite` built deterministically from parsed JSON.
- Acts, chapters, sections, subsections, cross-references, FTS5 over titles/text.
- Exact/range/chapter queries through parameterized SQL in `app/rag/catalog.py`;
  a coordinate that does not exist returns empty, never the nearest match.
- `scripts/verify/verify_catalog.py` re-reads the parsed JSON: counts, byte-level
  text parity, statutory ordering, hierarchy, FTS parity, reference resolution,
  and a deterministic-rebuild digest.
- Found five parser continuation artifacts, a missing `crpc-s99A`, and 60
  cross-Act references the build refuses to guess at.

### Phase 2 — planner and validator — **done (E13)**

- One strict Groq JSON schema for category, operation, scope and clarification.
- The model decides the route and may not answer, quote law or produce SQL.
- Every Act/chapter/section validated against the catalog before execution.
- Two-tier prompt: 778 tokens standard, chapter listing fetched only when a
  chapter is described by subject rather than numbered.
- 42/42 category and 25/27 → 27/27 scope on 41 pinned cases, of which the first
  27 are those previously pinned against the regex router.
- **Those numbers were measured on Groq Qwen.** The planner now runs on Gemini
  3.5 Flash Lite (E16, 1.3-1.8s against 43.3s), and `plan_cases.py` has not been
  re-run since. Re-measuring is the first item in phase 7.

### Phase 3 — validated executors — **done**

- `meta`: capability response from live catalog/index metadata.
- `structural / fetch`: exact Act/section fetch, ambiguity handled.
- `structural / enumerate`: ordered Act/chapter/range enumeration, paginated.
- `structural / overview`: grounded prose from the complete hierarchy, not top-k.
- `semantic`: retrieval, reranking, generation, C2/C3 gates. This is the
  scenario path — messy facts, no section named, several provisions engaged.
- `out_of_scope`: say the corpus does not cover it, name what it does cover,
  cite nothing.
- `general`: clearly labelled ungrounded answer.
- Retire `refine.classify_pattern` and the intent regexes once these are live.
  (Still present, reachable only via `RAG_PLAN=0` as an outage switch.)

**`act` is a hint on a semantic plan, never a filter.** On the scenario "they
beat a man, he died, they were arrested without a warrant", the planner returns
`act=crpc` — correct for the arrest, but the death engages Penal Code culpable
homicide. Hard-filtering retrieval to the named Act would drop exactly the
provisions the question needs. Structural plans filter by Act because the reader
named it; semantic plans must not.

### Phase 4 — threshold instead of top-k — **next, and the biggest gap**

`RAG_FINAL_K=5` stops being an answer-shaping constant. Relevance decides how
many provisions are shown; the prompt budget separately decides how many reach
the generator.

- Retrieve wide (dense k≈50), collapse children to sections, rerank all.
- Cut on a calibrated score, not a rank: sigmoid the reranker logit, then apply
  an absolute floor **and** a relative floor against the top hit. Absolute alone
  swings wildly between easy and hard queries; relative alone dumps thirty
  sections when one obviously wins.
- Fit the floor on the 608-question set — where gold sections score against
  everything else. Publish the curve, and label the number **unvalidated for
  multi-section recall**: one gold section per question cannot tell us whether
  the seventh result belonged.
- **At most 10 sections reach the generator.** Everything above the threshold is
  still shown to the reader as a deterministic list of catalog records, which
  needs no model. When the cap binds, say "showing 10 of 41", never truncate
  silently.
- Expand explicit cross-references one hop, marking why each was included.
- Never claim literal completeness; report the threshold and truncation state.

Two scenario runs have now failed on exactly this. "Three men beat a man
outside a shop at midnight and he died in hospital" retrieved `penal-s323/324/326`
— *hurt* sections — and never reached s.299/300/302/304. The generator refused,
correctly, on what it was handed. The cause is not only that five is too few: a
scenario with two legal issues (fatal assault, warrantless arrest) collapses into
one embedding query and the dominant wording takes every slot. Facets — one
query per issue, merged — are as necessary as the threshold.

The threshold is also the **abstention** mechanism, and the harder half of
"say you don't know". `out_of_scope` catches a question that names a statute we
do not hold, which is easy because the planner can read the name. It cannot
catch a question that *sounds* like our Acts but whose answer is not in them:
retrieval returns five plausible adjacent sections and the generator writes a
confident wrong answer. Nothing above the threshold must mean "not found here",
not "here are the five closest things". E10 fitted such a curve and measured
AUC 0.727 — weak, which is why no default was set then; refitting against
per-section relevance rather than per-question answerability is part of this
phase.

### Phase 5 — clarification as a visible answer — **done**

The planner already decides when a request cannot be executed as written — an
ambiguous section number, a subjective selection like "the most important laws"
— and the validator already turns unresolvable coordinates into a question. None
of it is surfaced yet: there is no `clarification` answer kind and no UI for it.

- `kind="clarify"` renders in blue as a question, distinct from a refusal.
- Over-asking remains a measured failure: breadth is what `semantic` is for, and
  `plan_cases.py` pins two cases that must NOT ask.

### Phase 6 — conversation memory — **done, differently than designed**

Built as a **rolling summary** rather than typed scope. After each turn the
previous summary plus the new question and answer go to Gemini 3.5 Flash Lite
and come back as one paragraph, which the next question is read against. The
planner decides `follow_up` explicitly (defaulting to false) and rewrites a
follow-up into a standalone question; everything downstream sees an ordinary
self-contained question.

Typed scope alone would not have worked: it handles "show that section" and
completely fails "what if he had survived", because a fact pattern is not a
coordinate. The summary carries facts; a code-written `sections` list carries
the citation keys the summariser is not allowed to paraphrase.

The UI half is done too: the running summary is inspectable from a `context · N
turns` chip, the resolved question is shown on any answer that used context, and
conversations persist in `localStorage` with their memory attached so reopening
one restores what the assistant knew.

Remaining here:
- Follow-up detection is **model-dependent** — Groq flagged "arrest *them*" as a
  follow-up and Mercury did not. Needs pinning in `plan_cases.py`.
- No regression set yet for multi-turn behaviour, including topic switches that
  must not inherit context.
- Summary drift over long conversations is unquantified. The mitigation —
  citation keys written by code, never by the summariser — guarantees section
  numbers only, not the prose around them.
- Add related-section groups, total counts and load-more behavior to the frontend.
- Keep exact citation previews backed by corpus keys.

### Phase 6b — accounts and session storage — **done**

Optional Supabase auth, with the storage choice made explicitly on the way in.

- **Guest stores nothing, anywhere.** Not on the server and not in the browser;
  the conversation lives in React state and dies with the tab. Enforced by the
  absence of a token, not by a client-side flag.
- **Signed in** stores everything needed to reload a session as it happened: the
  question and the whole answer as sent, the memory snapshot each answer was
  produced against, which model answered, what the planner decided, and page
  images with their transcriptions.
- The service-role key bypasses RLS, so `app/db/conversations.py` is the
  isolation boundary. Verified with two real users: one cannot list, read or
  delete the other's conversation while holding its exact uuid. Forged, expired,
  wrong-audience and `alg=none` tokens are all rejected.
- Works with Supabase absent, and with the session-fidelity migration
  unapplied — a missing column is detected and the turn is stored without its
  per-message snapshot rather than not stored at all.

Open here: retention. The current choice is to keep conversations indefinitely,
which is a decision rather than a default and should be revisited before this is
used by anyone but its author.

### Phase 7 — evaluation and rollout — **the standing gap**

Everything since phase 3 is built but unmeasured, and one published number has
gone stale: the planner's 42/42 describes Groq, while Gemini now does the
planning. Re-running `plan_cases.py` is cheap and comes first.

Everything since phase 3 is built but unmeasured. `plan_cases.py` covers
planning only (42/42 category, 27/27 scope); the executors, memory, voice input
and quota fallback have no pinned numbers at all. Voice has the sharpest missing
metric: **section-number accuracy**, not WER, because a 9% WER transcript that
turns 379 into 279 is a different offence.


- Router: intent, operation, scope and clarification accuracy.
- Focused retrieval: existing Recall@k/MRR/Act metrics.
- Comprehensive retrieval: multi-label precision, recall and F1.
- Generation: citation validity, claim entailment sample, refusal in both directions.
- Latency/token usage: planner, retrieval, generation and complete request.

## Parallel backlog

- Finish E8 against a shipping generator — the over-refusal it was meant to
  measure is now visible in ordinary use.
- Voice: measure section-number accuracy on real speech, and the cost of the
  Bangla/English language choice (`deepgram_stt.py --compare`).
- Free-tier budgets are a real constraint: Groq's daily token cap is per model
  and was exhausted once by verification runs. Prefer offline checks and
  `--only` over re-running whole suites.
- Parser gaps surfaced by the catalog verifier (E12): five continuation
  fragments re-detected as sections, and a missing `crpc-s99A`.
- Measure query rewriting; 28.6% of gold sections are never retrieved.
- Evaluate whether C4 entailment checking catches failures beyond quote fidelity.
- Parse the remaining in-scope Acts after deduplicating source PDFs.
- Attach parsed gloss titles to their subsections.
- Address 39 chunks over 512 estimated tokens.
- Source the missing CrPC Second Schedule.
- Add `AI_USAGE.md`, architecture diagram and deployment/demo instructions.

## Guardrails

- PDFs remain the content source; the oracle is validation-only.
- SQLite and FAISS are rebuildable read models, not new sources of law.
- The model never writes executable SQL or unvalidated statutory coordinates.
- Exact requests never silently degrade to nearest-neighbor retrieval.
- No retrieval/generation change ships without a pinned evaluation and before/after result.
