# আইনসংলাপ (AinShonglap)

A source-grounded Bangladesh law assistant. Three **core Acts** are always there
— the Code of Criminal Procedure, 1898, the Penal Code, 1860, and the Evidence
Act, 1872 — and further sources can be added and removed from the app. It
answers with section-level citations and source excerpts, refuses unsupported
legal claims, and exposes the indexed statutes for exact browsing and lookup.

## Current architecture

```text
source PDF
  -> parse: PyMuPDF (numbered statutes) or Docling (any layout)
            — a person picks, from sampled pages shown beside both readings
  -> verification against the PDF and an independent oracle
  -> section + subsection chunks -> BGE-M3 embeddings
  -> Supabase Postgres: law_* tables + pgvector       (served)
     local catalog.sqlite + FAISS                      (fallback only, logged)

question
  -> gpt-oss-120b planner  (strict JSON: coordinates only, never text or SQL;
                            gpt-oss-120b on its own key as fallback)
  -> validated against the catalog
  -> structural: exact fetch   |   semantic: vector search + reranking
  -> gpt-oss-120b structured generation (other models selectable)
  -> citation-key + verbatim-quote gates
  -> FastAPI -> Next.js UI
```

A planner decides what the question is and names **where** to look; code owns
every fact. Six categories: `meta` (about the assistant), `structural` (a named
section, range, chapter or Act — fetched exactly, in statutory order),
`semantic` (provisions that must be found), `document` (a pasted case brief or
transcribed page — its cited sections are fetched by number rather than hoped
for), `out_of_scope` (a legal question about law outside the Acts it holds —
refused, never answered from memory), `general` (not a legal question, including
ordinary conversation; answered plainly, no citations). A coordinate the catalog does not have returns nothing rather than
the nearest match, so a wrong chapter fails visibly instead of quietly returning
different law. Measured at 42/42 category and 27/27 scope on a pinned 41-case
set (`experiments.md` E13) — on an earlier planner model; two have served since
(E18), and re-measuring is the first open item in `plan.md`.

Which Acts exist is data, not code: the planner's scope, citation keys and the
"outside …" messages are all read from the catalog, so adding or removing a
source needs no code change and no restart.

`gpt-oss-120b` plans on a Groq key nothing else uses. Every model call —
answers, planner, summariser, naming — falls back to gpt-oss-120b on one more
key of its own, and each use is logged. `gpt-oss-120b` also writes the answers by
default, with Qwen, gpt-oss-20b, Gemini, Mercury and OpenRouter routes
selectable per request. Every request logs one line per step, so which stage
produced a given answer is readable rather than guessed.
Generator output is never trusted as a citation: every claim must cite a
retrieved section and include a quote found verbatim in it.

The planner, catalog and executors are live and served. Two things sit on top:
**voice input** (Deepgram nova-3 — the transcript is shown for confirmation, never
auto-sent, because the pipeline cannot detect a correctly-executed *wrong*
question) and **conversation memory** (a short rolling summary from
gpt-oss-120b on its own key, plus the previous question and answer kept word for
word; the planner rewrites a follow-up into a standalone question so nothing
downstream knows a conversation happened). The memory is updated after the
answer is shown, never before it. A conversation names itself after three answered turns,
on its own model and key, unless the reader has already renamed it.

Generators are selectable per request and quota-aware. Groq's free tier caps
usage per key *and* per model, so every `GROQ_API_KEY*` in `.env.local` is used
in turn and a 429 fails over to the next key immediately rather than waiting.
Exhaustion is tracked per (model, key); a model is greyed out in the picker only
once every one of its keys is spent, and `/api/query` then substitutes a
different provider and says so.

Accounts are optional, and the first screen makes the choice explicit. As a
**guest** nothing is written anywhere — not to the server, not to the browser —
and the conversation disappears with the tab. **Signed in** (Supabase Auth),
conversations are stored with everything needed to reload them as they happened:
each question and answer, the context each answer was produced against, the
model that produced it, and any page images with their transcriptions.

**Sources.** The three core Acts cannot be removed. Admins (`ADMIN_EMAILS`)
add and remove other sources from the sidebar: upload a PDF, compare sampled
pages with what each parser read, choose one, and it is indexed and served
without a restart. Statute text is served from Supabase; the local files are a
fallback, and every use of them is logged.

The required disclaimer is appended server-side from a constant to every answer,
including refusals, so it travels with the answer rather than sitting as chrome.

Focused answers cap at a fixed number of sections (currently eight, raised
from five and unmeasured). That cap is the largest known weakness — a two-issue
scenario collapses into one embedding query and the dominant wording takes every
slot — and the fitted threshold plus facet retrieval that replaces it is
`plan.md` phase 4.

## Repository layout

- `backend/` — FastAPI API, retrieval/generation pipeline, ingestion and experiments
- `frontend/` — Next.js chat UI, model picker, statute browser and citation drawer
- `Laws/main/` — the source PDFs (core Acts and added sources) and their parsed, chunked and indexed outputs
- `Laws/bdlaws/` — independent validation oracle; never used as answer content
- `Laws/bangladesh-legal-qa-dataset/` — external retrieval evaluation set

The index currently holds 2,370 vectors — 1,354 section chunks and 1,016
subsection chunks — across the three core Acts and the Arms Act, 1878. Served
retrieval measured on the external 608-question set, over the three core Acts,
is Recall@1 41.8%, Recall@5 59.0%, Recall@20 71.4%, and Act accuracy 87.5%.
Moving the store to Supabase left those results identical (`experiments.md`
E19).

## Run locally

Backend and frontend run separately. Start the backend first.

```bash
conda create -n ainshonglap python=3.12
conda activate ainshonglap
cd backend
pip install -r requirements.txt
cp .env.example .env.local
# add GROQ_API_KEY (and optionally _2, _3) and OPENROUTER_API_KEY to .env.local
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
python scripts/parse_law_pdf.py --all          # the core Acts
python scripts/verify/verify_parsed.py
python scripts/chunk_sections.py
python scripts/verify/verify_chunks.py
python scripts/build_index.py                  # reuses known vectors; --full re-embeds
python scripts/build_catalog.py && python scripts/verify/verify_catalog.py
python scripts/load_supabase.py --all && python scripts/verify/verify_supabase.py
python scripts/eval_retrieval.py
python scripts/rerank.py --topk 20
```

A new source can be added from the app's sidebar (admins), or from the command
line — `ingest_document.py` writes the same page review as an HTML file and
asks which parser to keep; `remove_act.py` takes a source out (never a core
Act).

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
| GET | `/api/acts` | indexed Acts, section counts, and which are core |
| GET/PATCH/DELETE | `/api/conversations…` | saved conversations (signed in) |
| POST/GET/DELETE | `/api/sources…` | add, review and remove sources (admins) |
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

- The core Acts plus the Arms Act, 1878 are served. Amendments and case law are
  not; other PDFs in `Laws/` can be added as sources, one at a time.
- Retrieval metrics were measured over the three core Acts. Every added source
  changes the corpus those numbers describe; they have not been re-measured.
- A document parsed by Docling is cited as "Section N" of it, not "Passage N":
  the catalog does not yet record a source's document type.
- The answer path sends at most eight reranked sections to the generator. A
  scenario raising two legal issues collapses into one embedding query, so the
  dominant wording takes every slot — "three men beat a man and he died"
  retrieves *hurt* sections and never reaches culpable homicide, and the
  generator then refuses, correctly, on what it was given. Facet retrieval plus
  a fitted threshold is the fix (`plan.md` phase 4). Raising the budget from 5 to
  8 did **not** fix a traced instance of this and is itself unmeasured (E17).
- A question whose answer is a burden *allocation* rather than a stated rule is
  refused even when retrieval finds the right sections. "If I can't prove myself
  innocent, am I guilty?" retrieves Evidence ss.101-106 correctly, and the
  generator declines because its prompt forbids reasoning from a provision to
  its consequence. Traced in E17.
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
