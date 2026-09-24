# Project context

Bangladesh Criminal Law Q&A assistant — source-grounded answers with
section-level citations.

## Read these, in this order

- **`entry-assessment.md`** — the actual task. This is the project brief: what
  is being built, what the deliverables are, how it will be judged. Treat it as
  the spec.
- **`plan.md`** — the pipeline steps and which one is current.
- **`progress.md`** — what has been built so far, and why.

Supporting detail, read only when working on that area:
`parsing-plan.md` (parser options tested and the decision),
`chunking-plan.md` (chunking options and the decision),
`retrieval-plan.md` (embedder and retrieval options and the decision),
`retrieval-baseline.md` (measured retrieval numbers),
`generation-plan.md` (answer generation, citation enforcement, abstention options),
`query-routing-plan.md` (model-authored plans, SQLite catalog, broad coverage),
`experiments.md` (experiment log — read before re-testing something),
`similar-projects.md` (what comparable repos chose at each stage),
`verification.md` (how parsing and chunking are checked).

## Scope

The corpus is what is in **`Laws/main/`**. Three **core Acts** are permanent —
the Code of Criminal Procedure 1898, the Penal Code 1860, and the Evidence Act
1872 (`config.CORE_ACTS`); nothing may remove them. Other sources are added one
at a time, deliberately — through the app's add-source review or
`scripts/ingest_document.py` — and can be removed again. Loaded now: the three
core Acts plus the Arms Act, 1878 (`arms_act`) and the Citizenship Act, 1951
(`citizenship_act`).

The ~31 other PDFs in `Laws/` are candidates, not corpus, and include duplicate
copies of acts already in `main/` (a `(bdlaws.minlaw.gov.bd copy)` beside the
original). Do not glob `Laws/*.pdf` — it would pull in duplicates and material
nobody chose to add.

**`Laws/` is not tracked by git** from the commit that dropped it onward — PDFs,
parsed JSON, chunks, index and catalog alike. Earlier commits still contain
`Laws/main/`. A fresh clone therefore has no corpus files; the running app does
not need them (it reads Supabase), but the pipeline scripts do.

`Laws/bdlaws/bdlaws.jsonl` is an independently scraped copy of all Bangladesh
acts. It is a **validation oracle only** — never a content source. Statutory
text and page numbers must come from the PDFs, so that a citation and the text
it cites come from the same document.

## Environment

Everything runs in one conda env — no venv:

```bash
conda activate ainshonglap
cd backend
uvicorn app.main:app --reload            # API on :8000
```

Or the backend in Docker (CPU-only; `backend/Dockerfile`, built from the repo
root; no source management inside it). Deployment: `gcp-deployment-plan.md`.

```bash
docker build -f backend/Dockerfile -t ainshonglap-backend .
docker run -d --name ainshonglap-api -p 8000:8080 --env-file backend/.env.local ainshonglap-backend
```

Secrets live in `backend/.env.local` (gitignored). Every job that runs on
*every* question has a key nothing else draws on:

| key | used by |
|---|---|
| `GROQ_API_KEY`, `_2`, `_3` | the generators, in rotation (see below) |
| `GROQ_API_KEY_PLANNER` | the planner alone — deliberately outside that rotation |
| `GROQ_API_KEY_FALLBACK` | the system-wide fallback (gpt-oss-120b) for every model call — answers, planner, chapter lookup, summariser, naming |
| `GROQ_API_KEY_SUMMARIZER` | the conversation summariser (gpt-oss-120b), and nothing else |
| `GEMINI_API_KEY_3` | naming a conversation, and nothing else |
| `INCEPTION_API_KEY` | Mercury, a selectable generator |
| `DEEPGRAM_API_KEY` | voice |
| `OPENROUTER_API_KEY`, `_2` | vision, OpenRouter generators, and the retrieval profiles that run on OpenRouter (rotation + failover, like Groq) |
| `SUPABASE_*`, optional `GEMINI_API_KEY` | accounts and storage, optional generator |
| `ADMIN_EMAILS` | signed-in users allowed to add/remove sources from the frontend |

`app/rag/keyring.py` discovers every `GROQ_API_KEY*` and uses them in turn:
rotation per call so the daily caps drain evenly, immediate failover to the next
key on a 429, and sleeping only when every key has refused. Groq's budget is per
key *and* per model, so `quota.py` records exhaustion per (model, key) and a
model greys out only when all of its keys are spent. Adding a fourth key is an
`.env.local` edit and nothing else.

Every request logs one line per step — planner in and out, retrieval hits,
generator claims kept and dropped, memory, Supabase writes. `RAG_TRACE=0`
silences it. Read that before guessing which stage produced a surprising
answer; a refusal can mean retrieval fetched the wrong sections, the generator
declined on the right ones, or the citation gates killed every claim, and those
look identical from outside.

**Free-tier budgets are a real constraint.** Groq's daily token cap is per model
and per key, and has been exhausted once by verification runs. Three keys triple
it; they do not make it infinite. Prefer offline checks — imports,
`tsc --noEmit`, eslint, direct tests of pure logic — and use
`plan_cases.py --only` rather than re-running a whole suite to confirm a narrow
fix. `app/rag/quota.py` records exhaustion per (model, key) so the picker can
grey a route out once every key is spent, and `/api/query` can fall back.

## The pipeline

Each stage is checked against something external, not self-reported:

```bash
# scripts/ is the pipeline and its tools.
# scripts/verify/ checks correctness; scripts/experiments/ measures.
python scripts/parse_law_pdf.py --all    # PDFs    -> Laws/main/parsed/*.json
python scripts/verify/verify_parsed.py          #   check vs the PDF + bdlaws oracle
python scripts/chunk_sections.py         # parsed  -> Laws/main/chunks/*.json
python scripts/verify/verify_chunks.py          #   check vs the parsed JSON
python scripts/build_index.py            # chunks  -> BGE-M3 vectors + FAISS (reuses known vectors; --full re-embeds)
python scripts/build_catalog.py          # parsed  -> Laws/main/catalog.sqlite
python scripts/verify/verify_catalog.py         #   check vs the parsed JSON
python scripts/load_supabase.py --all    # read models -> Supabase law_* tables (002)
python scripts/verify/verify_supabase.py        #   check vs the local read models
# retrieval profile `nemotron-embed` (E21): its vectors, beside BGE-M3's.
# Costs OpenRouter requests for new chunks only (~1 per 32). Migration 003 first.
python scripts/build_index.py --embedder nemotron-3-embed
python scripts/load_supabase.py --embedder nemotron-3-embed --all

# adding a NEW document: parse, review 4 sampled pages as image vs PyMuPDF vs
# Docling (Laws/review/<slug>/review.html), then choose. PyMuPDF = numbered
# statute; Docling = anything else, cited by passage. The person decides.
python scripts/ingest_document.py --pdf "Laws/main/The Arms Act, 1878.pdf" \
    --slug arms --name "The Arms Act, 1878" --act-no "XI of 1878"
# removing one: Supabase first (live within a minute), then the local fallback
python scripts/remove_act.py --act arms

python scripts/plan_demo.py          # walk the planner question by question
python scripts/experiments/plan_cases.py         #   graded: category + scope (E13)
python scripts/eval_retrieval.py     #   Recall@k / MRR / Act accuracy (--profile: see below)
python scripts/rerank.py --topk 20   # + cross-encoder rerank, with delta
python scripts/experiments/fit_threshold.py      #   abstention operating curve (E10)
python scripts/experiments/experiment_retrieval.py --what all   # A/B prefix, chunk budget
python scripts/experiments/experiment_granularity.py            # retrieval unit (E5)
python scripts/experiments/experiment_abstention.py --n 20      # over-answering (E8)
python scripts/groq_chat.py --json                              # Groq schema probe (E11)
python scripts/inception_chat.py --json                         # Mercury schema probe (E14)
python scripts/deepgram_stt.py --record 6 --expect "..."        # voice: number accuracy, WER
python scripts/chat_demo.py                                     # multi-turn, shows the memory
python scripts/answer_demo.py                                   # full path, one question at a time
python scripts/check_questions.py --store supabase             # 15 questions, every planner path, step by step
```

Two read models, two jobs: meaning search ("which provisions resemble this
question") and the catalog ("which provisions *are* these" — exact section,
range, chapter, Act). **Both are served from Supabase** (`law_*` tables,
migration 002; `app/rag/corpus_db.py`). The local `catalog.sqlite` and FAISS
index are **fallbacks only**, used when Supabase fails and logged as `FALLBACK`
every time (`app/rag/fallback.py`; `/health` shows it too). Everything is
rebuildable from the parsed JSON, which stays the only source of law: build
locally, then `load_supabase.py` + `verify_supabase.py`. A model names coordinates
(`app/rag/plan.py`); `app/rag/catalog.py` fetches them with parameterized SQL.
The model never writes SQL, and a coordinate the catalog does not have comes
back empty rather than as the nearest match.

**Retrieval has three profiles** (E21, `config.RETRIEVAL_PROFILES`), chosen by
`RAG_RETRIEVAL` or `retrieval` on one `/api/query` request:
`bge+nemotron-rerank` (best R@1 50.8 and Act accuracy 91.6), `bge+bge-rerank`
(the default, R@1 41.8, no API) and `nemotron-embed` (best R@20 86.5, no
reranker). Supabase holds both vector sets: BGE-M3 in `law_chunks`
(1024-dim), nemotron in `law_alt_chunks` (2048-dim, migration 003). A question
searches only its profile's set. The two OpenRouter profiles cost **one request
per question** against the free-model daily cap (1,000 per account with credits,
50 without) and fall back to the default, logged as `FALLBACK`, when OpenRouter
cannot serve. Evaluating them costs a request per question too:
`eval_retrieval.py --profile` on all 608 is most of a day's cap.

**Core Acts are fixed; everything else is a source.** The CrPC, the Penal Code
and the Evidence Act (`config.CORE_ACTS`) can never be removed — the service,
the API and `remove_act.py` all refuse. Other sources are added and removed
from the frontend by admins (`app/ingest/sources.py`, `/api/sources`): upload,
review sampled pages against both parsers, choose, and the job indexes it.
Embedding reuses every known vector (`build_index.build`), so adding a source
embeds only that source. Only where the pipeline exists: the Docker image
(`backend/Dockerfile`, CPU-only, no `scripts/`, PyMuPDF or Docling) reports
`manage: false` and refuses with 503 — sources are added locally and reach the
deployment through Supabase. Deployment: `gcp-deployment-plan.md`.

**Which Acts exist is data, not code.** `app/rag/acts.py` answers every
"which Acts do we hold?" from the catalog: citation keys, short names, the
planner's scope and its allowed `acts` values, the "outside …" messages, and
spotting an Act named in text. Nothing lists Acts by name — add or remove one
and all of that follows within a minute, with no code change.

Handlers that do blocking work (retrieval, generator calls) are plain `def`, not
`async def`: FastAPI runs an `async` handler on the event loop, and one blocking
handler freezes every other request. Warmup loads the retrieval models in a
worker thread so the server answers immediately and `/health` reports `warming`.

Serving: `uvicorn app.main:app --reload`. The API and the eval scripts share
`app/rag/pipeline.retrieve()` — keep it that way. They were once separate, and
the logged numbers ended up describing a pipeline nothing could serve (E7).
Endpoints and configuration: `backend/API.md`.

Generator routes must enforce JSON schema. Check the default with
`python scripts/groq_chat.py --json`; check an OpenRouter fallback with
`python scripts/openrouter_chat.py --json --model <alias>`. A route that ignores
the schema returns prose, which the pipeline would surface as a false refusal.

## Accounts and storage

Optional Supabase auth. Guests store nothing anywhere; signed-in sessions store
conversations, per-turn context and page images. Schema in `backend/db/`, applied
by hand in the Supabase SQL editor (`schema.sql`, then `migrations/`).

**The service-role key bypasses Row Level Security**, so `app/db/conversations.py`
is the isolation boundary, not the policies. Every statement filters by
`user_id`, including ones already scoped by `conversation_id`. `user_id` always
comes from a verified token, never from a request body.

## Conventions

- Parsing and chunking are deterministic: re-running reproduces byte-identical
  output.
- Verifiers re-read the source rather than trusting the previous stage's own
  statistics. A parser that drops text can drop it from its own accounting too.
- When a check and the data disagree, **suspect the check first** — several
  "failures" turned out to be bugs in the checker.
- **Nothing is adopted without moving a number on the 608-question eval set.**
  At n=608 the 95% confidence interval is ±3.7pp, so smaller gaps are not
  results. Two techniques taken from published work were later found to do
  nothing here — read `experiments.md` before re-testing something.


And remember you are not allowed to commit in any way. The commits will be done manually by me . At best you can provide the instructions to commit. Basically don't worry about git and commits at all . I will handle that
