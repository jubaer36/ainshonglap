# phase-1-assessment

Bangladesh Criminal Law Q&A Assistant — source-grounded answers over the
Code of Criminal Procedure, 1898 and related laws, with section-level
citations.

## Project layout

- `frontend/` — Next.js UI (see `frontend/README.md`)
- `backend/` — FastAPI API, currently serving canned responses behind the
  real `/api/query` contract (see `backend/README.md`)
- `Laws/` — source legal PDFs; see `Laws/SOURCES.md` for provenance
- `Laws/main/parsed/` — parser output, one hierarchical JSON per act
  (Act → Part → Chapter → Section → Subsection → Clause)

## Documents

- `plan.md` — pipeline steps and where each one stands
- `progress.md` — what has been built, and why
- `parsing-plan.md` — PDF parsing: options tested, decision, results, gaps
- `verification.md` — how the parsed JSON is verified, and what is outstanding
- `chunking-plan.md` — chunking options, evidence, and the decision

## Parsing the law PDFs

```bash
conda activate ainshonglap
cd backend
python scripts/parse_law_pdf.py --all       # CrPC, Penal Code, Evidence Act
python scripts/parse_law_pdf.py --act crpc  # one act
```

Writes `Laws/main/parsed/<act>.json`. Then verify against the source PDF and an
independent oracle — see `verification.md` for what each check does:

```bash
python scripts/verify_parsed.py
```

Then chunk for retrieval, and verify that too:

```bash
python scripts/chunk_sections.py     # -> Laws/main/chunks/<act>.json
python scripts/verify_chunks.py
```

## Running locally

Backend and frontend run as two separate processes; start the backend
first since the frontend calls it over HTTP.

### Backend

```bash
conda create -n ainshonglap python=3.12   # first time only
conda activate ainshonglap
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload
```

(see `backend/README.md` for details, incl. the local-model experiment scripts)

Runs at http://localhost:8000 (API docs at `/docs`, health check at `/health`).

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Runs at http://localhost:3000. It reads the backend URL from
`frontend/.env.local` (`NEXT_PUBLIC_API_URL`, defaults to
`http://localhost:8000`) — no changes needed if the backend is running
locally on its default port.

With both running, open http://localhost:3000.
