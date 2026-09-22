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
| DELETE | `/api/conversations/{id}` | delete one |
| POST | `/api/conversations/import` | adopt conversations made before signing in |
| GET | `/health` | liveness, index stats, configured models |

### POST `/api/query`

```json
{"question": "What is the punishment for theft?"}
```

Returns an `AssistantMessage`: `heading`, `blocks` (paragraphs with inline
citation segments), `citations` keyed by citation key, `unsupported`, `kind`,
and — for a conversation — `memory`, `resolved_question`, `generator`.

#### Conversation memory

Send the `memory` from the previous answer back with the next question:

```json
{"question": "what if he had survived instead?",
 "memory": {"summary": "The user described three men beating a man...",
            "sections": ["penal-s302", "penal-s304"], "turns": 1}}
```

A rolling summary, rewritten after every turn by Gemini 3.5 Flash Lite on
`GEMINI_API_KEY_2`. The planner decides `follow_up` itself, **defaulting to
false**, and rewrites a follow-up into a standalone question; `resolved_question`
comes back so the reader can see what was actually answered. A bad rewrite would
otherwise be a confident, properly cited answer to a question nobody asked.

The server stores nothing — the client holds the memory and echoes it. For
questions this personal that is a privacy property, and it removes the session
store entirely. `sections` is written by code from the answer's own citations,
never by the summariser, because a rolling summary is lossy and the detail most
likely to be mangled is a section number.

`generator` names the model that answered, and `generator_substituted` the alias
it replaced when the requested one was out of quota.

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
| `meta` | false | 0 | about the assistant, assembled from the catalog manifest |
| `general` | false | 0 | **not** a legal question; the model's own knowledge, uncited and unchecked |
| `out_of_scope` | true | 0 | a legal question about law outside the three Acts. Nothing was searched. |
| `clarify` | true | 0 | the request needs one more detail — an ambiguous section number, a subjective selection |
| `refused` | true | 0 | retrieval ran and nothing survived the citation gates |

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
refusal. The disclaimer is appended server-side from a constant, on every path,
so the model cannot omit it.

### GET `/api/plan?q=...`

What the planner decided, without answering. One small model call — no
retrieval, no generation.

```json
{"category": "structural", "operation": "enumerate",
 "act": "evidence", "chapter": "V", "sections": [],
 "needs_clarification": false, "by": "llm",
 "notes": ["chapter-lookup: 'DOCUMENTARY EVIDENCE' -> V in evidence"]}
```

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

**The planner runs on Gemini 3.5 Flash Lite** (`gemini2`, on `GEMINI_API_KEY_2`
so it does not share a quota with anything else), with Groq Qwen as an automatic
fallback. A different *provider*, not just a different model: Groq's per-minute
and daily limits are per-account, so a second Groq route waits behind the same
wall. Measured 1.3-1.8s against 43s on a rate-limited Groq. `gemini2` is marked
`internal` in the registry and so never appears in the model picker.

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
exists.

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

**Internal routes are not listed.** `gemini2` runs the planner and the
conversation summariser on `GEMINI_API_KEY_2`; offering it as an answer model
would let a reader spend the classifier's quota and then wonder why the next
question stopped being routed. It stays resolvable by alias — it is simply not
advertised. `config.selectable()` is the filter, and the 422 "unknown model"
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

### GET `/api/retrieve?q=...&k=5&rerank=true`

The retrieval half alone, with dense and reranker scores per hit. `rerank=false`
gives the dense-only ordering — the same code path the eval scripts measure, so
served and measured numbers cannot drift.

## Configuration

`backend/.env.local` (gitignored) holds the keys: `GROQ_API_KEY`,
`GROQ_API_KEY_2`, `GROQ_API_KEY_3` (see **Groq key rotation** below),
`INCEPTION_API_KEY` (Mercury), `GEMINI_API_KEY`, `GEMINI_API_KEY_2` (the planner
and the conversation summariser, on their own key so they never compete for
quota), `DEEPGRAM_API_KEY` (voice), `OPENROUTER_API_KEY`.

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

A provider with one key (Inception, OpenRouter) yields a single slot and behaves
exactly as it did before. `RAG_TRACE` logs each switch (`key GROQ_API_KEY_2 429
-> next key`), and `/health` names which keys are down rather than only that the
model is.

| var | default | |
|---|---|---|
| `RAG_GENERATOR` | `oss120` | alias returned by `GET /api/models` |
| `RAG_GROQ_MODEL` | `qwen/qwen3.8-27b` | direct Groq model id |
| `RAG_LLM_BACKEND` | `groq` | `groq`, `openrouter`, `gemini`, `inception` |
| `RAG_INCEPTION_MODEL` | `mercury-2.5` | Inception Mercury default |
| `RAG_PLANNER_MODEL` | `gemini2` | the planner — decides category and coordinates |
| `RAG_PLANNER_FALLBACK` | `qwen` | tried when the planner's own model fails |
| `RAG_TRACE` | `1` | one concise log line per pipeline step; `0` silences |
| `RAG_PLAN` | `1` | `0` falls back to the old pattern router |
| `RAG_ROUTER_MODEL` | `lfm` | old router's model, used only when `RAG_PLAN=0` |
| `RAG_DENSE_K` / `RAG_FINAL_K` | `20` / `8` | candidates reranked / sections returned |
| `RAG_FALLBACK_GENERATOR` | `mercury` | used when the chosen generator is out of quota |
| `RAG_MEMORY_MODEL` | `gemini-3.5-flash-lite` | rolling conversation summariser |
| `RAG_MEMORY_CHARS` | `700` | ceiling on the summary, so context stays bounded |
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
  config.py     models, paths, k values
  encoder.py    BGE-M3 query/document embedding
  store.py      FAISS index + chunk records
  reranker.py   cross-encoder (E4)
  pipeline.py   retrieve() — dense, dedupe, rerank
  llm.py        Groq / OpenRouter / Gemini, JSON-schema output
  catalog.py    structural read side of catalog.sqlite — exact fetch by
                coordinates, parameterized SQL, empty on a bad coordinate
  plan.py       the planner: question -> category + coordinates, then
                validated against the catalog
  execute.py    one executor per category; plan -> Answer
  answer.py     prompt, generation, gating, refusal
  verify.py     C2 + C3 citation gates
  citations.py  chunk metadata -> Citation
  library.py    browsable corpus (older in-memory view; catalog.py supersedes
                it for structural queries)
  refine.py     the pattern router, used only when RAG_PLAN=0
  speech.py     Deepgram STT — audio -> transcript, never an answer
  memory.py     rolling conversation summary; prose from a model, section keys
                from code
  keyring.py    several API keys per provider: rotation, and failover on 429
  quota.py      per (model, key) exhaustion and cooldown, for fallback and the picker
  present.py    Answer -> AssistantMessage
```

Two read models, two jobs: **FAISS** answers "which provisions resemble this
question", **`catalog.sqlite`** answers "which provisions *are* these". Both are
rebuilt from the parsed JSON, which stays the only source of law.

`pipeline.retrieve()` is shared by the API and the eval scripts by design: they
previously diverged, and the logged numbers described a pipeline nothing could
serve (`experiments.md` E7).
