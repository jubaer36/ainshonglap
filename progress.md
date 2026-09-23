# Progress

What is built, and why. Measurements: `experiments.md`. Next: `plan.md`.

## At a glance

| | |
|---|---|
| Corpus | 3 core Acts — CrPC 1898 (508 sections), Penal Code 1860 (557), Evidence Act 1872 (182). Others can be added from the app |
| Storage | Supabase (Postgres + pgvector); local SQLite + FAISS as a logged fallback |
| Retrieval | 3 profiles — best R@1 50.8%, best R@20 86.5% (E21) |
| Answers | Planner → validated fetch → cited claims → quote check → answer or refusal |
| App | FastAPI + Next.js; voice, images, memory, optional accounts, admin-managed sources |
| Deploy | CPU Docker image (~6 GB), GCP Cloud Run plan |

## Parsing and verification

Eight parsers were tried on the real PDFs; a rule-based parser on PyMuPDF won.
It handles sections split across pages, the Penal Code's side column, fake
sections in contents pages, and CrPC endnotes. Output is deterministic.

Checks re-read the PDF and an independent copy of the Acts: no phantom
sections, no numbering gaps, 100% line recovery (CrPC 99.99%). Three early
"failures" were bugs in the checkers — so the rule is: suspect the check first.

## Retrieval

Section chunks plus subsection chunks, embedded with BGE-M3.

- The reranker was the first real gain: +9.2pp R@1 (E4).
- Hierarchy prefixes and chunk size changed nothing (E2, E3).
- The API and the eval scripts share one `retrieve()`, so measured = served.
- Three profiles now (E21), picked with `RAG_RETRIEVAL`:

| profile | R@1 | R@20 | right Act | |
|---|---|---|---|---|
| `bge+bge-rerank` | 41.8 | 71.4 | 87.5 | default, all local |
| `bge+nemotron-rerank` | 50.8 | 71.4 | 91.6 | reranker on OpenRouter |
| `nemotron-embed` | 48.4 | 86.5 | 88.5 | search on OpenRouter, no reranker |

The OpenRouter two cost one call per question against a daily cap, and fall
back to the default when OpenRouter fails. Nemotron's vectors (2048-dim) live
in their own Supabase table next to BGE-M3's (1024-dim).

## Answering

- **Planner** (Groq gpt-oss-120b, own key): returns question type and
  coordinates only — never law text, never SQL. Each coordinate is checked
  against the catalog; unknown ones return nothing. Six types: meta,
  structural, semantic, document, out-of-scope, general.
- **Generator**: answers as claims, each with a section and a quote. A claim
  survives only if the section was retrieved and the quote is in it word for
  word. No claims left → refusal. This removes invented citations by
  construction.
- **Which Acts exist** is read from the catalog, so adding a source needs no
  code change.

## Models and keys

- Every `GROQ_API_KEY*` is used in rotation; a 429 moves to the next key at once.
  Quota is tracked per (model, key).
- Planner, summariser and fallback each have their own key, so one busy job
  cannot starve another.
- Every model call falls back to gpt-oss-120b on `GROQ_API_KEY_FALLBACK`,
  logged as `FALLBACK`; a substituted answer says which model answered.

## Conversation

- **Memory**: a short rolling summary plus the previous question and answer word
  for word. Section numbers are copied by code, never paraphrased. The summary
  moved to gpt-oss-120b when Gemini took 34–64s per call (E20).
- **The answer never waits for memory.** Memory updates afterwards; a question
  sent meanwhile waits up to 25s, then goes with the old context and says so.
- Conversations name themselves after three answered turns (Gemini, own key);
  titles are editable and never overwritten.
- **Guests store nothing anywhere.** Signed-in users get stored conversations;
  every database statement filters by `user_id` (the service key bypasses RLS).
  Tested with two real users.

## Sources

- Core Acts cannot be removed. Admins add or remove other sources in the app.
- Adding shows four sample pages beside what PyMuPDF and Docling read; a person
  picks. The statute parser silently fails on non-statutes, so it is a choice,
  not a guess.
- Only new text is embedded; the app picks the change up within a minute.
- The Docker image cannot run the pipeline, so there the controls are hidden
  and the API answers 503.

## Supabase

The laws moved from local files to Supabase with no change in results: 13/13
checks, all 2,320 vectors bit-identical, 10/10 retrieval queries identical (E19).
Search stays exact (no approximate index). A failure opens a 60s breaker and the
local files answer, logged.

## Interface

Responsive from phone to laptop, with resizable side panels. The input grows
with its text. Answer types look different (clarify, out-of-scope, refusal).
Voice gives a transcript to check, never an automatic answer. The name is
আইনসংলাপ throughout.

## Known gaps

- Only retrieval is measured; answer quality, planner accuracy on the current
  model, memory and voice have no numbers.
- Two-issue scenarios and "burden" questions are refused (E17) — `plan.md`
  phase 4.
- Retrieval numbers cover the three core Acts only.
