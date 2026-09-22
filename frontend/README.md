# AinShonglap frontend

Next.js 16 and React 19 client for the AinShonglap Bangladesh criminal-law
assistant. It consumes the FastAPI contract documented in `../backend/API.md`.

The UI carries no decorative copy. Text that described sources the corpus does
not have — "Supreme Court Precedents & Gazettes", "Verified Precedents" — was
removed rather than reworded: for a tool whose value is that every claim is
checkable, chrome that overstates its sources is worse than none. The required
disclaimer is appended server-side to every answer, so it appears where it is
read instead of permanently under the input box.

## Features

- chat input with loading, timeout and provider-error states
- structured assistant blocks rather than untrusted HTML or Markdown
- inline citation pills with a source-text preview drawer
- statute browser backed by `/api/acts` and `/api/sections`
- on-demand section loading instead of downloading the corpus at startup
- selectable schema-capable answer generators from `/api/models`
- responsive sidebar and reference drawer
- distinct rendering per answer `kind`: quoted sections, chapter listings,
  cited answers, ungrounded general answers, out-of-scope boundaries,
  clarification questions and refusals
- a provenance badge stating how each answer was produced — quoted verbatim from
  the catalog, cited and checked, or not from the Acts at all
- voice input beside the Ask button: click to record, click to stop, and the
  transcript lands in the box for you to check. It is never sent automatically,
  because a misheard section number becomes a fully grounded answer about a
  different offence and no gate downstream can catch that
- a `context · N turns` chip opening the conversation memory: the running
  summary verbatim, the section keys kept exactly, a stale warning when the
  summariser failed, and a Forget button
- the model picker lists an alias and the provider's real model id and nothing
  else, best first (`oss120`, `qwen`, `oss`, `gemini`, then the rest). It greys
  out routes that are out of quota, with a countdown, and moves a dead selection
  to the fallback. A Groq route is only out once **every** one of its API keys
  is spent, so a per-minute limit on one key does not grey anything out
- the waiting message follows the pipeline instead of claiming one thing for
  every question: "Working out what you're asking…" while the planner
  classifies, then "Finding the provisions that apply…", then "Reading them and
  checking every citation…". These are timed from the measured stage latencies,
  not observed — `POST /api/query` is one round trip, so the browser cannot see
  the backend's stage
- a welcome screen that makes the storage choice explicit before anything is
  typed: guest (nothing saved anywhere) or an account (conversations stored)
- Supabase sign-in on its own page at `/signin`, with the account and a sign-out
  at the foot of the sidebar, and a guest prompt in the same place
- conversations listed in the sidebar for signed-in users, loaded from the API;
  reopening one restores the messages, the context each answer was produced
  against, and any page images. A row is retitled in place — pencil icon or
  double-click, Enter or blur commits, Escape abandons — since the default
  title is the first question asked and that is often the wrong label for a
  conversation that moved on. After three answered turns the server proposes a
  name of its own, unless the title has already been made the reader's — and
  deleting asks first, in the row rather than in a browser dialog, with focus on
  Cancel so a stray Enter cannot confirm it
- **Try again appears only on the last message.** Retry re-asks the question and
  replaces that answer in place, so offered mid-conversation it would rewrite an
  answer later turns were already built on. The affordance is withheld rather
  than disabled: a greyed-out button implies an action you can wait for, and
  this one never returns
- Enter sends, Shift+Enter inserts a newline, and Enter is ignored while an IME
  is composing (typing Bangla would otherwise fire mid-word)

The TypeScript contract in `lib/types.ts` mirrors the backend Pydantic models in
`backend/app/models/schemas.py`. Snake-case API fields are converted to
camel-case in one place: `lib/api.ts`.

## Run locally

Start the backend at `http://localhost:8000`, then:

```bash
npm install
npm run dev
```

Open <http://localhost:3000>.

To use another backend URL, create `.env.local`:

```env
NEXT_PUBLIC_API_URL=http://localhost:8000
```

## Commands

```bash
npm run dev
npm run build
npm run start
npm run lint
```

## Main files

```text
app/page.tsx                          page entry
components/chat/ChatShell.tsx        conversation and API state
components/chat/BlockRenderer.tsx    structured answer rendering
components/layout/StatuteBrowser.tsx corpus navigation
components/layout/ReferencesDrawer.tsx citation/source preview
components/chat/ModelPicker.tsx      generator selection
components/chat/ThinkingBubble.tsx   stage-phased waiting message
components/layout/ConversationList.tsx sidebar rows, rename and delete
lib/api.ts                           all backend calls and normalization
lib/conversations.ts                 saved conversations, via the API
lib/supabase.ts                      Supabase client — authentication only
app/signin/page.tsx                  sign in / create account
components/auth/AuthProvider.tsx     session state
components/auth/WelcomeGate.tsx      guest or account, before anything else
components/layout/SidebarAccount.tsx account and sign-out, bottom left
components/chat/AnswerKindBadge.tsx  how an answer was produced, in three words
components/chat/MemoryPanel.tsx      the running summary the next question is read against
components/chat/MicButton.tsx        record, transcribe, place in the box — never send
components/layout/ConversationList.tsx  saved conversations, newest first
lib/types.ts                         frontend API contract
```

## Current limitations

- Chat state is in-memory and disappears on refresh.
- The frontend sends history, but the backend does not yet use it for follow-up resolution.
- Corpus browsing loads the first 40 matches without a “load more” control.
- The statute browser has a current React lint error for synchronous state updates inside an effect.
- Network errors render as `kind: "refused"`, sharing the amber treatment with a real refusal, although their messages differ.
