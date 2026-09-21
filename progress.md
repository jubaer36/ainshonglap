# Progress

## Frontend
- Next.js chat UI, wired to a `POST /api/query` contract so UI and backend could be built independently.
- Renders structured answers (paragraphs, numbered steps, bullets) rather than flat text — legal answers like FIR filing steps need the structure.
- Citations render inline with a reference panel, so a claim traces back to its source without leaving the chat.
- TS types mirror the backend's Pydantic schemas by hand.

## Backend
- FastAPI service over the real pipeline: `POST /api/query`, `GET /api/retrieve`, `GET /api/acts`, `GET /api/sections[/{key}]`, `GET /health`. Contract and configuration in `backend/API.md`.
- The mock response module is gone; every endpoint serves the indexed corpus.
- Query handling now distinguishes `meta`, `lookup`, `browse`, `summarize`,
  `general` and `legal` intents. Exact lookups and statute outlines use the
  indexed corpus directly instead of forcing every request through top-k RAG.
- The current router is hybrid: obvious intents use patterns and ambiguous
  questions use a schema-constrained small model. This is an intermediate
  implementation; `query-routing-plan.md` specifies a model-authored plan with
  validated executors as the replacement.

## Legal documents
- 34 PDFs under `Laws/` — CrPC 1898 plus related criminal-law acts, scoped to the assessment brief. Provenance per file in `Laws/SOURCES.md`.
- Duplicates (CrPC ×3, 5 acts ×2) still on disk; not yet deduped.
- The 3 acts actually ingested live in `Laws/main/` and are tracked in git alongside their parsed output; the rest is gitignored.

## LLM-only baseline (step 4)
- Dedicated `ainshonglap` conda env; scripts for a local model (Qwen2.5-VL-3B on GPU) and a hosted one (Gemini 2.5 Flash), each with batch and REPL modes.
- Same 5 legal questions to both: Qwen hallucinated section content or citations on 3 of 5; Gemini got all 5 right with accurate citations.
- Conclusion: the generator is not the weak link — retrieval will be.

## PDF parsing (step 2)
- Evaluated 8 parsers by running each against the actual PDFs, not on reputation. Docling loses text; LandingAI ADE is capable but its elements straddle section boundaries; `bluebell` is an authoring format, not a parser. Full comparison in `parsing-plan.md`.
- Adopted PyMuPDF plus a rule-based legal-structure parser (`backend/scripts/parse_law_pdf.py`). Text and hierarchy both come from the PDF.
- Output: `Laws/main/parsed/<act>.json` — Act → Part → Chapter → Section → Subsection → Clause, plus Explanation/Illustration/Exception/Proviso, with page numbers and full ancestry inline on every section.
- Parsed CrPC (508 sections), Penal Code (557), Evidence Act (182). Deterministic — regenerating reproduces byte-identical files.
- Trailing endnote pages are excluded: the CrPC closes with 24 pages of amendment notes that were being appended to its last section, leaving s.565 at 10,918 words. Now 280. Verified nothing statutory was dropped — 1,218 of 1,221 removed lines are 7.6pt against a 9.0pt body, all past the final section, none a section opener.
- Hard cases solved: sections split across page boundaries (300 of them across the three acts), the Penal Code's gloss column swapping sides every page, and contents pages that mimic real sections.

## Verification (step 3)
- Found `Ashik2380/bdlaws` on HF — an independently scraped copy of all 1,467 Bangladesh acts. Used strictly as an oracle, never as a source: complete text, zero hierarchy.
- `backend/scripts/verify/verify_parsed.py` runs 7 checks, all by re-reading the PDF rather than trusting the parser's own stats. Method and results in `verification.md`.
- Results: **zero phantom sections in any act**, zero real numbering gaps, 100% line-level text recovery on Penal and Evidence (99.99% CrPC). Evidence Act matches its oracle exactly, 182 for 182, and passes every check.
- The suite earned its keep immediately — contents pages leaking into the body, gloss notes silently dropped when they matched no section, and two bugs in the checker itself. All invisible in the parser's own summary stats.
- It also showed the limit of coverage checking: s.565's 24 pages of endnotes counted as *recovered* because they landed somewhere. Coverage catches deletion; only size and shape checks catch misfiling.

## Chunking (step 5)
- Section-level chunking, split at subsection/clause boundaries when oversized, hierarchy path prepended to each chunk's text. Options and evidence in `chunking-plan.md`.
- 1,321 chunks from 1,247 sections: CrPC 536, Penal 583, Evidence 202. Median 133-172 tokens.
- `verify_chunks.py` checks the output against the parsed JSON independently — 3/3 acts pass all seven checks: text reproduced as an ordered word sequence, no duplication, no orphans, unique ids, metadata and prefix in agreement.
- It caught three bugs the chunker reported as clean, including duplicate chunk ids that would have silently overwritten entries in a vector store. The first fidelity check was itself wrong — a set comparison, which passes even when text is duplicated or scrambled.

## Retrieval (step 6-8)
- BGE-M3 embeddings + FAISS flat index over the 1,321 chunks; dense-only to start, per statute-specific evidence that hybrid BM25 does not reliably help here.
- Built an eval harness first: 608 questions from `bangladesh-legal-qa-dataset` with gold Act + section, scored on Recall@k, MRR and Act accuracy. Nothing is adopted without moving a number on it.
- Baseline: R@1 32.7%, R@20 68.4%, Act accuracy 82.2%. Checked the test was fair — only 2% of gold answers are absent from our corpus, and 9 of those 10 are mislabelled in the dataset itself.
- A/B'd the two assumptions baked into chunking (hierarchy prefix, chunk budget). **Both flat** — 3.0pp and 0.7pp spreads against a ±3.7pp confidence interval. The prefix was cut back to `Act, Section N: title`, saving 38 tokens a chunk for no measurable loss.
- Added `bge-reranker-v2-m3` over the dense top-20: **R@1 32.7% → 41.9%** (+9.2pp, ~4.8 standard errors) and Act accuracy 82.2% → 87.2%. First significant result.
- Full log with hypotheses, configs and decisions in `experiments.md`.

## Making it servable (step 9)
- Both best results existed only inside eval scripts: reranking lived in `experiments/rerank.py`, and dual-granularity indexing was written but the index had never been rebuilt with it. The logged 41.9% described a pipeline nothing could execute.
- Extracted `backend/app/rag/` so the API and the eval scripts call one `retrieve()`. Rebuilt the index dual-granularity: 2,320 vectors (1,321 section + 999 subsection).
- Measured the two together for the first time (E7): **they do not stack.** Reranked R@1 is 41.8% with dual granularity against 41.9% without — the reranker was already promoting the sections dual granularity pulls in. Kept anyway: it costs nothing at query time and lifts R@20 to 71.4%, the ceiling query rewriting has to work inside.
- Served numbers: R@1 41.8%, R@5 59.0%, R@20 71.4%, MRR 0.498, Act accuracy 87.5%.

## Generation (step 12)
- Options surveyed against published evidence in `generation-plan.md`. The design constraint is our own R@5: 41% of the time the correct section is absent from the context, replaced by plausible adjacent statute — the condition under which prompt-based abstention is reported to fail worst.
- `app/rag/`: Groq Qwen is the default generator, with schema-capable OpenRouter and Gemini fallbacks. JSON-schema output makes every claim carry a section number and a verbatim quote.
- Two deterministic gates run on every answer. **C2**: the cited section was actually retrieved, and where acts share a section number the act must match. **C3**: the quoted span appears verbatim in that section. Failing claims are dropped; if none survive the answer becomes a refusal.
- C3 is cheap here only because parsing is verified — the cited section's exact text is known, so a fabricated quotation cannot survive a string match. That is the E1 failure closed by construction rather than by prompting.
- The disclaimer is appended server-side from a constant on every path, including refusals. A disclaimer the model emits is one it can omit.
- Groq Qwen passed the strict-schema probe. OpenRouter fallback requests pin `provider.require_parameters` so a route that ignores JSON schemas fails loudly instead of returning prose.
- Direct Groq smoke tests returned in 0.95-1.31s, but ungrounded Qwen confused
  Evidence Act sections 25-27 with the CrPC and confused Penal Code sections
  378-380. The provider/model is fast and structurally compatible, not safe
  without retrieval and the citation gates. Recorded as E11.
- Bugs found and fixed while testing: C2 ignored the act entirely (all three acts have a s.25); the section regex matched inside the year, resolving "Penal Code, 1860, Section 379" to 186; reasoning tokens exhausted the output budget on ~20% of calls.
- **E8 (does the generator answer anyway when retrieval missed) is unfinished** — 21 of 40 calls spent before the run was interrupted. Partial: 5 of 16 valid responses answered rather than refused when the gold section was absent. n=16, so the interval is wide; treat as unmeasured.

## Structural catalog (E12)
- `Laws/main/catalog.sqlite`, built from the parsed JSON by
  `scripts/build_catalog.py`: 3 acts, 76 chapters, 1,247 sections, 1,873
  subsections, 878 cross-references, FTS5 over titles and text. 2.5 MB, rebuilt
  in seconds, deterministic by content digest.
- It exists because similarity is the wrong instrument for an exact request.
  Asked for Chapter XVI, a vector index returns what *reads like* Chapter XVI.
  The in-memory dict it replaces also had nowhere to put a range query, a
  full-text match or a cross-reference edge.
- `app/rag/catalog.py` is the read side: every query parameterized, every
  predicate built from validated values, so no model output reaches SQLite as
  SQL. A coordinate that does not exist returns empty — never the nearest one.
- `scripts/verify/verify_catalog.py` re-reads the parsed JSON rather than
  trusting the build: counts, byte-level text parity, statutory ordering
  (302 < 302A < 303), hierarchy, FTS parity, reference resolution, rebuild digest.
- Two of its three findings are parser defects already listed under *Outstanding*
  in `verification.md`, reached independently from a different direction. **Five
  section numbers occur twice** — page breaks where spilled text like
  `"344. (10) If, before the argument..."` was re-read as a section heading;
  known from the duplicate chunk ids `verify_chunks.py` caught. The catalog now
  keeps them flagged and denied the citation key, so `penal-s2` cannot resolve to
  a fragment from page 104; `library.py` still concatenates them onto the real
  section's text. **`crpc-s99A` is missing from the parse** — known against the
  oracle, found here instead by reference resolution, since it is cited five
  times while 99B-99G are present.
- One finding is new: 60 references point at sections absent from their own Act
  but present in another — CrPC citing "section 323" without naming the Penal
  Code. The build refuses to guess; the verifier counts them.
- One extraction bug fixed mid-build: `"section 3 and 2nd Schedule"` produced 49
  references to a section `2ND`. Unresolved rate 13.9% → 9.1%.

## Model-authored planning (E13)
- `app/rag/plan.py` replaces the regex router. One strict-JSON Groq Qwen call
  returns **coordinates only** — category, operation, act, chapter, sections,
  range — never statutory text, never SQL. `validate()` then checks each against
  the catalog: unknown act dropped, nonexistent chapter dropped with a note,
  a section number shared by three Acts turned into a clarification, a structural
  plan with nothing left to fetch turned into a question rather than silently
  falling through to search.
- Five categories (`meta`, `structural`, `semantic`, `out_of_scope`, `general`)
  replace six intents. Browse, summarize and lookup are all structural,
  differing by operation rather than by machine.
- `out_of_scope` was added after testing found `general` doing two jobs: asked
  what the Dowry Prohibition Act says, the planner returned `general`, whose
  contract is *answer plainly, no citations* — an ungrounded legal answer about
  a statute the corpus does not hold, which is the E1 failure. "Not law" and
  "law we do not have" need opposite handling, so they are now separate. 7/7 on
  the boundary cases with `general` and `semantic` unchanged.
- Scenario questions work single-turn — "they beat a man, he died, they were
  arrested without a warrant" plans as `semantic` with the restatement "arrest
  without warrant for causing death by voluntarily causing hurt". It also
  exposed a trap for the executors: the planner narrowed to `act=crpc`, right
  for the arrest but wrong as a filter, since the death engages Penal Code
  culpable homicide. On semantic plans the Act is a hint, never a filter.
- Follow-ups on a scenario do not work yet: "in that scenario, what if he had
  survived?" asks for clarification rather than resolving the referent. Right
  failure mode, still a gap — conversation scope is `plan.md` phase 6.
- **42/42 category, 27/27 scope** on 41 pinned cases in
  `scripts/experiments/plan_cases.py`, the first 27 being those previously pinned
  against the regex router. `scripts/plan_demo.py` walks the same questions one
  at a time, printing the raw model JSON, what validation changed, and what the
  coordinates actually fetch — for reading rather than grading.
- This reverses the earlier decision to run patterns first. That decision was
  evidence-based, but the evidence was against lfm-2.6B, which had labelled
  identical phrasings differently between two prompt revisions. The model
  changed, so the conclusion did.
- The questions the regex router could not structurally answer now work: "show me
  the sections about documentary evidence" → Evidence Act Chapter V, "the murder
  chapter" → Penal Code XVI. No pattern can know that "documentary evidence" is
  a chapter title.
- First version cost 1,873 tokens a call and exhausted Groq's
  input-tokens-per-minute budget after three questions. Two-tier prompt now: 778
  tokens standard, and the 76-chapter listing fetched only when a chapter is
  described by subject. 0.5-0.6s typical, 0.9s when the second call fires.
  Backoff on 429 added, so a burst is latency rather than silent degradation.
- Not yet wired into `answer.py`: the executors are the next step, and the
  existing pipeline is untouched. Clarification is decided and validated but has
  no answer kind and no UI.
- Focused legal answers still send five reranked sections to the generator. That
  is the direct reason broad questions return few cited provisions, and the
  threshold work in `plan.md` phase 4 is what replaces it.
- Conversation history is accepted but not used; typed Act/chapter/section scope
  is planned for follow-ups such as “show that section.”

## Documentation
- Root, backend and frontend READMEs now describe the real pipeline, Groq Qwen
  default, request modes, setup, metrics and known limitations.
- `query-routing-plan.md` records the next architecture, validation rules,
  database read model, evaluation sets and phased rollout.

## What this establishes
- The full path runs end to end: question → retrieval → reranking → generation → per-claim citation verification → cited answer or refusal, served over HTTP.
- Retrieval is measured at every stage against 608 external gold questions, including three negative results that overturned assumptions taken from published work (E2, E3, E7) and one that was never validly measured at all (E6).
- Generation is verified per claim at request time, but its *quality* is not yet measured — no faithfulness or abstention numbers exist. That is the largest gap.
- Routing is now decided by a model and executed by validated code: the planner
  names coordinates (42/42 category, 27/27 scope on 41 pinned cases) and the
  verified SQLite catalog fetches them exactly, failing closed on anything it
  does not have.
- Next: build the four executors behind the planner, then replace `FINAL_K=5`
  with a fitted relevance threshold — at most 10 sections to the generator, every
  section above the threshold shown to the reader as catalog records. Query
  rewriting remains part of that work because 28.6% of gold sections are still
  never retrieved.
