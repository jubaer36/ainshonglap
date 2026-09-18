# Progress

## Frontend
- Next.js chat UI, wired to a `POST /api/query` contract so UI and backend could be built independently.
- Renders structured answers (paragraphs, numbered steps, bullets) rather than flat text — legal answers like FIR filing steps need the structure.
- Citations render inline with a reference panel, so a claim traces back to its source without leaving the chat.
- TS types mirror the backend's Pydantic schemas by hand.

## Backend
- FastAPI service with the real route shapes (`/health`, `POST /api/query`, `GET /api/citations`).
- `/api/query` still returns canned data — a deliberate stand-in so the full UI could be built and tested before the retrieval pipeline existed. Isolated to one module, so replacing it needs no route or frontend change.

## Legal documents
- 34 PDFs under `Laws/` — CrPC 1898 plus related criminal-law acts, scoped to the assessment brief. Provenance per file in `Laws/SOURCES.md`.
- Duplicates (CrPC ×3, 5 acts ×2) still on disk; not yet deduped.
- The 3 acts actually ingested live in `Laws/main/` and are tracked in git alongside their parsed output; the rest is gitignored.

## LLM-only baseline (step 4)
- Dedicated `ainshonglap` conda env; scripts for a local model (Qwen2.5-VL-3B on GPU) and a hosted one (Gemini 2.5 Flash), each with batch and REPL modes.
- Same 5 legal questions to both: Qwen hallucinated section content or citations on 3 of 5; Gemini got all 5 right with accurate citations.
- Conclusion: the generator is not the weak link — retrieval will be. Matches `findings.md`, which puts the embedder ahead of the generator for legal RAG.

## PDF parsing (step 2)
- Evaluated 8 parsers by running each against the actual PDFs, not on reputation. Docling loses text; LandingAI ADE is capable but its elements straddle section boundaries; `bluebell` is an authoring format, not a parser. Full comparison in `parsing-plan.md`.
- Adopted PyMuPDF plus a rule-based legal-structure parser (`backend/scripts/parse_law_pdf.py`). Text and hierarchy both come from the PDF.
- Output: `Laws/main/parsed/<act>.json` — Act → Part → Chapter → Section → Subsection → Clause, plus Explanation/Illustration/Exception/Proviso, with page numbers and full ancestry inline on every section.
- Parsed CrPC (508 sections), Penal Code (557), Evidence Act (182). Deterministic — regenerating reproduces byte-identical files.
- Trailing endnote pages are excluded: the CrPC closes with 24 pages of amendment notes that were being appended to its last section, leaving s.565 at 10,918 words. Now 280. Verified nothing statutory was dropped — 1,218 of 1,221 removed lines are 7.6pt against a 9.0pt body, all past the final section, none a section opener.
- Hard cases solved: sections split across page boundaries (300 of them across the three acts), the Penal Code's gloss column swapping sides every page, and contents pages that mimic real sections.

## Verification (step 3)
- Found `Ashik2380/bdlaws` on HF — an independently scraped copy of all 1,467 Bangladesh acts. Used strictly as an oracle, never as a source: complete text, zero hierarchy.
- `backend/scripts/verify_parsed.py` runs 7 checks, all by re-reading the PDF rather than trusting the parser's own stats. Method and results in `verification.md`.
- Results: **zero phantom sections in any act**, zero real numbering gaps, 100% line-level text recovery on Penal and Evidence (99.99% CrPC). Evidence Act matches its oracle exactly, 182 for 182, and passes every check.
- The suite earned its keep immediately — contents pages leaking into the body, gloss notes silently dropped when they matched no section, and two bugs in the checker itself. All invisible in the parser's own summary stats.
- It also showed the limit of coverage checking: s.565's 24 pages of endnotes counted as *recovered* because they landed somewhere. Coverage catches deletion; only size and shape checks catch misfiling.

## Chunking (step 5)
- Section-level chunking, split at subsection/clause boundaries when oversized, hierarchy path prepended to each chunk's text. Options and evidence in `chunking-plan.md`.
- 1,321 chunks from 1,247 sections: CrPC 536, Penal 583, Evidence 202. Median 133-172 tokens.
- `verify_chunks.py` checks the output against the parsed JSON independently — 3/3 acts pass all seven checks: text reproduced as an ordered word sequence, no duplication, no orphans, unique ids, metadata and prefix in agreement.
- It caught three bugs the chunker reported as clean, including duplicate chunk ids that would have silently overwritten entries in a vector store. The first fidelity check was itself wrong — a set comparison, which passes even when text is duplicated or scrambled.

## What this establishes
- Parsing, verification and chunking are done for the 3 main acts, with correctness measured rather than assumed at each stage.
- Next: embed the chunks and stand up a vector store (step 6), likely after parsing the remaining 25 acts so the index is built once.
