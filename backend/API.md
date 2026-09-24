# API

Base `http://localhost:8000` · OpenAPI at `/docs`.

```bash
conda activate ainshonglap
cd backend
uvicorn app.main:app --reload
RAG_WARMUP=0 uvicorn app.main:app --reload   # skip model load (corpus endpoints only)
```

## Endpoints

| method | path | purpose |
|---|---|---|
| POST | `/api/query` | question → cited answer, or a refusal |
| GET | `/api/plan` | what the planner decided, without answering |
| POST | `/api/transcribe` | voice input → transcript (deliberately not an answer) |
| GET | `/api/retrieve` | raw retrieval, no generation — inspection and debugging |
| GET | `/api/acts` | indexed acts + section counts |
| GET | `/api/sections` | browse/filter sections (`act`, `q`, `limit`, `offset`) |
| GET | `/api/sections/{key}` | one section by citation key, e.g. `penal-s379` |
| POST | `/api/read-image` | one page image → text (never an answer) |
| GET | `/api/conversations` | saved conversations (signed in) |
| GET | `/api/conversations/{id}` | one conversation, with per-turn context and images |
| PATCH | `/api/conversations/{id}` | rename one |
| GET | `/api/sources/admin` | may the caller manage sources? |
| POST | `/api/sources` | upload a PDF; starts its review (admin) |
| GET | `/api/sources/jobs` | documents still being added, or failed — the sources list shows each as a pending row that reopens its dialog (admin) |
| GET | `/api/sources/jobs/{id}` | job progress, and the review when ready (admin) |
| POST | `/api/sources/jobs/{id}/choose` | `{"parser": "pymupdf"\|"docling"}` — index it (admin) |
| POST | `/api/sources/jobs/{id}/discard` | abandon an add before indexing (admin) |
| DELETE | `/api/sources/{slug}` | remove a source; core Acts refused (admin) |
| POST | `/api/conversations/{id}/name` | name one from its summary |
| DELETE | `/api/conversations/{id}` | delete one |
| POST | `/api/conversations/import` | adopt conversations made before signing in |
| GET | `/health` | liveness, index stats, configured models |

### POST `/api/query`

```json
{"question": "What is the punishment for theft?"}
```

Returns an `AssistantMessage`: `heading`, `blocks` (paragraphs with inline
citation segments), `citations` keyed by citation key, `unsupported`, `kind`,
`resolved_question`, `generator`, and `memory_pending: true` — the updated
conversation memory is not in this response (see below).

#### Conversation memory

Send the `memory` from the previous answer back with the next question:

```json
{"question": "what if he had survived instead?",
 "memory": {"summary": "The user described three men beating a man...",
            "sections": ["penal-s302", "penal-s304"], "turns": 1}}
```

A rolling summary, rewritten after every turn by gpt-oss-120b on its own key,
`GROQ_API_KEY_SUMMARIZER`, with the system fallback behind it. (Gemini 3.5 Flash
Lite held the job until it took 34-64s per call and every update hit the 20s
deadline with `504 DEADLINE_EXCEEDED` — E20.) The planner decides `follow_up` itself, **defaulting to
false**, and rewrites a follow-up into a standalone question; `resolved_question`
comes back so the reader can see what was actually answered. A bad rewrite would
otherwise be a confident, properly cited answer to a question nobody asked.

Two parts, because a follow-up leans hardest on the turn right before it — the
one a rolling summary compresses first. The **summary** is short background,
under ~100 words: one sentence per turn, what was asked and what was concluded,
with its sections. The **previous exchange** — `last_question` and
`last_answer` — is copied word for word by code, never by the summariser, so it
survives a paraphrase and a summariser outage alike. Both reach the planner, and
both are shown in the frontend's memory panel.

The server stores nothing for a guest — the client holds the memory and echoes
it; the only server-side copy is the few minutes an answer waits in
`pending_memory` for its context update. For
questions this personal that is a privacy property, and it removes the session
store entirely. `sections` is written by code from the answer's own citations,
never by the summariser, because a rolling summary is lossy and the detail most
likely to be mangled is a section number.

`generator` names the model that answered, and `generator_substituted` the alias
it replaced when the requested one was out of quota.

#### `POST /api/memory` — the context update, after the answer

```json
{"message_id": "<the answer's id>", "conversation_id": "<optional>"}
```

Returns the updated `ConversationMemory` for the client to send with its next
question. **The answer never waits for it.** The summariser used to run inside
`/api/query`, between the answer being ready and being sent, and a slow Gemini
(60-90s observed) held a two-second answer behind a minute of summarising. Now
the query handler parks the question, the structured answer and the memory it
was asked against in `app/rag/pending_memory.py`, keyed by message id, and this
request collects them.

Parked entries live in process memory for 10 minutes and are taken once — a
second call, or one after a restart, gets `410`, and the client keeps the
context it had. Nothing is written for a guest; for a signed-in user with a
`conversation_id`, the new memory replaces the stored one on the conversation
and on that answer's row.

The frontend asks for it as soon as an answer renders. If the next question is
sent while it is still running, the client waits up to 25 seconds (showing that
the context is updating), then sends the question with the previous context and
marks that question as sent without the last exchange.

**A refusal is a 200 with `unsupported: true`**, not an error — "the Acts do not
cover this" is an answer. A **502** means the generator was unreachable, which
is a different failure and retryable.

#### `kind` — how the answer was produced

A question is planned into a category and executed by the matching machine
(`app/rag/plan.py` → `app/rag/execute.py`), and the results are trustworthy in
different ways. `unsupported` alone cannot express that: it says only "not a
grounded answer", which makes "which Act did you mean?" indistinguishable from
"I searched and found nothing".

| `kind` | `unsupported` | citations | what it is |
|---|---|---|---|
| `lookup` | false | 1 | one named section, quoted verbatim from the catalog. No model touched the text. |
| `outline` | false | 0–80 | a chapter or range listing, or a whole-Act summary, from the parsed hierarchy |
| `legal` | false | 1+ | retrieved and generated, every claim through C2/C3 below |
| `legal` (explain) | false | 1+ | "explain section 302", "what does s.300 mean": the named sections fetched exactly, then broken down element by element, every point quoting the section (C2/C3). If no point survives, the section is quoted instead (`lookup`). |
| `meta` | false | 0 | about the assistant, assembled from the catalog manifest |
| `general` | false | 0 | **not** a legal question; the model's own knowledge, uncited and unchecked |
| `out_of_scope` | true | 0 | a legal question about law outside the Acts held. Nothing was searched. |
| `clarify` | true | 0 | the request needs one more detail — an ambiguous section number, a subjective selection |
| `refused` | true | 0–5 | retrieval ran and nothing survived the citation gates; the sections it considered are listed as citations, so each can be opened |

`out_of_scope` is kept apart from `general` deliberately. Answering "what does
the Dowry Prohibition Act say" from the model's own knowledge would be an
ungrounded legal answer about a statute nobody has verified — the E1 failure.
Answering "what is the capital of France" that way is fine. Same mechanism,
opposite consequences.

Clients should render `clarify` as a question rather than a failure: it is one
detail away from an answer, and an error-coloured panel tells the reader to give
up when they should type three more words.

Every claim in a returned answer has passed two checks against the retrieved
text (`app/rag/verify.py`):

- **C2** the cited section was actually retrieved, and where several acts share
  a section number the act must match too
- **C3** the quoted span appears verbatim in that section

Claims failing either are dropped. If none survive, the answer becomes a
refusal. Answers carry no disclaimer from the API: the frontend adds it once
per conversation, under the first answer to a legal question (not small talk,
`meta`, `out_of_scope` or `clarify`).

### GET `/api/plan?q=...`

What the planner decided, without answering. One small model call — no
retrieval, no generation.

```json
{"category": "structural", "operation": "enumerate",
 "act": "evidence", "acts": ["evidence"], "chapter": "V", "sections": [],
 "needs_clarification": false, "by": "llm",
 "notes": ["chapter-lookup: 'DOCUMENTARY EVIDENCE' -> V in evidence"]}
```

`acts` is every Act the question named; `act` is the first of them, kept because
most coordinates concern one and the semantic path reads it as a hint. A request
spanning two Acts — "the first five sections of the Evidence Act **and** the
Code of Criminal Procedure" — is one plan with `acts: ["evidence", "crpc"]`, and
the structural executor runs one catalog query per Act and merges them in the
order named. This used to be unrepresentable: `act` held one slug, so the second
Act was either dropped from the answer without a word or turned into "which Act
did you mean?".

The planner chooses the machine that answers every question, so when an answer
looks wrong the first thing worth knowing is whether it was *planned* wrong.
`notes` records everything validation changed or rejected — a chapter
renumbered to the Act's own spelling (`16` → `XVI`), a coordinate dropped for
not existing. `by` is `"fallback"` when the planner call failed and the question
degraded to plain semantic retrieval.

The planner returns **coordinates only** — never statutory text, never SQL. Code
validates each against the catalog before executing it, so a hallucinated
chapter returns nothing rather than the nearest real one.

Set `RAG_PLAN=0` to fall back to the older pattern router in `app/rag/refine.py`.

**The planner runs on `gpt-oss-120b`** (`planner`, on `GROQ_API_KEY_PLANNER`),
with the system fallback (`fallback`, gpt-oss-120b on `GROQ_API_KEY_FALLBACK`)
behind it. Both are marked `internal` in the registry and never appear in the
model picker.

The key is a **dedicated prefix, not a fourth numbered one**. `keyring` matches
the bare name plus numeric suffixes, so `GROQ_API_KEY_PLANNER` is never swept
into the generators' rotation. The planner runs before anything else on every
question, and a burst of answers must not leave the classifier rate-limited: a
failed plan degrades the question to plain semantic retrieval, and "who are you"
then goes to the statute index and comes back with sections scoring -3.90.

The fallback is the same model on a key nothing else draws on, so a burst that
rate-limits the planner's key has not touched it. It does not cover a Groq-wide
outage; Gemini held this job before (E16, 1.3-1.8s) and `RAG_PLANNER_FALLBACK=gemini2`
puts it back.

This matters more than it looks. The planner is the first step, so its failures
do not present as planner failures — a failed plan degrades the question to
plain semantic retrieval, and "who are you" then goes to the statute index and
comes back with sections scoring -3.90 and a refusal.

### POST `/api/transcribe`

Multipart upload of a recording; returns what was heard. **It does not answer
the question.**

```bash
curl -X POST http://localhost:8000/api/transcribe \
  -F "audio=@question.webm;type=audio/webm"
```

```json
{"transcript": "what does section 379 of the penal code say",
 "confidence": 0.98, "duration": 4.2, "language": "en"}
```

The separation is a safety property, not an API convenience. A misheard section
number produces a **perfectly grounded answer about the wrong offence**: the
planner names coordinates that exist, the catalog fetches them, the verbatim
quote checks out against the section that was fetched. Every gate passes,
because no gate knows what was actually said. The reader seeing the transcript
before sending it is the only check on this path, so the API refuses to skip it.

`503` means no `DEEPGRAM_API_KEY` — voice is unconfigured, the rest of the
assistant is unaffected, and typing still works. `413` is audio over 10 MB.

Keyterm prompting boosts statutory vocabulary ("cognizable", "bailable", the
Act names) built from the catalog, because a general speech model turns those
into commoner words rather than failing visibly.

**Bengali is `bn`, nova-3 only, and is not in Deepgram's `multi`
code-switching set** (en, es, fr, de, hi, ru, pt, ja, it, nl). Mixed
Bangla-English has no single-model answer; `RAG_STT_LANGUAGE` picks one, and
`scripts/deepgram_stt.py --compare` measures what the choice costs.

## Accounts and storage

Signing in is **optional**, and the two modes differ in exactly one way:

| | guest | signed in |
|---|---|---|
| answers | identical | identical |
| stored on the server | **nothing** | conversations, per-turn context, page images |
| stored in the browser | **nothing** | a session token |

A guest sends no `Authorization` header, so `_persist` has no verified user and
returns before touching the database. That absence *is* the enforcement — there
is no client-side flag to get wrong.

Supabase Auth issues the token; the API verifies it locally by reading the
token's own `alg` header (`RS256`/`ES256` against the project's JWKS endpoint,
`HS256` against `SUPABASE_JWT_SECRET`). Verification covers signature, expiry
and audience: decoding without it would let anyone claim any `sub` and read
another user's conversations.

**The service-role key bypasses Row Level Security.** The policies in
`db/schema.sql` are therefore not what isolates users —
`app/db/conversations.py` filters every statement by `user_id`, including the
ones already scoped by `conversation_id`, because a conversation id is a uuid
and not a capability. The policies remain for anything that ever connects with
an anon key.

Another user's conversation returns **404, not 403**: a 403 would confirm the id
exists. This covers `PATCH` as well as the reads — a rename filters by `user_id`
like everything else, so holding a uuid is not enough to retitle a stranger's
conversation.

### POST `/api/conversations/{id}/name`

```json
{"memory": {"summary": "...", "sections": [], "turns": 4, "answered": 3}}
```

The client calls it after the context update has landed, since `answered`
comes from that. Returns the updated `ConversationSummary`, or `null` when the title is
unchanged — the ordinary outcome. Safe to call more than once.

**Its own request, not part of `POST /api/query`.** Naming is not on the path to
an answer, so folding it in would have put a second model call between the
reader and an answer they were already waiting for, for a label they were not
looking at yet. It has its own model and key too (`app/rag/naming.py`,
`GEMINI_API_KEY_3`), because it runs once per conversation while the summariser
runs every turn, and sharing a key would let a label eat the quota a follow-up
depends on.

The first question stays the title until a conversation has had
`RAG_NAME_AFTER` turns that actually produced an answer. Refusals do not count:
a conversation of three refusals has no subject to name. That count is
`memory.answered`, which the client echoes back like the rest of the memory.

**A title the reader typed is never overwritten.** The guard is that the stored
title still equals the one derived from the first question; if it does not,
either they renamed it or naming already ran, and both are theirs to keep. No
extra column, so no migration.

Without `GEMINI_API_KEY_3` the call is a no-op and conversations keep their first
question. `/health` reports whether it is configured, since a missing key
otherwise looks like a feature that was never built.

### PATCH `/api/conversations/{id}`

```json
{"title": "Bail for house theft"}
```

Returns the updated `ConversationSummary`. The default title is the first
question asked, which is a reasonable guess and often the wrong label for a
conversation that moved on.

The title is normalised server-side — whitespace collapsed, capped at 120
characters with an ellipsis, and a blank one falling back to `New
conversation` — so the client re-reads the list rather than trusting the string
it sent.

### What a stored session contains

Everything needed to reload it as it happened:

```text
conversations   title, latest memory, timestamps
messages        the question and the whole answer as sent, plus — per turn —
                the memory snapshot it was answered against, which model
                answered, and what the planner decided
uploads         the page image, its transcription, and the turn it fed
```

The memory snapshot is per message rather than per conversation on purpose. An
answer at turn 3 was produced against the summary as it stood at turn 3;
replaying it beside turn 7's summary explains the wrong thing.

Page images are private, served through one-hour signed URLs. The transcription
that reaches the pipeline is a vision model's *reading* of the page — keeping
the page beside it is the only way to check that reading later.

### GET `/api/models`

Selectable generators, with availability:

```json
{"alias": "oss120", "backend": "groq", "model": "openai/gpt-oss-120b",
 "enforces_schema": true, "default": true, "fallback": false,
 "available": false, "unavailable_reason": "daily token limit reached",
 "seconds_remaining": 760}
```

Ordered best-first (`config.PICKER_ORDER`): `oss120`, `qwen`, `oss`, `gemini`,
then the rest in registry order. The registry itself is grouped by provider,
which is the right shape for reading it and the wrong one for choosing from it.

**Internal routes are not listed.** `planner` (`GROQ_API_KEY_PLANNER`),
`summariser` (`GROQ_API_KEY_SUMMARIZER`), `fallback` (`GROQ_API_KEY_FALLBACK`)
and `gemini2` (unused by default) are machinery, not choices; offering one as an
answer model would let a reader spend the quota a per-question job depends on —
the next question then goes unclassified, or its context stops updating. They
stay resolvable by alias — they are simply not advertised. `config.selectable()` is the filter, and the 422 "unknown model"
message lists the same set.

Two different things make a route unusable, and they are reported separately.
`enforces_schema: false` is permanent and about correctness — the pipeline needs
a verbatim quote per claim, and a route answering in prose parses as "no claims"
and surfaces as a false refusal. `available: false` is temporary and about
budget.

Quota is tracked **per (model, key)**. Groq's daily cap is named for one model
on one key: `openai/gpt-oss-120b` on `GROQ_API_KEY` says nothing about the same
model on `GROQ_API_KEY_2`, and nothing about `qwen/qwen3.8-27b` on either. A
model reports `available: false` only once **every** key it has is blocked, and
comes back when the **first** of them does — so `seconds_remaining` is the
soonest expiry, not the longest. Tracking it per model alone would have greyed
out a route still holding two thirds of its capacity.

The cooldown is parsed from the provider's own "try again in 12m40s". Only
genuine quota errors count — a 500 or a timeout says nothing about budget.

When the chosen generator is out, `POST /api/query` answers on
`RAG_FALLBACK_GENERATOR` (default `mercury`, deliberately a *different
provider*) and reports the substitution rather than making it silently.

### GET `/api/retrieve?q=...&k=5&rerank=true&profile=`

The retrieval half alone, with dense and reranker scores per hit. `rerank=false`
gives the dense-only ordering — the same code path the eval scripts measure, so
served and measured numbers cannot drift. `profile` picks a retrieval profile
(below); an unknown name is a 422.

## Retrieval profiles

Which embedder finds the 20 candidates and which reranker orders them
(`config.RETRIEVAL_PROFILES`, E21):

| profile | search | reranker | R@1 | R@5 | R@20 | Act | API cost |
|---|---|---|---|---|---|---|---|
| `bge+nemotron-rerank` | BGE-M3, local | Nemotron rerank (OpenRouter) | **50.8** | 67.1 | 71.4 | **91.6** | 1 request / question |
| `bge+bge-rerank` *(default)* | BGE-M3, local | bge-reranker-v2-m3, local | 41.8 | 59.0 | 71.4 | 87.5 | none |
| `nemotron-embed` | nemotron-3-embed (OpenRouter) | none | 48.4 | **75.0** | **86.5** | 88.5 | 1 request / question |

**Choosing.** `RAG_RETRIEVAL` sets the server default (restart to apply). A
single question can override it with `"retrieval": "<profile>"` on
`POST /api/query`; everything that question retrieves — every executor, every
facet — uses it. `/health` lists the profiles and the default.

**Two vector sets, one per embedder.** BGE-M3's live in `law_chunks`
(`vector(1024)`); nemotron-3-embed's in `law_alt_chunks` (migration 003), whose
`embedding` column has no fixed dimension and is always searched one model at a
time through `law_match_alt_chunks(query_embedding, model_name, match_count)`.
A question is embedded by one model and compared only with that model's
vectors. Locally the same split: `embeddings.npy` and
`embeddings.nemotron-3-embed.npy`, the latter tied to `chunks.json` by a digest
of its texts so vectors built for an older corpus are refused rather than
searched.

**Cost and fallback.** The two remote profiles call OpenRouter once per
question (a query embedding, or one rerank of 20 sections), on every
`OPENROUTER_API_KEY*` in rotation with failover on a refusal. `:free` models
share a daily cap per account — 1,000 requests with credits bought, 50 without.
When OpenRouter cannot serve — the cap, an outage, a timeout
(`RAG_REMOTE_TIMEOUT`), or no nemotron vectors for the current corpus — the
question is answered with `bge+bge-rerank` and a WARNING is logged:

```
FALLBACK  retrieval: nemotron-3-embed search failed (<reason>); using bge+bge-rerank
FALLBACK  retrieval: nemotron rerank failed (<reason>); using bge+bge-rerank
```

**Keeping the nemotron vectors current.** `build_index.py --embedder
nemotron-3-embed` embeds only chunks that have no vector yet (about one request
per 32 chunks), then `load_supabase.py --embedder nemotron-3-embed --all`
loads them. Adding or removing a source through the app does both itself; if
the embedding step fails, the job still completes and `nemotron-embed` falls
back until it is re-run. `ingest_document.py` prints both steps; `remove_act.py`
runs them. Before each search, the backend checks that every loaded Act has
nemotron vectors in Supabase (cached for a minute); if one is missing,
`nemotron-embed` falls back rather than silently leaving that Act out.

## Managing sources

The core Acts (`RAG_CORE_ACTS`, default `crpc,penal,evidence`) are permanent.
Any other source can be added or removed while the app runs, by a signed-in
user whose email is in `ADMIN_EMAILS`. That check is made on the verified
token; the frontend only hides the controls from everyone else. `/api/acts`
carries `core: true` for the permanent ones.

Adding is two steps with a person in between. `POST /api/sources` saves the PDF
to `Laws/main/`, parses it with PyMuPDF and previews Docling on four sampled
pages; the job then waits in `review`, carrying each page as an image beside
both parsers' output. `choose` indexes it with the chosen parser: parse, chunk,
embed (new text only — every known vector is reused), catalog, load into
Supabase, verify. Removing deletes from Supabase first, moves the local files to
`Laws/removed/<slug>/`, and rebuilds. Jobs run one at a time on one worker, so
two index rebuilds can never overlap; they are held in memory and forgotten on
restart, which loses nothing — an Act is either loaded (it has a `law_loads`
commit marker) or it is not. When nemotron vectors exist (E21), the same job
embeds the new chunks for them and loads them to `law_alt_chunks`.

**Only where the pipeline runs.** Adding runs the offline pipeline from
`backend/scripts/` with PyMuPDF and Docling. The deployment image has none of
that, so there `GET /api/sources/admin` returns `manage: false` with a
`reason`, the frontend shows no add/remove controls, and every other
`/api/sources` endpoint answers 503. `RAG_SOURCES=0` switches it off anywhere.
Add sources on a machine that has the pipeline; the load to Supabase reaches
every deployment within a minute.

## When a model fails

Every model call has one fallback: `gpt-oss-120b` on `GROQ_API_KEY_FALLBACK`, a
key nothing else uses, so whatever took the first model down has not also spent
the fallback's budget. It covers answers (any provider failure — quota, outage,
timeout — not only quota; an unknown model id is not retried), the planner and
its chapter lookup, the conversation summariser, and conversation naming (also
when Gemini returns an empty title). Each use is logged at WARNING as
`FALLBACK  <job>: <model> failed (<reason>); using fallback`. Gemini calls are
abandoned after `RAG_GEMINI_TIMEOUT` seconds so the fallback gets its turn.

The fallback key is budgeted separately from the shared pool: `quota.py` records
it under `openai/gpt-oss-120b [GROQ_API_KEY_FALLBACK]`, so the pool's three keys
running out of gpt-oss-120b does not grey out the fallback, and vice versa.

Vision is the exception: gpt-oss-120b cannot read an image.

A weaker planner makes more mistakes, so one rule is enforced in code rather
than trusted to the prompt: a question that names an Act the catalog holds is
never `out_of_scope` — it becomes an exact fetch when a section is named, and a
scoped search otherwise.

## Where the statute text comes from

Supabase, always first. The catalog (exact lookups) is loaded from the `law_*`
tables into memory at startup and rebuilt within a minute of an Act being
loaded; meaning search runs `law_match_chunks` in Postgres on every question
(or `law_match_alt_chunks` for the `nemotron-embed` profile — see Retrieval
profiles).

The local `catalog.sqlite` and FAISS index are **fallbacks only**. They answer
when Supabase cannot, and each use is logged at WARNING — visible even with
`RAG_TRACE=0`:

```
FALLBACK  catalog served from LOCAL files — Supabase unavailable: <reason>
RECOVERED catalog back on Supabase
```

A failure opens a 60-second breaker, so an outage costs one timeout rather than
one per catalog lookup. `/health` reports it under `store`:

```json
"store": {"primary": "supabase", "catalog_loaded": true,
          "acts": ["crpc", "evidence", "penal"], "unavailable": null,
          "fallback": {}}
```

A non-empty `fallback` means answers are coming from the local copy, which does
not have any Act loaded to Supabase since it was last rebuilt.

## Configuration

`backend/.env.local` (gitignored) holds the keys:

| key | used by |
|---|---|
| `GROQ_API_KEY`, `_2`, `_3` | the generators, in rotation (below) |
| `GROQ_API_KEY_PLANNER` | the planner alone, deliberately outside that rotation |
| `GROQ_API_KEY_FALLBACK` | the system-wide fallback — gpt-oss-120b for every model call that fails |
| `GROQ_API_KEY_SUMMARIZER` | the conversation summariser (gpt-oss-120b), and nothing else |
| `GEMINI_API_KEY_3` | naming a conversation, and nothing else |
| `GEMINI_API_KEY` | optional generator |
| `INCEPTION_API_KEY` | Mercury, a selectable generator |
| `DEEPGRAM_API_KEY` | voice |
| `OPENROUTER_API_KEY`, `_2` | vision, OpenRouter generators, the OpenRouter retrieval profiles (rotated) |

Each job that runs on *every* question gets a key nothing else draws on. A
sidebar label must not be able to exhaust the quota the next follow-up depends
on.

### Groq key rotation

Groq's free tier is capped per key *and* per model, so more keys mean more
budget — but only if requests actually spread across them. `app/rag/keyring.py`
discovers them by convention: `GROQ_API_KEY`, then `GROQ_API_KEY_2`,
`GROQ_API_KEY_3`, stopping at the first gap. Adding a fourth is an `.env.local`
edit and nothing else.

Two mechanisms, and they are not the same thing:

| | |
|---|---|
| **rotation** | every call starts at the next key, so a stream of questions divides roughly evenly instead of burning key 1 to its daily cap while 2 and 3 sit untouched |
| **failover** | a 429 on one key is retried on the next *immediately*, because a per-minute ceiling on one key is not a ceiling on another. Sleeping on `retry-after` is what happens only after every key has refused |

Keys already recorded as out of budget are tried **last, not skipped** — that
record is a cached 429, not a fact about the account.

A provider with one key (Inception) yields a single slot and behaves exactly as
it did before. The OpenRouter retrieval calls (`remote_retrieval.py`) rotate
over every `OPENROUTER_API_KEY*` the same way. `RAG_TRACE` logs each switch (`key GROQ_API_KEY_2 429
-> next key`), and `/health` names which keys are down rather than only that the
model is.

| var | default | |
|---|---|---|
| `RAG_GENERATOR` | `oss120` | alias returned by `GET /api/models` |
| `RAG_GROQ_MODEL` | `qwen/qwen3.8-27b` | direct Groq model id |
| `RAG_LLM_BACKEND` | `groq` | `groq`, `openrouter`, `gemini`, `inception` |
| `RAG_INCEPTION_MODEL` | `mercury-2.5` | Inception Mercury default |
| `RAG_PLANNER_MODEL` | `planner` | the planner — decides category and coordinates |
| `RAG_PLANNER_FALLBACK` | `fallback` | tried when the planner's own model fails |
| `RAG_NAME_MODEL` | `gemini-3.5-flash-lite` | names a conversation from its summary |
| `RAG_NAME_AFTER` | `3` | answered turns before a conversation is named |
| `RAG_TRACE` | `1` | one concise log line per pipeline step; `0` silences |
| `RAG_PLAN` | `1` | `0` falls back to the old pattern router |
| `RAG_ROUTER_MODEL` | `lfm` | old router's model, used only when `RAG_PLAN=0` |
| `RAG_DENSE_K` / `RAG_FINAL_K` | `20` / `8` | candidates reranked / sections returned |
| `RAG_RETRIEVAL` | `bge+bge-rerank` | retrieval profile: `bge+nemotron-rerank`, `bge+bge-rerank`, `nemotron-embed` |
| `RAG_REMOTE_RERANK_MODEL` | `nvidia/llama-nemotron-rerank-vl-1b-v2:free` | reranker for `bge+nemotron-rerank` |
| `RAG_REMOTE_TIMEOUT` | `15` | seconds before an OpenRouter retrieval call falls back to local |
| `RAG_FALLBACK_GENERATOR` | `fallback` | gpt-oss-120b on its own key; used when any model call fails |
| `RAG_GEMINI_TIMEOUT` | `20` | seconds before a Gemini call is abandoned for the fallback |
| `RAG_MEMORY_MODEL` | *(auto)* | summariser alias; `summariser` when its key is set, else `oss120` |
| `RAG_MEMORY_CHARS` | `800` | ceiling on the summary; the previous exchange is kept verbatim on top (up to 2,000 + 4,000 chars) |
| `RAG_STT_MODEL` | `nova-3` | Deepgram model; Bengali needs nova-3 |
| `RAG_STT_LANGUAGE` | `en` | `en`, `bn`, or `multi` (no Bengali in `multi`) |
| `RAG_WARMUP` | `1` | `0` skips model loading at startup |
| `CORS_ORIGINS` | `http://localhost:3000` | comma-separated |
| `SUPABASE_URL` | — | project URL; storage and auth are off without it |
| `SUPABASE_SERVICE_ROLE_KEY` | — | **server only**; bypasses RLS |
| `SUPABASE_JWT_SECRET` | — | only for older projects with a shared HS256 secret |
| `SUPABASE_BUCKET` | `page-images` | private bucket for page images |

Generator routes must enforce JSON schema. The default Groq Qwen route passed
the strict-schema probe in `scripts/groq_chat.py --json`. OpenRouter fallbacks
still set `provider.require_parameters`, so providers that ignore schemas fail
loudly instead of returning prose.

## Layout

```
app/rag/
  config.py        models, paths, k values, CORE_ACTS
  acts.py          which Acts are held — keys, short names, scope — from the catalog
  catalog_schema.py  the catalog's table layout, shared by the build and corpus_db
  corpus_db.py     the corpus from Supabase: catalog cached in memory, vector search by RPC
  fallback.py      logs every time the local files answer instead of Supabase
  encoder.py       BGE-M3 query/document embedding
  store.py         local FAISS index + chunk records — the fallback; also the
                   nemotron vectors (`load_alt`), checked against chunks.json
  remote_retrieval.py  OpenRouter embedding + reranking for the E21 profiles
  reranker.py      cross-encoder (E4)
  pipeline.py      retrieve() — search, collapse to sections, rerank
  llm.py           Groq / Inception / OpenRouter / Gemini, JSON-schema output
  catalog.py       exact fetch by coordinates, parameterized SQL, empty on a bad
                   coordinate — same queries whether the catalog came from Supabase
                   or the local file
  plan.py          the planner: question -> category + coordinates, validated
                   against the catalog; scope and allowed Acts read from it
  execute.py       one executor per category; plan -> Answer
  answer.py        prompt, generation, gating, refusal
  verify.py        C2 + C3 citation gates
  citations.py     retrieved section -> Citation
  library.py       browsable corpus for the side panels, read from the catalog
  refine.py        the pattern router, used only when RAG_PLAN=0
  speech.py        Deepgram STT — audio -> transcript, never an answer
  vision.py        OpenRouter vision — one page image -> text
  memory.py        rolling conversation summary + previous exchange verbatim;
                   section keys from code
  pending_memory.py answers waiting for their context update (POST /api/memory),
                   in process memory for 10 minutes, taken once
  naming.py        names a conversation from its summary, on its own key
  keyring.py       several API keys per provider: rotation, and failover on 429
  quota.py         per (model, key) exhaustion and cooldown, for fallback and the picker
  trace.py         one log line per step
  present.py       Answer -> AssistantMessage

app/ingest/
  sources.py       add/remove sources as background jobs, one at a time; core Acts refused
```

Two read models, two jobs: meaning search answers "which provisions resemble
this question", the catalog answers "which provisions *are* these". Both are
served from Supabase (`law_chunks` with pgvector; the `law_*` structural
tables), with `chunks.faiss` and `catalog.sqlite` kept locally as a logged
fallback. All of it is rebuilt from the parsed JSON, which stays the only
source of law.

`pipeline.retrieve()` is shared by the API and the eval scripts by design: they
previously diverged, and the logged numbers described a pipeline nothing could
serve (`experiments.md` E7).
