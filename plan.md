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

1. Dedup Laws PDFs, pick canonical copy per act. — **not done** (CrPC ×3, 5 acts ×2 still on disk)
2. ~~Parse PDFs → hierarchical JSON.~~ **done for 3 main acts** — see `parsing-plan.md`
3. ~~Verify the parse against the PDF + an independent oracle.~~ **done** — see `verification.md`
4. ~~LLM-only baseline (no retrieval) — establish floor.~~ **done** (Qwen2.5-VL-3B local, Gemini 2.5 Flash API)
5. **← NEXT:** Chunk the parsed JSON, embed, load a vector store.
6. Basic vector retrieval + LLM (RAG v1).
7. Parse the remaining 25 in-scope acts.
8. Hybrid retrieval / reranking.
9. Prompt improvements (grounding, citation format, refusal behaviour).
10. Citation validation (cited text matches source).
11. Eval dataset, repeatable scoring, failure-case log.
12. Wire the pipeline into `/api/query` (replace the mock module).
13. Docs: architecture diagram, README, AI_USAGE.md.

## Next step in detail (step 5)

- One chunk per section; split at subsection boundaries when oversized.
- Prepend the hierarchy path to the embedded text so a bare "Any police-officer
  may arrest…" carries its context. Ancestry is already inline on every parsed
  section, so no tree walk is needed.
- Keep `act / part / chapter / section / page` as metadata for filtering and
  for rendering citations.
- Pick an embedding model and vector store (see `findings.md`: the embedder
  matters more than the generator for legal RAG).

## Open items
- 25 acts unparsed; 6 duplicate PDFs to remove first.
- CrPC: 7 sections missing vs oracle, 4 duplicate numbers. Penal: s.59 in a range.
- Sub-section gloss titles parsed but not yet attached to their subsections.
- CrPC Second Schedule (offence table) absent from every source we have.
