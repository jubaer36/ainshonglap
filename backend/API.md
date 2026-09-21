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
| GET | `/api/plan` | what the planner decided, without answering |
| POST | `/api/transcribe` | voice input → transcript (deliberately not an answer) |
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
citation segments), `citations` keyed by citation key, `unsupported`, and
`kind`.

**A refusal is a 200 with `unsupported: true`**, not an error — "the Acts do not
cover this" is an answer. A **502** means the generator was unreachable, which
is a different failure and retryable.

#### `kind` — how the answer was produced

A question is planned into a category and executed by the matching machine
(`app/rag/plan.py` → `app/rag/execute.py`), and the results are trustworthy in
different ways. `unsupported` alone cannot express that: it says only "not a
grounded answer", which makes "which Act did you mean?" indistinguishable from
"I searched and found nothing".

| `kind` | `unsupported` | citations | what it is |
|---|---|---|---|
| `lookup` | false | 1 | one named section, quoted verbatim from the catalog. No model touched the text. |
| `outline` | false | 0–80 | a chapter or range listing, or a whole-Act summary, from the parsed hierarchy |
| `legal` | false | 1+ | retrieved and generated, every claim through C2/C3 below |
| `meta` | false | 0 | about the assistant, assembled from the catalog manifest |
| `general` | false | 0 | **not** a legal question; the model's own knowledge, uncited and unchecked |
| `out_of_scope` | true | 0 | a legal question about law outside the three Acts. Nothing was searched. |
| `clarify` | true | 0 | the request needs one more detail — an ambiguous section number, a subjective selection |
| `refused` | true | 0 | retrieval ran and nothing survived the citation gates |

`out_of_scope` is kept apart from `general` deliberately. Answering "what does
the Dowry Prohibition Act say" from the model's own knowledge would be an
ungrounded legal answer about a statute nobody has verified — the E1 failure.
Answering "what is the capital of France" that way is fine. Same mechanism,
opposite consequences.

Clients should render `clarify` as a question rather than a failure: it is one
detail away from an answer, and an error-coloured panel tells the reader to give
up when they should type three more words.

Every claim in a returned answer has passed two checks against the retrieved
text (`app/rag/verify.py`):

- **C2** the cited section was actually retrieved, and where several acts share
  a section number the act must match too
- **C3** the quoted span appears verbatim in that section

Claims failing either are dropped. If none survive, the answer becomes a
refusal. The disclaimer is appended server-side from a constant, on every path,
so the model cannot omit it.

### GET `/api/plan?q=...`

What the planner decided, without answering. One small model call — no
retrieval, no generation.

```json
{"category": "structural", "operation": "enumerate",
 "act": "evidence", "chapter": "V", "sections": [],
 "needs_clarification": false, "by": "llm",
 "notes": ["chapter-lookup: 'DOCUMENTARY EVIDENCE' -> V in evidence"]}
```

The planner chooses the machine that answers every question, so when an answer
looks wrong the first thing worth knowing is whether it was *planned* wrong.
`notes` records everything validation changed or rejected — a chapter
renumbered to the Act's own spelling (`16` → `XVI`), a coordinate dropped for
not existing. `by` is `"fallback"` when the planner call failed and the question
degraded to plain semantic retrieval.

The planner returns **coordinates only** — never statutory text, never SQL. Code
validates each against the catalog before executing it, so a hallucinated
chapter returns nothing rather than the nearest real one.

Set `RAG_PLAN=0` to fall back to the older pattern router in `app/rag/refine.py`.

### POST `/api/transcribe`

Multipart upload of a recording; returns what was heard. **It does not answer
the question.**

```bash
curl -X POST http://localhost:8000/api/transcribe \
  -F "audio=@question.webm;type=audio/webm"
```

```json
{"transcript": "what does section 379 of the penal code say",
 "confidence": 0.98, "duration": 4.2, "language": "en"}
```

The separation is a safety property, not an API convenience. A misheard section
number produces a **perfectly grounded answer about the wrong offence**: the
planner names coordinates that exist, the catalog fetches them, the verbatim
quote checks out against the section that was fetched. Every gate passes,
because no gate knows what was actually said. The reader seeing the transcript
before sending it is the only check on this path, so the API refuses to skip it.

`503` means no `DEEPGRAM_API_KEY` — voice is unconfigured, the rest of the
assistant is unaffected, and typing still works. `413` is audio over 10 MB.

Keyterm prompting boosts statutory vocabulary ("cognizable", "bailable", the
Act names) built from the catalog, because a general speech model turns those
into commoner words rather than failing visibly.

**Bengali is `bn`, nova-3 only, and is not in Deepgram's `multi`
code-switching set** (en, es, fr, de, hi, ru, pt, ja, it, nl). Mixed
Bangla-English has no single-model answer; `RAG_STT_LANGUAGE` picks one, and
`scripts/deepgram_stt.py --compare` measures what the choice costs.

### GET `/api/retrieve?q=...&k=5&rerank=true`

The retrieval half alone, with dense and reranker scores per hit. `rerank=false`
gives the dense-only ordering — the same code path the eval scripts measure, so
served and measured numbers cannot drift.

## Configuration

`backend/.env.local` (gitignored) holds `GROQ_API_KEY`. OpenRouter and Gemini
keys are optional fallbacks.

| var | default | |
|---|---|---|
| `RAG_GENERATOR` | `qwen` | alias returned by `GET /api/models` |
| `RAG_GROQ_MODEL` | `qwen/qwen3.8-27b` | direct Groq model id |
| `RAG_LLM_BACKEND` | `groq` | `groq`, `openrouter`, `gemini`, `inception` |
| `RAG_INCEPTION_MODEL` | `mercury-2.5` | Inception Mercury default |
| `RAG_PLANNER_MODEL` | `qwen` | the planner — decides category and coordinates |
| `RAG_PLAN` | `1` | `0` falls back to the old pattern router |
| `RAG_ROUTER_MODEL` | `lfm` | old router's model, used only when `RAG_PLAN=0` |
| `RAG_DENSE_K` / `RAG_FINAL_K` | `20` / `5` | candidates reranked / sections returned |
| `RAG_STT_MODEL` | `nova-3` | Deepgram model; Bengali needs nova-3 |
| `RAG_STT_LANGUAGE` | `en` | `en`, `bn`, or `multi` (no Bengali in `multi`) |
| `RAG_WARMUP` | `1` | `0` skips model loading at startup |
| `CORS_ORIGINS` | `http://localhost:3000` | comma-separated |

Generator routes must enforce JSON schema. The default Groq Qwen route passed
the strict-schema probe in `scripts/groq_chat.py --json`. OpenRouter fallbacks
still set `provider.require_parameters`, so providers that ignore schemas fail
loudly instead of returning prose.

## Layout

```
app/rag/
  config.py     models, paths, k values
  encoder.py    BGE-M3 query/document embedding
  store.py      FAISS index + chunk records
  reranker.py   cross-encoder (E4)
  pipeline.py   retrieve() — dense, dedupe, rerank
  llm.py        Groq / OpenRouter / Gemini, JSON-schema output
  catalog.py    structural read side of catalog.sqlite — exact fetch by
                coordinates, parameterized SQL, empty on a bad coordinate
  plan.py       the planner: question -> category + coordinates, then
                validated against the catalog
  execute.py    one executor per category; plan -> Answer
  answer.py     prompt, generation, gating, refusal
  verify.py     C2 + C3 citation gates
  citations.py  chunk metadata -> Citation
  library.py    browsable corpus (older in-memory view; catalog.py supersedes
                it for structural queries)
  refine.py     the pattern router, used only when RAG_PLAN=0
  speech.py     Deepgram STT — audio -> transcript, never an answer
  present.py    Answer -> AssistantMessage
```

Two read models, two jobs: **FAISS** answers "which provisions resemble this
question", **`catalog.sqlite`** answers "which provisions *are* these". Both are
rebuilt from the parsed JSON, which stays the only source of law.

`pipeline.retrieve()` is shared by the API and the eval scripts by design: they
previously diverged, and the logged numbers described a pipeline nothing could
serve (`experiments.md` E7).
