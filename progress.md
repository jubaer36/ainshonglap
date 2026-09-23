# Progress

What is built, and the decision behind each part. Measurements and their
configurations are in `experiments.md`; the road ahead is in `plan.md`. Neither
is repeated here.

## Where it stands

| | |
|---|---|
| Corpus | 3 Acts in `Laws/main/` — CrPC 1898 (508 sections), Penal Code 1860 (557), Evidence Act 1872 (182) |
| Parsing | PyMuPDF + a rule-based legal-structure parser. Deterministic: re-running reproduces byte-identical JSON |
| Read models | FAISS (2,320 vectors) for "what resembles this"; `catalog.sqlite` (1,247 sections, 76 chapters, 878 cross-references) for "which provisions *are* these". Both rebuildable from the parsed JSON |
| Routing | Model-authored plan → validated coordinates → one executor per category |
| Generation | Strict JSON schema, C2/C3 citation gates, server-side disclaimer |
| Serving | FastAPI, planner path live; voice, images, conversation memory, optional accounts |

**Measured retrieval** (608 external gold questions, ±3.7pp CI):

| | dense | + reranker |
|---|---|---|
| R@1 | 32.7% | **41.8%** |
| R@5 | — | 59.0% |
| R@20 | 68.4% | 71.4% |
| Act accuracy | 82.2% | 87.5% |

## Pipeline

**Parsing and verification.** Eight parsers evaluated by running them against
the actual PDFs rather than on reputation (`parsing-plan.md`). Hard cases
solved: 300 sections split across page boundaries, the Penal Code's gloss column
swapping sides every page, contents pages that mimic real sections, and 24 pages
of CrPC endnotes that were being appended to s.565 (10,918 words → 280).

Every stage is checked against something external, never the previous stage's
own statistics. `verify_parsed.py` re-reads the PDF and an independently scraped
oracle: zero phantom sections, zero numbering gaps, 100% line-level recovery on
Penal and Evidence (99.99% CrPC). The verifiers earned their keep immediately —
and **three "failures" turned out to be bugs in the checkers themselves**, which
is why the standing rule is to suspect the check first.

**Chunking and retrieval.** Section-level chunks, 1,321 from 1,247 sections.
The two assumptions baked into chunking — hierarchy prefix and chunk budget —
were both **flat** when A/B'd (E2, E3), and the prefix was cut back accordingly.
Cross-encoder reranking was the first significant result (+9.2pp R@1, E4).
Dual-granularity indexing **does not stack** with it (E7); kept anyway, since it
costs nothing at query time and lifts the R@20 ceiling.

**One `retrieve()`, shared.** Both best results once existed only inside eval
scripts: the logged 41.9% described a pipeline nothing could serve. The API and
the eval scripts now call the same code, by design.

**Generation.** Structured claims, each carrying a section number and a verbatim
quote. Two deterministic gates on every answer: **C2** the cited section was
actually retrieved and the Act matches; **C3** the quoted span appears verbatim
in that section. Failing claims are dropped; if none survive, the answer becomes
a refusal. C3 is cheap only because parsing is verified — the section's exact
text is known, so a fabricated quotation cannot survive a string match. That
closes the E1 hallucination failure by construction rather than by prompting.

## Routing: plan, then execute

`app/rag/plan.py` replaced the regex router (E13). One strict-JSON call returns
**coordinates only** — never statutory text, never SQL — and `validate()` checks
each against the catalog before anything executes: unknown Act dropped,
nonexistent chapter dropped with a note, a section number shared by three Acts
turned into a clarification. `app/rag/execute.py` holds one executor per
category. The old pattern router survives behind `RAG_PLAN=0` as an outage
switch.

This reversed an earlier evidence-based decision to run patterns first. The
evidence was against lfm-2.6B, which labelled identical phrasings differently
between two prompt revisions. The model changed, so the conclusion did.

`out_of_scope` was split out of `general` after testing found `general` doing
two jobs: asked about the Dowry Prohibition Act, the planner returned `general`,
whose contract is *answer plainly, no citations* — an ungrounded legal answer
about a statute the corpus does not hold. "Not law" and "law we do not have"
need opposite handling.

The first prompt cost 1,873 tokens and exhausted Groq's per-minute budget after
three questions; a two-tier prompt now costs 778, fetching the 76-chapter
listing only when a chapter is described by subject rather than numbered.

**The planner has its own key** (E18): `gpt-oss-120b` on
`GROQ_API_KEY_PLANNER`, outside the generators' rotation, with Gemini 3.5 Flash
Lite as the fallback — a different *provider* on purpose. The planner runs
before anything else on every question, so a burst of answers must not leave the
classifier rate-limited. Gemini held the job before this (E16): 1.3-1.8s against
43.3s on a rate-limited Groq. The move surfaced a cross-provider schema bug: the schema used
an empty string as an enum member for "not applicable", which Groq and OpenAI
accept and **Gemini rejects**. Every Gemini call 400'd and fell through to the
slow path, so the symptom was latency and the cause was a validation error on a
provider that never ran.

## Interface and sessions

**Answer kinds render differently** because the outcomes are not variations of
each other: `clarify` is a blue question one detail away from an answer,
`out_of_scope` a neutral boundary, and only genuine retrieval failure is amber.
A provenance badge states whether text was quoted verbatim from the catalog,
cited and checked, or ungrounded.

**Voice returns a transcript, never an answer.** The separation is the safety
property: a misheard section number produces a perfectly grounded answer about a
different offence, because every gate passes — none of them know what was said.
The reader seeing the words is the only check on this path.

**Conversation memory is a rolling summary**, not typed scope. Typed scope
handles "show that section" and completely fails "what if he had survived",
because a fact pattern is not a coordinate. A rolling summary is lossy and the
loss compounds, and the detail most likely to be mangled is a section number —
so memory carries two things: the prose summary, and a `sections` list of
citation keys copied by *code* from the answer's own sources. The prompt asks
for verbatim numbers; the code list is the guarantee. A `context · N turns` chip
makes all of it inspectable, because hidden state that steers answers should be.
The summary was later loosened from 90 to ~180 words and the previous
question and answer kept verbatim beside it: at 90 words, what had just been
asked was already paraphrased away by the time the next question arrived.

**Retry is offered only on the last message.** It re-asks the question and
replaces that answer in place, so mid-conversation it would rewrite an answer
later turns were built on — and the memory summary written after it would still
describe the answer it replaced. Withheld rather than disabled, since a greyed
button implies an action you can wait for.

**Conversations can be retitled, and name themselves.** The default title is
the first question asked; that is a good guess and often the wrong label once a
conversation moves on. After three turns that produced an answer — refusals do
not count — `POST /api/conversations/{id}/name` proposes one from the summary.
Its own model, its own key (`GEMINI_API_KEY_3`) and its own request: naming is
not on the path to an answer, and folding it into `/api/query` would put a
second model call between the reader and an answer they are already waiting for.

A title the reader typed is never overwritten. The guard is that the stored
title still equals the one derived from the first question, so no column and no
migration were needed. `PATCH /api/conversations/{id}` renames, and both filter
by `user_id` like every other statement — holding a uuid is not enough to
retitle a stranger's conversation. Deleting asks first, in the row, with focus
on Cancel.

**Guest mode stores nothing, anywhere** — not on the server and not in the
browser. It previously wrote to `localStorage`; "not saved" quietly meaning
"saved locally" would have been a lie in the one place it matters. Signed-in
sessions store everything needed to reload a conversation *as it happened*,
including the memory snapshot **per message**, since an answer at turn 3 was
produced against turn 3's context.

**The service-role key bypasses RLS**, so `app/db/conversations.py` is the
actual isolation boundary, not the policies: every statement filters by
`user_id`, including ones already scoped by `conversation_id`, because a
conversation id is a uuid and not a capability. Verified with two real users —
one cannot list, read or delete the other's conversation while holding its exact
uuid. Forged, expired, wrong-audience and `alg=none` tokens are all rejected.

**Waiting messages follow the pipeline.** The thinking bubble said "Searching
the statutes…" for every question, and for most it was false — nothing searches
until the planner has classified, and a `meta` or `general` answer never touches
the index. It now phases through classify → find → check. The phases are
**timed, not observed**: `/api/query` is one round trip, so the browser cannot
see the backend's stage. Streaming them is in the backlog; `trace.py` already
emits the right events.

## Models, quota and keys

Groq's free tier caps usage **per model and per key**. `app/rag/keyring.py`
therefore uses every `GROQ_API_KEY*` as one budget: **rotation** so each call
starts at the next key and the caps drain evenly, and **failover** so a 429
moves to the next key immediately rather than sleeping. Sleeping happens only
once every key has refused. Keys recorded as spent are tried last, not skipped —
the record is a cached 429, not a fact about the account.

`app/rag/quota.py` records exhaustion per (model, key), so a model greys out
only when **every** one of its keys is blocked. Tracking it per model alone
would have thrown away two thirds of the capacity the extra keys bought. Only
genuine quota errors count; a 500 or a timeout says nothing about budget.

Generators offered, best first: `oss120` (default, probed for strict schema),
`qwen`, `oss`, `gemini`, then `lfm`, `super`, `mercury`, `mercury-2`. `gemini2`
is marked `internal` — it runs the planner and the summariser on their own key,
and offering it as an answer model would spend the classifier's quota. The
picker shows an alias and the real model id and nothing else.

When a route is out, `/api/query` substitutes the fallback (a different
provider) and **says so** — a silent swap would hide which model answered.

## Observability

One concise log line per step: what went to the planner and what came back,
retrieval hits with scores, sections sent to the generator, claims surviving the
gates, the memory exchange, each Supabase write. It exists because a question
crosses five machines and a refusal can mean retrieval fetched the wrong
sections, the generator declined on the right ones, or the gates killed every
claim — three different bugs that look identical from outside. It paid for
itself immediately, identifying a planner outage that had been presenting as the
assistant failing to understand "who are you". Never prints statutory text,
prompts or full answers.

## Serving correctness fixes

- **Every handler was `async def` while doing blocking work**, so FastAPI ran
  them on the event loop and one in-flight question froze the whole server.
  Blocking handlers are now plain `def`.
- **Warmup blocked startup** for ~30s, so a backend still waking up looked like
  a broken one. It now runs in a worker thread and `/health` reports `warming`.
- CORS allowed only `localhost:3000`; `127.0.0.1:3000` is a different origin.
- The model picker returned `null` on an empty list, so a failing `/api/models`
  looked identical to a feature that did not exist.

## The Act list is data

A fourth Act loaded and searched correctly — and still could not be answered
from. Its sections came back keyed `act-s13` (a fixed three-entry list mapped
Act names to keys), and the planner was told "Scope: exactly three Acts" while
being shown four. Ten places in the backend and three in the frontend named the
three Acts outright.

`app/rag/acts.py` now answers all of it from the catalog: keys, short names, the
planner's scope and the `acts` values its schema allows, the "outside …"
messages, and which Act a sentence names — strictly for a question (so "the
evidence shows" names no Act) and nearest-first in a pasted document (so "s.54
of the CrPC and s.25 of the Evidence Act" gives each number its own Act). The
frontend reads the same list. Short names are derived from titles, so "CrPC"
is now shown as "Code of Criminal Procedure".

Checked end to end on the Arms Act: "what does section 13 of the Arms Act say"
plans to `acts=['arms']` and quotes s.13; "is it an offence to go armed without
a license" is answered from `arms-s13` with its citation verified.
`scripts/remove_act.py` takes an Act out of Supabase and the local fallback.

## Adding a document: a person chooses the parser

The statute parser finds sections by their numbers. On a judgment, on Rules
("Rule 3."), or on a Bangla Act ("৩।") it found none and put the whole text in
`preamble` — without an error, and where nothing downstream reads it. The
verifier would have passed it, since all the text was "recovered".

`scripts/ingest_document.py` makes it a decision instead of a guess: it parses
with PyMuPDF, samples four pages spread through the document, runs Docling on
just those, and writes a review page — each page's image beside both parsers'
output. The person picks; choosing Docling parses the whole document by layout
(`parse_docling_document.py`) into heading-led passages, in the same JSON shape,
so chunking, the catalog and the load take it unchanged. PyMuPDF is refused
outright when it found no sections at all.

Checked on the Arms Act (PyMuPDF: 32 sections, all text inside them, numbering
in order; the review showed Docling turning its contents page into passages)
and on test documents in the three formats that had failed. The three served
Acts still chunk byte-identically.

## Corpus served from Supabase

The statute text is moving from local files into Postgres with pgvector
(`plan.md` phase 8). **The app now reads it from Supabase**; the local files
answer only when Supabase cannot, and every such use is logged as `FALLBACK`.

Exact lookups load Supabase's structural tables into an in-memory copy of the
catalog at startup — the planner consults the catalog many times per question,
and a network call each time would add up to half a second — and rebuild it
within a minute of an Act being loaded or reloaded. Meaning search asks Supabase
on every question. A failure opens a 60-second breaker so an outage costs one
timeout, not one per lookup. Simulated: 51 lookups during an outage made one
network attempt, answers were identical, and recovery was logged.

Checked across both stores with no model calls: 17/17 catalog functions and
10/10 retrieval queries returned identical results, scores included. The side
panels now read the catalog too, which fixed the five sections where they had
shown page-break fragments glued onto the real text — `penal-s2` showed 1,155
characters for a 188-character section.

All three Acts loaded in ~19s and `verify_supabase.py` passed 13/13: every
section's text byte-identical, all 2,320 vectors bit-identical, and on 60 search
probes the top-60 results matched FAISS exactly in 56 and differed only in the
order of equal-scoring chunks in the other 4 (largest score gap 4.8e-7, float
rounding). Re-running the loader skips all three — the per-Act digest matches.

Two things were settled before writing any of it. **Exact search only** — FAISS
here is an exact scan and the retrieval numbers were measured on it, so an
approximate index would change results; at 2,320 rows it buys nothing anyway.
**Vectors are loaded, not recomputed** — sent at float32's shortest round-trip
precision, which was checked to come back bit-identical for all 2,320.

Found on the way: 36 subsection chunks share an id with a sibling
(`crpc_s10_child4` appears twice). Harmless to FAISS, which works by position,
so the database keys chunks by position too.

## What this establishes, and what it does not

The full path runs end to end and is served over HTTP: question → plan →
retrieval or exact fetch → generation → per-claim citation verification → cited
answer or refusal. Routing is decided by a model and executed by validated code,
failing closed on any coordinate the catalog does not have. Retrieval is
measured at every stage against 608 external questions — including three
negative results that overturned assumptions taken from published work, and one
that was never validly measured at all.

**What is built is wider than what is measured.** Generation quality has no
numbers: no faithfulness, citation-precision or abstention measurement exists,
and E8 — meant to be the first — is unfinished. The executors, memory, voice,
key rotation and `FINAL_K=8` have no pinned regression numbers either.

Two known failure modes, both traced:

- **Multi-issue scenarios refuse.** A scenario raising two legal issues
  collapses into one embedding query and the dominant wording takes every
  retrieval slot, so the generator refuses — correctly — on what it was handed.
- **Questions answered by a burden *allocation* refuse even when retrieval
  succeeds** (E17). "If I can't prove myself innocent, am I guilty?" retrieves
  Evidence ss.101-106 correctly and is declined, because the generator prompt
  forbids reasoning from a provision to its consequence. The prompt is tuned for
  "what is the punishment for X", where the answer sits verbatim in the text.

Both point at `plan.md` phase 4. A third was found and fixed: a request naming
two Acts was unrepresentable, because the planner's `act` field held one slug —
so "the first section of both" returned one Act's and dropped the other without
a word (E18). `acts` is now a list.

Next, in order: measure `FINAL_K=8` against 5, re-run `plan_cases.py` on the
current planner model, then replace the fixed k with a fitted threshold plus
facet retrieval.
