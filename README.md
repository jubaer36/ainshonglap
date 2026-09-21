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
  -> Groq Qwen planner   (strict JSON: coordinates only, never text or SQL)
  -> validated against the catalog
  -> structural: exact SQL fetch   |   semantic: retrieval + reranking
  -> Groq Qwen structured generation
  -> citation-key + verbatim-quote gates
  -> FastAPI -> Next.js UI
```

A planner decides what the question is and names **where** to look; code owns
every fact. Four categories: `meta` (about the assistant), `structural` (a named
section, range, chapter or Act — fetched exactly, in statutory order),
`semantic` (provisions that must be found), `general` (answered plainly, no
citations). A coordinate the catalog does not have returns nothing rather than
the nearest match, so a wrong chapter fails visibly instead of quietly returning
different law. Measured at 42/42 category and 27/27 scope on a pinned 41-case
set (`experiments.md` E13).

Groq-hosted `qwen/qwen3.8-27b` is both the planner and the answer generator;
schema-capable OpenRouter and Gemini models remain available as fallbacks.
Generator output is never trusted as a citation: every claim must cite a
retrieved section and include a quote found verbatim in it.

The planner and catalog are live; the executors behind them are being built, so
the served path still runs the older six-intent router. Focused answers still
cap at five sections — the fitted relevance threshold that replaces that cap is
`plan.md` phase 4.

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
| POST | `/api/query` | routed, cited answer or refusal |
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
- The answer path sends five reranked sections to the generator, so it is optimized for focused answers rather than exhaustive provision discovery.
- Routing is hybrid pattern/LLM rather than a single model-authored execution plan.
- Conversation history is sent by the UI but is not yet used to resolve follow-ups.
- Quote verification proves provenance, not that a claim is logically entailed by its quote.
- Generation quality and multi-section completeness still need dedicated evaluation.

This system provides legal information, not legal advice.
