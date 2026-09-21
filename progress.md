# Progress

## Frontend
- Next.js chat UI, wired to a `POST /api/query` contract so UI and backend could be built independently.
- Renders structured answers (paragraphs, numbered steps, bullets) rather than flat text — legal answers like FIR filing steps need the structure.
- Citations render inline with a reference panel, so a claim traces back to its source without leaving the chat.
- TS types mirror the backend's Pydantic schemas by hand.
- Each answer `kind` renders differently, because the outcomes are not variations
  of each other: `clarify` is a blue question (one detail away from an answer),
  `out_of_scope` a neutral boundary, and only genuine retrieval failure is amber.
  A provenance badge says whether the text was quoted verbatim from the catalog,
  cited and checked, or is the model's own knowledge.
- Voice input puts the transcript in the box for confirmation and never sends it.
- A `context · N turns` chip opens the conversation memory: the running summary
  verbatim, the section keys kept exactly, a stale warning when the summariser
  failed, and a Forget button. Hidden state that steers answers is inspectable.
- Conversations are saved in `localStorage` and listed in the sidebar, each
  record carrying its **memory and citations**, not just messages — reopening
  one has to restore what the assistant remembered, or the next follow-up
  silently loses its context. Client-side for the same reason as the memory
  itself: a server that never receives these questions cannot mishandle them.
  Capped at 50, every access guarded, since storage throws in private windows
  and when full.
- Enter sends, Shift+Enter inserts a newline, and an IME composition guard stops
  Enter firing mid-word while composing Bangla.

### UI cleanup
- Removed the standing chrome text: "Powered by Bangladeshi Legal Corpus:
  BDLaws, Supreme Court Precedents & Gazettes", "Plain-Language Statutory
  Intelligence & Verified Precedents", "Bangladeshi Codex Active", the sidebar
  footer paragraph, and the always-on disclaimer strip.
- Three of those were **false**: there is no case law, no gazettes and no
  precedents in the corpus, and the status line described activity that did not
  exist. For a tool whose whole value is that claims are checkable, decorative
  copy overstating its sources is the worst kind of clutter.
- **The disclaimer requirement (brief, line 37) is still met**: it is appended
  server-side from a constant to every answer including refusals
  (`app/rag/present.py`), so it sits on the answers where it is read rather than
  under the input box where it became wallpaper.
- The status line now shows only real events — microphone state, transcription
  confidence, memory errors — and renders nothing otherwise.

## Backend
- FastAPI service over the real pipeline: `POST /api/query`, `GET /api/retrieve`, `GET /api/acts`, `GET /api/sections[/{key}]`, `GET /health`. Contract and configuration in `backend/API.md`.
- The mock response module is gone; every endpoint serves the indexed corpus.
- Questions are planned into five categories and executed by validated code
  (`app/rag/plan.py` -> `execute.py`); `POST /api/transcribe` takes voice input;
  conversation memory is a rolling summary carried by the client. The six-intent
  pattern router it replaced survives behind `RAG_PLAN=0` as an outage switch.

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

## Validated executors and the served pipeline (stage 3)
- `app/rag/execute.py` turns a validated plan into an `Answer`, one executor per
  category: `meta` from the catalog manifest, `structural` by exact SQL fetch,
  `semantic` through retrieval + generation + the C2/C3 gates, `out_of_scope`
  and `clarify` as reasoned refusals, `general` as a labelled ungrounded answer.
- `POST /api/query` runs the planner path (`RAG_PLAN=1`, a kill switch rather
  than a permanent fork). `GET /api/plan` returns what the planner decided
  without answering, for when an answer looks wrong and the first question is
  whether it was *planned* wrong.
- `AssistantMessage.kind` carries which machine answered, so the client can tell
  "outside these Acts" from "which Act did you mean?" — `unsupported` alone
  cannot. Eight kinds; the frontend renders `clarify` as a question in blue,
  `out_of_scope` as a neutral boundary, and only genuine retrieval failures in
  amber. A provenance badge states whether text was quoted verbatim from the
  catalog, cited and checked, or ungrounded.
- Two rules enforced in code: the Act **filters** a structural plan but is only
  a **hint** on a semantic one (a scenario planner answers `act=crpc` for the
  arrest while the death is Penal Code, and filtering would drop it); and a
  structural fetch quotes rather than paraphrases, so no model can alter it.
- Verified end to end over HTTP across all seven paths, including a 42-citation
  chapter listing and a four-section theft answer with every quote verified.

## Voice input
- `POST /api/transcribe` (Deepgram nova-3) returns a transcript and **never an
  answer**. The separation is the safety property: a misheard section number
  produces a perfectly grounded answer about a different offence — the planner
  names coordinates that exist, the catalog fetches them, the quote verifies
  against the section fetched. Every gate passes because none of them know what
  was said, so the reader seeing the words is the only check on this path.
- `MicButton` records, transcribes, and puts the text in the input box for
  confirmation. Click-to-toggle, not hold-to-talk: holding raced the permission
  prompt, so the release arrived before the stream was granted and recording
  began after the button came up.
- Keyterm prompting boosts statutory vocabulary built from the catalog, because
  a general speech model turns "cognizable" into "recognizable" rather than
  failing visibly.
- **Bengali is `bn`, nova-3 only, and is not in Deepgram's `multi`
  code-switching set.** Mixed Bangla-English — the ordinary register for talking
  about law here — has no single-model answer. `scripts/deepgram_stt.py
  --compare` measures the cost of the choice; transcription accuracy on real
  speech is **not yet measured**, and the metric that matters is section-number
  accuracy, not WER (a 9% WER transcript that turns 379 into 279 is a different
  offence).

## Conversation memory
- A rolling summary: after each turn, the previous summary plus the new question
  and answer go to Gemini 3.5 Flash Lite (on a second key, `GEMINI_API_KEY_2`,
  so per-turn summarisation never competes for quota) and come back as one short
  paragraph. That paragraph is the context the next question is read against.
- The planner decides `follow_up` explicitly and **defaults to false**, then
  rewrites a follow-up into a standalone `resolved_question`. Everything
  downstream consumes the rewrite, so no executor knows a conversation happened.
- Summarisation runs **after** the answer, so a summariser outage costs the
  conversation its memory, never the user their answer.
- Memory is client-held and echoed each turn; the server stores nothing. For
  questions this personal that is a privacy property, and it also means no
  session store and nothing to lose on restart.
- **A rolling summary is lossy and the loss compounds**, and the detail most
  likely to be mangled is a section number. So the memory carries two things:
  the prose summary, and a `sections` list of citation keys copied by *code*
  from the answer's own sources. The prompt asks for verbatim numbers; the code
  list is the guarantee.
- The frontend shows it: a `context · N turns` chip opens a panel with the
  summary verbatim, the cited section keys (clickable), a stale warning when the
  summariser failed, and a Forget button. Hidden state that steers answers
  should be inspectable.
- Observed working on two providers: "what if he had survived with only
  injuries?" → *"What offence is committed if three men beat a man... causing
  him injuries but not death?"*; "the section right before it" after s.302 →
  s.301 fetched exactly. A topic switch to "what is the capital of France"
  correctly inherited nothing.
- Two limits: follow-up detection is **model-dependent** (Groq flagged "arrest
  *them*" as a follow-up, Mercury did not), and none of this is on a pinned
  regression set yet.

## Provider quota and fallback
- Free tiers run out, and on Groq the daily token cap is **per model** — a 429
  naming `qwen/qwen3.8-27b` says nothing about `openai/gpt-oss-20b`. So
  `app/rag/quota.py` records exhaustion per model, with the cooldown parsed from
  the provider's own "try again in 12m40s".
- `GET /api/models` marks a route unavailable with the reason and a countdown;
  the picker greys it out, moves a dead selection to the fallback, and re-polls
  so a model un-greys when its budget resets. `POST /api/query` substitutes the
  fallback (`mercury`, a different provider) and **says so** in the response —
  a silent swap would hide which model wrote the answer.
- Only genuine quota errors are recorded. A 500 or a timeout says nothing about
  budget, and blocking a model for an hour over a blip would be worse than the
  blip.
- Generators registered: `qwen`, `oss` (gpt-oss-20b), `oss120`, `lfm`, `super`,
  `mercury`, `mercury-2`, `gemini`.

## Serving correctness fixes
- **Every handler was `async def` while doing blocking work** — cross-encoder
  inference then a synchronous HTTPS call — so FastAPI ran them on the event
  loop and one in-flight question froze the whole server. The model list, the
  statute browser and citation previews were all unusable while any answer
  generated. Blocking handlers are now plain `def` (threadpool); `/api/transcribe`
  stays async for the upload and offloads the Deepgram call explicitly.
- **Warmup blocked startup.** Loading BGE-M3 and the cross-encoder took ~30s
  before uvicorn served anything, so a browser that loaded in 368ms asked for
  the model list and timed out — a backend still waking up looked like a broken
  one. Warmup now runs in a worker thread, the catalog (SQLite, milliseconds)
  loads synchronously, and `/health` reports `warming` until ready.
- CORS allowed only `http://localhost:3000`; `127.0.0.1:3000` is a different
  origin to a browser, so opening the app at the other address blocked every
  response after it arrived. Both are allowed by default now.
- The model picker returned `null` whenever its list was empty, so a failing
  `/api/models` looked identical to a feature that did not exist. It now shows
  the error with a retry.

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
- The planner path is now served end to end, with voice input and conversation
  memory on top of it. What is built is wider than what is measured: the
  executors, memory, voice and quota handling have no pinned regression numbers,
  and `plan_cases.py` (42/42 category, 27/27 scope) still covers only planning.
- Next, and in this order: pin follow-up cases in `plan_cases.py`, then replace
  `FINAL_K=5` with a fitted relevance threshold plus facet retrieval. Two
  separate scenario runs refused answerable questions because five retrieval
  slots all went to *hurt* sections and never reached culpable homicide — a
  multi-issue scenario collapses into one embedding query, and the dominant
  wording takes every slot. Query rewriting belongs to the same work, since
  28.6% of gold sections are still never retrieved.
