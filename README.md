# আইনসংলাপ (AinShonglap)

A Bangladesh criminal-law assistant that answers from the statute text and cites
the exact sections it used. It covers the Code of Criminal Procedure 1898, the
Penal Code 1860 and the Evidence Act 1872; admins can add more sources.
When the Acts do not support an answer, it says so instead of guessing.

> Legal information, not legal advice.

## Run it

You need Python 3.12 (conda), Node 22, a Supabase project, and Groq and
OpenRouter API keys.

**1. Backend**

```bash
conda create -n ainshonglap python=3.12 && conda activate ainshonglap
cd backend
pip install -r requirements.txt
cp .env.example .env.local        # fill in the keys (see below)
uvicorn app.main:app --reload     # http://localhost:8000  (/docs, /health)
```

**2. Frontend**

```bash
cd frontend
cp .env.example .env.local        # NEXT_PUBLIC_SUPABASE_URL / _ANON_KEY
npm install
npm run dev                       # http://localhost:3000
```

**First time only — load the laws into Supabase.** The `Laws/` folder is not in
the repository (earlier commits contain `Laws/main/`); put the corpus files in
`Laws/main/` first. In the Supabase SQL editor run `backend/db/schema.sql`, then
each file in `backend/db/migrations/` in order. Then, from `backend/`:

```bash
python scripts/load_supabase.py --all
python scripts/load_supabase.py --embedder nemotron-3-embed --all
```

**Keys** (`backend/.env.local`): `GROQ_API_KEY`, `OPENROUTER_API_KEY` (search)
and `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` are required. The rest are
optional — each one turns on a feature or gives a job its own quota:
`GROQ_API_KEY_2`/`_3`, `GROQ_API_KEY_PLANNER`, `GROQ_API_KEY_SUMMARIZER`,
`GROQ_API_KEY_FALLBACK`, `OPENROUTER_API_KEY_2`, `GEMINI_API_KEY_3`
(conversation names), `DEEPGRAM_API_KEY` (voice), `ADMIN_EMAILS`.

**Or run the backend in Docker** (~435 MB, ~90 MB of memory; no models inside):

```bash
docker build -f backend/Dockerfile -t ainshonglap-backend:deployable .
docker run -d --name ainshonglap-api -p 8000:8080 \
  --env-file backend/.env.local ainshonglap-backend:deployable
```

## How it works

```text
question
  -> planner (LLM, strict JSON): what kind of question, and where to look
  -> validated against the catalog — unknown sections return nothing, never a guess
  -> exact fetch by section / chapter / Act   OR   meaning search (OpenRouter)
  -> generator (LLM): answer as claims, each with a section and a quote
  -> every quote must appear word for word in the cited section, or is dropped
  -> answer with citations, or a refusal
```

- **Laws**: parsed from the PDFs into sections, checked against the PDFs and an
  independent copy of the Acts, stored in Supabase (Postgres + pgvector). Local
  copies are a logged fallback.
- **Search**: nemotron-3-embed on OpenRouter, exact search over the stored
  vectors. No model runs locally. If OpenRouter is down or out of daily quota,
  meaning search says so; exact section lookups still work.
- **Models**: Groq `gpt-oss-120b` plans and answers by default; others can be
  picked in the UI. Every model call has a fallback on its own key.
- **Voice and images**: speech (Deepgram) or a photo of a page (Groq
  `qwen3.8-27b`, max 3 MB) becomes text you check before sending.
- **Stack**: FastAPI backend, Next.js frontend, optional Supabase accounts
  (guests store nothing).

## Adding a document

In the app (admins): upload a PDF, compare four sample pages with what each
parser read (PyMuPDF for numbered statutes, Docling for anything else), pick
one. It is parsed, indexed and live within a minute — no restart. Or:

```bash
python scripts/ingest_document.py --pdf "Laws/main/The Arms Act, 1878.pdf" \
    --slug arms --name "The Arms Act, 1878" --act-no "XI of 1878"
python scripts/remove_act.py --act arms
```

The three core Acts cannot be removed.

## Evaluation and results

Retrieval is scored on 608 external questions with known answers
(`Laws/bangladesh-legal-qa-dataset`): did the right section come back, and from
the right Act.

```bash
python scripts/eval_retrieval.py --split single_hop   # 222 OpenRouter requests; all 608 without --split
```

| retrieval | R@1 | R@5 | R@20 | right Act |
|---|---|---|---|---|
| `nemotron-embed` (default) | **48.4%** | **75.0%** | **86.5%** | 88.5% |
| local BGE-M3 + reranker (on `main`, for comparison) | 41.8% | 59.0% | 71.4% | 87.5% |

Each question costs one OpenRouter request (free tier: 1,000 a day per account
with credits, 50 without). `RAG_RETRIEVAL=nemotron-embed+rerank` adds the
Nemotron reranker (a second request; no gain measured on 119 questions).
±3.7 points is noise at this size. Every experiment — including the ones that
did nothing — is in `experiments.md`.

## Limitations

- The three core Acts plus the Arms Act, 1878 and the Citizenship Act, 1951 are
  loaded; no amendments or case law. Retrieval was measured on the core three.
- A question raising two legal issues can come back with sections for only one.
- Answers that need reasoning from a rule to its consequence ("if I can't prove
  I'm innocent, am I guilty?") are refused even when the right sections are found.
- The CrPC Second Schedule (bailable / cognizable) is not in the corpus.
- Answer quality is not measured yet — only retrieval is.

Next steps: `plan.md`.

## More detail

- `backend/API.md` — endpoints and configuration
- `plan.md` / `progress.md` — what is next / what is built
- `experiments.md` — every experiment and result
- `parsing-plan.md`, `chunking-plan.md`, `retrieval-plan.md`,
  `generation-plan.md`, `query-routing-plan.md` — design decisions
