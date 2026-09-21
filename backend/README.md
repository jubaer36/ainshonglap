# AinShonglap backend

FastAPI service over the retrieval + generation pipeline. Endpoints and
configuration: **`API.md`**.

The default generator is `qwen/qwen3.8-27b` served directly by Groq with strict
JSON-schema output. OpenRouter LFM currently handles intent routing; Gemini and
the remaining schema-capable OpenRouter models are optional fallbacks.

## Run locally

Everything (API + the `scripts/` experiments) runs in one conda env — no `venv`:

```bash
conda create -n ainshonglap python=3.12   # first time only
conda activate ainshonglap
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload
```

`requirements.txt` holds both the API deps and the CUDA-linked torch stack the
scripts need — one env, one file.

Docs at http://localhost:8000/docs · health at `/health` (reports index stats
and the configured models, so a degraded start is visible without reading logs).

`.env.local` (gitignored) holds `GROQ_API_KEY` for the default Qwen generator.
`OPENROUTER_API_KEY` is used by the default router; Gemini is an optional fallback.

```bash
curl -X POST http://localhost:8000/api/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What is the punishment for theft?"}'
```

Startup loads BGE-M3 and the cross-encoder (~30s). `RAG_WARMUP=0` skips it when
only the corpus endpoints are needed.

## Structure

```
app/
  main.py            app, CORS, lifespan warmup, /health
  api/routes/        query.py, retrieve.py, citations.py, models.py
  models/schemas.py  Pydantic contract, mirrored by frontend/lib/types.ts
  rag/               the pipeline — see API.md for the file-by-file map
scripts/             ingestion + tooling: parse, chunk, index, eval, rerank, REPLs
  verify/            correctness checks, run against the source not the parser's stats
  experiments/       measurement; nothing here runs in the product
```

`app/rag/pipeline.retrieve()` is shared by the API and the eval scripts by
design. They used to be separate implementations, and the logged numbers ended
up describing a pipeline nothing could serve (`experiments.md` E7).

## Request handling

`POST /api/query` is not always a vector-search operation. The current router
separates six cases:

| intent | execution |
|---|---|
| `meta` | describe the indexed corpus and assistant capabilities |
| `lookup` | fetch an explicitly named Act/section directly |
| `browse` | enumerate sections from the parsed hierarchy |
| `summarize` | summarize a whole Act from its chapter outline |
| `general` | answer without legal citations and label it ungrounded |
| `legal` | dense retrieval, section collapse, reranking, generation and citation gates |

Stable patterns currently handle obvious meta/browse/lookup/summary requests;
the schema-constrained router handles the remainder. Direct coordinates are
validated against the corpus, and ambiguous section numbers require an Act.

**This is being replaced.** `app/rag/plan.py` collapses the six intents into
four categories — `meta`, `structural` (lookup/browse/summarize, differing by
operation), `semantic`, `general` — and lets a strict-JSON Groq Qwen call decide,
returning coordinates only. `app/rag/catalog.py` then executes them against
`Laws/main/catalog.sqlite` with parameterized SQL. Both are built and measured
(`experiments.md` E12, E13) but not yet wired into `/api/query`; the executors
are the next step.

The legal path retrieves 20 distinct section candidates, cross-encoder reranks
them, and sends the best five to the generator. This is appropriate for a
focused answer but is not yet an exhaustive “show every related provision”
mode. Conversation history is accepted by the API but not yet used.

## Pipeline

Each stage is checked against something external, not self-reported:

```bash
python scripts/parse_law_pdf.py --all    # PDFs   -> Laws/main/parsed/*.json
python scripts/verify/verify_parsed.py          #   vs the PDF + the bdlaws oracle
python scripts/chunk_sections.py         # parsed -> Laws/main/chunks/*.json
python scripts/verify/verify_chunks.py          #   vs the parsed JSON
python scripts/build_index.py            # chunks -> BGE-M3 vectors + FAISS
python scripts/build_catalog.py          # parsed -> Laws/main/catalog.sqlite
python scripts/verify/verify_catalog.py         #   vs the parsed JSON
python scripts/eval_retrieval.py         #   Recall@k / MRR / Act accuracy
python scripts/rerank.py --topk 20       #   dense vs + reranker, with delta
```

Ingest a PDF that is not one of the three acts — chunking and indexing discover
whatever the parser wrote, so nothing downstream needs editing:

```bash
python scripts/parse_law_pdf.py --pdf "Laws/The Arms Act, 1878.pdf" \
    --name "The Arms Act, 1878" --act-no "XI of 1878" --slug arms
```

`--backend auto` measures text per page and falls back to Docling (OCR) below
120 chars/page, i.e. for scans. Docling is much worse wherever PyMuPDF works
(5 of 13 sections on a 6-page sample) — see `parsing-plan.md`.

Measurement lives in `scripts/experiments/`: `eval_retrieval.py`, `rerank.py`,
`fit_threshold.py`, `experiment_retrieval.py` (prefix, chunk budget),
`experiment_granularity.py` (retrieval unit), `experiment_abstention.py`
(does the generator answer when retrieval missed), plus the LLM baselines
`groq_chat.py`, `openrouter_chat.py`, `gemini_chat.py`, plus the historical
local-model baseline scripts.

## Generator baselines

```bash
python scripts/plan_demo.py                         # planner, question by question
python scripts/experiments/plan_cases.py            # planner, graded (E13)
python scripts/groq_chat.py                         # default Qwen REPL
python scripts/groq_chat.py --json                  # strict-schema probe
python scripts/openrouter_chat.py --model lfm       # router/fallback probe
python scripts/gemini_chat.py                       # optional Gemini fallback
```

The `--json` probe matters: a route that ignores the schema returns prose, which
inside the pipeline would surface as a false refusal. Requests pin
`provider.require_parameters` so such routes fail loudly instead.

## Current limitations

- only the CrPC, Penal Code and Evidence Act are served
- legal generation sees five reranked sections, limiting broad coverage
- model routing and structural scope extraction are not yet one unified plan
- history does not yet resolve follow-ups such as “show that chapter”
- exact browsing is an in-memory read model over `chunks.json`, not yet SQLite
- citation gates validate the source and quote, not semantic entailment
- generation abstention and multi-section completeness evaluation are unfinished
