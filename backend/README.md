# AinShonglap backend

FastAPI service over the retrieval + generation pipeline. Endpoints and
configuration: **`API.md`**.

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

`.env.local` (gitignored) holds `OPENROUTER_API_KEY` / `GEMINI_API_KEY`.

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
  api/routes/        query.py, retrieve.py, citations.py
  models/schemas.py  Pydantic contract, mirrored by frontend/lib/types.ts
  rag/               the pipeline — see API.md for the file-by-file map
scripts/             ingestion, verification, evaluation, experiments
```

`app/rag/pipeline.retrieve()` is shared by the API and the eval scripts by
design. They used to be separate implementations, and the logged numbers ended
up describing a pipeline nothing could serve (`experiments.md` E7).

## Pipeline

Each stage is checked against something external, not self-reported:

```bash
python scripts/parse_law_pdf.py --all    # PDFs   -> Laws/main/parsed/*.json
python scripts/verify_parsed.py          #   vs the PDF + the bdlaws oracle
python scripts/chunk_sections.py         # parsed -> Laws/main/chunks/*.json
python scripts/verify_chunks.py          #   vs the parsed JSON
python scripts/build_index.py            # chunks -> BGE-M3 vectors + FAISS
python scripts/eval_retrieval.py         #   Recall@k / MRR / Act accuracy
python scripts/rerank.py --topk 20       #   dense vs + reranker, with delta
```

Experiments: `experiment_retrieval.py` (prefix, chunk budget),
`experiment_granularity.py` (retrieval unit), `experiment_abstention.py`
(does the generator answer when retrieval missed).

## Generator baselines

```bash
python scripts/openrouter_chat.py --list            # known model aliases
python scripts/openrouter_chat.py --model lfm       # REPL; /model switches live
python scripts/openrouter_chat.py --json --model X  # does this route enforce JSON schema?
python scripts/gemini_chat.py                       # Gemini 2.5 Flash
python scripts/local_llm_chat.py                    # local Qwen2.5-VL-3B
```

The `--json` probe matters: a route that ignores the schema returns prose, which
inside the pipeline would surface as a false refusal. Requests pin
`provider.require_parameters` so such routes fail loudly instead.
