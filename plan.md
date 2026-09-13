# Plan

## Main features
- Legal-document ingestion: parse Laws PDFs, chunk by section/subsection, preserve hierarchy + metadata.
- Retrieval: find relevant chunks for a question.
- LLM answer generation: grounded, cited, refuses/flags when unsupported or ambiguous.
- Citations: section-level, traceable to source excerpt.
- Legal-info disclaimer (not legal advice).
- Document update path: add/replace a law without rebuilding everything.
- Evaluation: dataset + repeatable scoring + failure-case review.

## Step-by-step pipeline
1. ~~Dedup Laws PDFs, pick canonical copy per act.~~ (done)
2. Parse PDFs → structured text (act > section > subsection).
3. Chunk respecting section boundaries, attach metadata (act, section, source).
4. ~~LLM-only baseline (no retrieval) — establish floor.~~ (done: tested Qwen2.5-VL-3B local + Gemini 2.5 Flash API on 5 sample questions)
5. Basic vector retrieval + LLM (RAG v1).
6. Legal-aware chunking improvements, re-test retrieval quality.
7. Hybrid retrieval / reranking.
8. Prompt improvements (grounding, citation formatting, refusal behavior).
9. Citation validation (cited text actually matches source).
10. Hallucination reduction pass.
11. Build eval dataset, run evaluation, log failure cases per stage above.
12. Wire real pipeline into existing `/api/query` backend (replace mock module).
13. Docs: architecture diagram, README updates, AI_USAGE.md.
