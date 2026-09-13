# phase-1-assessment

Bangladesh Criminal Law Q&A Assistant — source-grounded answers over the
Code of Criminal Procedure, 1898 and related laws, with section-level
citations.

## Project layout

- `frontend/` — Next.js UI (see `frontend/README.md`)
- `backend/` — FastAPI API, currently serving canned responses behind the
  real `/api/query` contract (see `backend/README.md`)
- `Laws/` — source legal PDFs; see `Laws/SOURCES.md` for provenance

## Running locally

Backend and frontend run as two separate processes; start the backend
first since the frontend calls it over HTTP.

### Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

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
