# AinShonglap frontend

Next.js 16 and React 19 client for the AinShonglap Bangladesh criminal-law
assistant. It consumes the FastAPI contract documented in `../backend/API.md`.

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
lib/api.ts                           all backend calls and normalization
components/chat/AnswerKindBadge.tsx  how an answer was produced, in three words
lib/types.ts                         frontend API contract
```

## Current limitations

- Chat state is in-memory and disappears on refresh.
- The frontend sends history, but the backend does not yet use it for follow-up resolution.
- Corpus browsing loads the first 40 matches without a “load more” control.
- The statute browser has a current React lint error for synchronous state updates inside an effect.
- Network errors render as `kind: "refused"`, sharing the amber treatment with a real refusal, although their messages differ.
