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

**Planner on Gemini** (E16): 1.3-1.8s against 43.3s on a rate-limited Groq, with
Groq Qwen as a fallback — a different *provider* on purpose, since Groq's limits
are per-account. The move surfaced a cross-provider schema bug: the schema used
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

Both point at `plan.md` phase 4. Next, in order: measure `FINAL_K=8` against 5,
re-run `plan_cases.py` on the current planner model, then replace the fixed k
with a fitted threshold plus facet retrieval.
