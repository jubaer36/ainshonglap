# API

Base `http://localhost:8000` · OpenAPI at `/docs`.

```bash
conda activate ainshonglap
cd backend
uvicorn app.main:app --reload
RAG_WARMUP=0 uvicorn app.main:app --reload   # skip model load (corpus endpoints only)
```

## Endpoints

| method | path | purpose |
|---|---|---|
| POST | `/api/query` | question → cited answer, or a refusal |
| GET | `/api/retrieve` | raw retrieval, no generation — inspection and debugging |
| GET | `/api/acts` | indexed acts + section counts |
| GET | `/api/sections` | browse/filter sections (`act`, `q`, `limit`, `offset`) |
| GET | `/api/sections/{key}` | one section by citation key, e.g. `penal-s379` |
| GET | `/health` | liveness, index stats, configured models |

### POST `/api/query`

```json
{"question": "What is the punishment for theft?"}
```

Returns an `AssistantMessage`: `heading`, `blocks` (paragraphs with inline
citation segments), `citations` keyed by citation key, and `unsupported`.

**A refusal is a 200 with `unsupported: true`**, not an error — "the Acts do not
cover this" is an answer. A **502** means the generator was unreachable, which
is a different failure and retryable.

Every claim in a returned answer has passed two checks against the retrieved
text (`app/rag/verify.py`):

- **C2** the cited section was actually retrieved, and where several acts share
  a section number the act must match too
- **C3** the quoted span appears verbatim in that section

Claims failing either are dropped. If none survive, the answer becomes a
refusal. The disclaimer is appended server-side from a constant, on every path,
so the model cannot omit it.

### GET `/api/retrieve?q=...&k=5&rerank=true`

The retrieval half alone, with dense and reranker scores per hit. `rerank=false`
gives the dense-only ordering — the same code path the eval scripts measure, so
served and measured numbers cannot drift.

## Configuration

`backend/.env.local` (gitignored) holds `OPENROUTER_API_KEY` / `GEMINI_API_KEY`.

| var | default | |
|---|---|---|
| `RAG_LLM_BACKEND` | `openrouter` | `openrouter`, `gemini`, `local` |
| `RAG_OPENROUTER_MODEL` | `deepseek/deepseek-v4-flash-0731:free` | |
| `RAG_DENSE_K` / `RAG_FINAL_K` | `20` / `5` | candidates reranked / sections returned |
| `RAG_WARMUP` | `1` | `0` skips model loading at startup |
| `CORS_ORIGINS` | `http://localhost:3000` | comma-separated |

Generator routes must enforce JSON schema. Requests set
`provider.require_parameters`, so a route whose providers ignore schemas fails
with a 404 rather than silently returning prose — which would otherwise surface
as a refusal. Verified enforcing: `deepseek`, `liquid/lfm-2.5-2.6b`,
`nvidia/nemotron-3-super-120b-a12b`. Verified **not**: `google/gemma-4-31b-it`.
Check any route with `python scripts/openrouter_chat.py --json --model <alias>`.

## Layout

```
app/rag/
  config.py     models, paths, k values
  encoder.py    BGE-M3 query/document embedding
  store.py      FAISS index + chunk records
  reranker.py   cross-encoder (E4)
  pipeline.py   retrieve() — dense, dedupe, rerank
  llm.py        OpenRouter / Gemini / local, JSON-schema output
  answer.py     prompt, generation, gating, refusal
  verify.py     C2 + C3 citation gates
  citations.py  chunk metadata -> Citation
  library.py    browsable corpus
  present.py    Answer -> AssistantMessage
```

`pipeline.retrieve()` is shared by the API and the eval scripts by design: they
previously diverged, and the logged numbers described a pipeline nothing could
serve (`experiments.md` E7).
