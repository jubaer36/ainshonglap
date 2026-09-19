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
`experiments.md` (experiment log — read before re-testing something),
`similar-projects.md` (what comparable repos chose at each stage),
`verification.md` (how both stages are checked).

**Ignore `findings.md`.** It is old scratch notes, not a source of truth.

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

Secrets live in `backend/.env.local` (gitignored).

## The pipeline

Each stage is checked against something external, not self-reported:

```bash
python scripts/parse_law_pdf.py --all    # PDFs    -> Laws/main/parsed/*.json
python scripts/verify_parsed.py          #   check vs the PDF + bdlaws oracle
python scripts/chunk_sections.py         # parsed  -> Laws/main/chunks/*.json
python scripts/verify_chunks.py          #   check vs the parsed JSON
python scripts/build_index.py            # chunks  -> BGE-M3 vectors + FAISS
python scripts/eval_retrieval.py         #   Recall@k / MRR / Act accuracy
python scripts/rerank.py --topk 20       # + cross-encoder rerank, with delta
python scripts/experiment_retrieval.py --what all   # A/B prefix, chunk budget
```

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


