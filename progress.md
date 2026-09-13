# Progress

## Frontend
- Built a Next.js chat UI as the user-facing entry point for asking legal questions.
- Wired it to a `POST /api/query` contract so UI and backend can be developed independently against a shared shape.
- Rendering supports structured answer content — paragraphs, numbered steps, bullet lists — because legal answers (e.g. FIR filing steps) need more than a flat text blob.
- Citations render as inline previews and a reference panel, so a claim in an answer can be traced back to its source without leaving the chat.
- Quick actions (copy / preview citation / ask followup) added to reduce friction for common next steps a user takes after an answer.
- TypeScript types (`lib/types.ts`) hand-mirror the backend's Pydantic schemas field-for-field, keeping both sides in sync until codegen is worth adding.

## Backend
- FastAPI service scaffolded with the real route shapes (`/health`, `POST /api/query`, `GET /api/citations`) so the frontend has a stable contract to build against before retrieval/LLM logic exists.
- `/api/query` is currently backed by canned data with keyword matching (`app/data/mock_responses.py`) — a stand-in that returns realistic-shaped answers so the full UI (citations, blocks, quick actions) could be built and tested end-to-end before the retrieval pipeline exists.
- Isolating the mock logic to one module was deliberate: replacing it with real ingestion/retrieval/generation should require no changes to the route or the frontend.
- Only 2 Q&A examples are seeded, enough to exercise every content-block/citation type in the schema, not meant as coverage.

## Legal documents
- Collected 30 PDFs under `Laws/` — the Code of Criminal Procedure, 1898 plus related criminal-law acts (Penal Code, Evidence Act, Police Act, Arms Act, etc.) — scoped to match the assessment's brief rather than pulling in unrelated civil/tax/corporate law.
- Sourced from bdcode.gov.bd and bdlaws.minlaw.gov.bd and logged in `Laws/SOURCES.md` with each file's origin URL, so provenance is traceable per document.
- Renamed files from their source-URL-derived names to their actual act titles, since the original names gave no indication of content.
- Found duplicate copies of the same act (CrPC had 3 copies, 5 other acts had 2 each, downloaded from different sources) — deduped, canonical copy kept per act.

## LLM-only baseline (step 4)
- Added dedicated `ainshonglap` conda env (Python 3.12, torch cu128) for local model work.
- Added local inference scripts (`backend/scripts/local_llm_test.py`, `local_llm_chat.py`) running Qwen2.5-VL-3B-Instruct on-GPU for text-only Q&A.
- Added Gemini 2.5 Flash scripts (`backend/scripts/gemini_test.py`, `gemini_chat.py`) using the Google AI Studio API.
- Both give batch (fixed test questions) and interactive (REPL) modes.
- Ran both models against the same 5 sample legal questions: Qwen2.5-VL-3B hallucinated section content/citations on 3 of 5; Gemini 2.5 Flash answered all 5 correctly with accurate citations.
