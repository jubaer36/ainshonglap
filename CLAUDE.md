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

Work only with the acts in **`Laws/main/`** — the Code of Criminal Procedure
1898, the Penal Code 1860, and the Evidence Act 1872, plus their parsed JSON in
`Laws/main/parsed/`.

The other ~31 PDFs sitting in `Laws/` are out of scope for now, and include
duplicate copies of acts already in `main/`. Do not glob `Laws/*.pdf` — it would
pull in duplicates and out-of-scope material. Everything outside `Laws/main/`
is gitignored.

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

Secrets live in `backend/.env.local` (gitignored): `GROQ_API_KEY` (default
generator and planner), `INCEPTION_API_KEY` (Mercury, the quota fallback),
`GEMINI_API_KEY_2` (conversation summariser, on its own key), `DEEPGRAM_API_KEY`
(voice input), plus optional `GEMINI_API_KEY` and `OPENROUTER_API_KEY`.

**Free-tier budgets are a real constraint.** Groq's daily token cap is per model
and has been exhausted once by verification runs. Prefer offline checks —
imports, `tsc --noEmit`, eslint, direct tests of pure logic — and use
`plan_cases.py --only` rather than re-running a whole suite to confirm a narrow
fix. `app/rag/quota.py` records exhaustion per model so the picker can grey a
route out and `/api/query` can fall back.

## The pipeline

Each stage is checked against something external, not self-reported:

```bash
# scripts/ is the pipeline and its tools.
# scripts/verify/ checks correctness; scripts/experiments/ measures.
python scripts/parse_law_pdf.py --all    # PDFs    -> Laws/main/parsed/*.json
python scripts/verify/verify_parsed.py          #   check vs the PDF + bdlaws oracle
python scripts/chunk_sections.py         # parsed  -> Laws/main/chunks/*.json
python scripts/verify/verify_chunks.py          #   check vs the parsed JSON
python scripts/build_index.py            # chunks  -> BGE-M3 vectors + FAISS
python scripts/build_catalog.py          # parsed  -> Laws/main/catalog.sqlite
python scripts/verify/verify_catalog.py         #   check vs the parsed JSON

# any legal PDF, not just the three acts; --backend auto OCRs scans via Docling
python scripts/parse_law_pdf.py --pdf "Laws/The Arms Act, 1878.pdf" \
    --name "The Arms Act, 1878" --act-no "XI of 1878" --slug arms

python scripts/plan_demo.py          # walk the planner question by question
python scripts/experiments/plan_cases.py         #   graded: category + scope (E13)
python scripts/eval_retrieval.py     #   Recall@k / MRR / Act accuracy
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
```

Two read models, two jobs. **FAISS** answers "which provisions resemble this
question"; **`catalog.sqlite`** answers "which provisions *are* these" — exact
section, range, chapter, Act. Both are rebuildable from the parsed JSON, which
stays the only source of law. A model names coordinates
(`app/rag/plan.py`); `app/rag/catalog.py` fetches them with parameterized SQL.
The model never writes SQL, and a coordinate the catalog does not have comes
back empty rather than as the nearest match.

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
