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
`verification.md` (how parsed output is checked).

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


