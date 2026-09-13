# AinShonglap backend

FastAPI skeleton for the Bangladesh Legal Q&A Assistant. Currently serves
canned responses (`app/data/mock_responses.py`) behind the same
`POST /api/query` contract the real retrieval/LLM pipeline will use later —
see `app/models/schemas.py` for the request/response shapes.

## Run locally

Everything (API + the local-model experiments in `scripts/`) runs in one
conda env — no `venv`:

```bash
conda create -n ainshonglap python=3.12   # first time only
conda activate ainshonglap
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload
```

`requirements.txt` includes both the API deps and the CUDA-linked torch
stack for the `scripts/` experiments (see plan.md step 4) — one env, one
file.

API docs: http://localhost:8000/docs
Health check: http://localhost:8000/health

Local-model experiments:

```bash
python scripts/local_llm_chat.py   # local Qwen2.5-VL-3B-Instruct, interactive
python scripts/gemini_chat.py      # Gemini 2.5 Flash via API, interactive
```

`gemini_*.py` reads `GEMINI_API_KEY` from `backend/.env.local`.

## Try it

```bash
curl -X POST http://localhost:8000/api/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What are the requirements for recording an FIR?"}'
```

## Structure

- `app/main.py` — FastAPI app, CORS (allows the Next.js dev server on `localhost:3000`), router registration.
- `app/api/routes/query.py` — the `POST /api/query` endpoint.
- `app/api/routes/citations.py` — `GET /api/citations`, the full citation library (lets the UI preview a statute before any question is asked).
- `app/models/schemas.py` — Pydantic request/response schemas, mirrored by `frontend/lib/types.ts`.
- `app/data/mock_responses.py` — canned Q&A data and the keyword-matching stand-in for retrieval + generation. This is the only module that needs to change when real ingestion/retrieval/LLM generation replaces the mock.
- `scripts/` — standalone LLM-baseline experiments (local Qwen2.5-VL-3B via `local_llm_*.py`, Gemini 2.5 Flash via `gemini_*.py`), not wired into the API yet. Run in the `ainshonglap` conda env, see above.
