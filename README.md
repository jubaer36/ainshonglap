# AinShonglap

A source-grounded Bangladesh criminal-law assistant over the Code of Criminal
Procedure, 1898, the Penal Code, 1860, and the Evidence Act, 1872. It answers
with section-level citations and source excerpts, refuses unsupported legal
claims, and exposes the indexed statutes for exact browsing and lookup.

## Current architecture

```text
source PDFs
  -> hierarchical parser
  -> PDF/oracle verification
  -> section + subsection chunks
  -> BGE-M3 embeddings + FAISS        (what resembles this question)
  -> catalog.sqlite                   (what these provisions ARE)

question
  -> Gemini Flash Lite planner  (strict JSON: coordinates only, never text or SQL)
  -> validated against the catalog
  -> structural: exact SQL fetch   |   semantic: retrieval + reranking
  -> Groq Qwen structured generation
  -> citation-key + verbatim-quote gates
  -> FastAPI -> Next.js UI
```

A planner decides what the question is and names **where** to look; code owns
every fact. Six categories: `meta` (about the assistant), `structural` (a named
section, range, chapter or Act — fetched exactly, in statutory order),
`semantic` (provisions that must be found), `document` (a pasted case brief or
transcribed page — its cited sections are fetched by number rather than hoped
for), `out_of_scope` (a legal question about law outside these three Acts —
refused, never answered from memory), `general` (not a legal question, including
ordinary conversation; answered plainly, no citations). A coordinate the catalog does not have returns nothing rather than
the nearest match, so a wrong chapter fails visibly instead of quietly returning
different law. Measured at 42/42 category and 27/27 scope on a pinned 41-case
set (`experiments.md` E13).

Gemini 3.5 Flash Lite plans (1.3-1.8s) with Groq Qwen as an automatic fallback;
Groq Qwen writes the answers, with Mercury, OpenRouter and Gemini routes
selectable per request. Every request logs one line per step, so which stage
produced a given answer is readable rather than guessed.
Generator output is never trusted as a citation: every claim must cite a
retrieved section and include a quote found verbatim in it.

The planner, catalog and executors are live and served. Two things sit on top:
**voice input** (Deepgram nova-3 — the transcript is shown for confirmation, never
auto-sent, because the pipeline cannot detect a correctly-executed *wrong*
question) and **conversation memory** (a rolling summary from Gemini Flash Lite;
the planner rewrites a follow-up into a standalone question so nothing
downstream knows a conversation happened).

Generators are selectable per request and quota-aware: exhaustion is tracked per
model, an exhausted route is greyed out in the picker, and `/api/query`
substitutes a different provider and says so.

Accounts are optional, and the first screen makes the choice explicit. As a
**guest** nothing is written anywhere — not to the server, not to the browser —
and the conversation disappears with the tab. **Signed in** (Supabase Auth),
conversations are stored with everything needed to reload them as they happened:
each question and answer, the context each answer was produced against, the
model that produced it, and any page images with their transcriptions.

The required disclaimer is appended server-side from a constant to every answer,
including refusals, so it travels with the answer rather than sitting as chrome.

Focused answers still cap at five sections. That cap is the largest known
weakness — a two-issue scenario collapses into one embedding query and the
dominant wording takes every slot — and the fitted threshold plus facet
retrieval that replaces it is `plan.md` phase 4.

## Repository layout

- `backend/` — FastAPI API, retrieval/generation pipeline, ingestion and experiments
- `frontend/` — Next.js chat UI, model picker, statute browser and citation drawer
- `Laws/main/` — the three source PDFs and their parsed, chunked and indexed outputs
- `Laws/bdlaws/` — independent validation oracle; never used as answer content
- `Laws/bangladesh-legal-qa-dataset/` — external retrieval evaluation set

The index currently contains 1,321 section chunks and 999 subsection vectors.
Served retrieval measured on the external 608-question set is Recall@1 41.8%,
Recall@5 59.0%, Recall@20 71.4%, and Act accuracy 87.5%.

## Run locally

Backend and frontend run separately. Start the backend first.

```bash
conda create -n ainshonglap python=3.12
conda activate ainshonglap
cd backend
pip install -r requirements.txt
cp .env.example .env.local
# add GROQ_API_KEY and OPENROUTER_API_KEY to .env.local
uvicorn app.main:app --reload
```

The API runs at <http://localhost:8000>; OpenAPI is at `/docs` and runtime/index
status is at `/health`.

```bash
cd frontend
npm install
npm run dev
```

The UI runs at <http://localhost:3000>. Set `NEXT_PUBLIC_API_URL` in
`frontend/.env.local` only when the backend is not at `http://localhost:8000`.

## Ingestion and evaluation

Run from `backend/` in the `ainshonglap` environment:

```bash
python scripts/parse_law_pdf.py --all
python scripts/verify/verify_parsed.py
python scripts/chunk_sections.py
python scripts/verify/verify_chunks.py
python scripts/build_index.py
python scripts/eval_retrieval.py
python scripts/rerank.py --topk 20
```

The API and evaluation scripts share `app/rag/pipeline.py`; measured retrieval
and served retrieval therefore use the same implementation.

Provider probes:

```bash
python scripts/groq_chat.py --json
python scripts/groq_chat.py --prompt "What does Penal Code section 379 provide?"
python scripts/openrouter_chat.py --json --model lfm
```

## API summary

| method | path | purpose |
|---|---|---|
| POST | `/api/query` | planned, cited answer or refusal; carries conversation memory |
| GET | `/api/plan` | what the planner decided, without answering |
| POST | `/api/transcribe` | voice input → transcript (never an answer) |
| GET | `/api/retrieve` | inspect dense/reranked retrieval |
| GET | `/api/models` | selectable schema-capable generators |
| GET | `/api/acts` | indexed Acts and section counts |
| GET | `/api/sections` | filter and paginate sections |
| GET | `/api/sections/{key}` | exact section text and provenance |
| GET | `/health` | model/index readiness |

See `backend/API.md` for the request/response contract and configuration.

## Documentation

- `entry-assessment.md` — assessment brief
- `plan.md` / `progress.md` — implementation status and next work
- `parsing-plan.md` / `verification.md` — parsing and independent checks
- `chunking-plan.md` — section-aware chunking decisions
- `retrieval-plan.md` / `retrieval-baseline.md` — retrieval design and metrics
- `generation-plan.md` — generation, citation enforcement and abstention
- `query-routing-plan.md` — model-authored plans, SQL catalog and comprehensive retrieval
- `experiments.md` — hypotheses, configurations, results and decisions
- `similar-projects.md` — related legal/Bangla RAG systems

## Current limitations

- Only three Acts are ingested; amendments, case law and remaining PDFs are not served.
- The answer path sends at most five reranked sections to the generator. A
  scenario raising two legal issues collapses into one embedding query, so the
  dominant wording takes every slot — "three men beat a man and he died"
  retrieves *hurt* sections and never reaches culpable homicide, and the
  generator then refuses, correctly, on what it was given. Facet retrieval plus
  a fitted threshold is the fix (`plan.md` phase 4).
- The CrPC Second Schedule — which classifies offences as bailable or
  cognizable — is **not in the corpus**, so "is house theft bailable?" is
  refused. No Bangladesh source for it exists in the repo; the copy that
  contains one is Pakistan's CrPC and was rejected for that reason.
- Quote verification proves provenance, not that a claim is logically entailed
  by its quote.
- Voice transcription accuracy on real speech is unmeasured; the metric that
  matters is section-number accuracy, not word error rate.
- Follow-up detection varies by model and has no pinned regression set; nor do
  the executors, memory, voice or quota fallback. `plan_cases.py` covers
  planning only.
- Generation quality and multi-section completeness still need dedicated
  evaluation.

This system provides legal information, not legal advice.
