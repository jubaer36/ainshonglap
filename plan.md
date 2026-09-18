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
5. ~~Chunk the parsed JSON.~~ **done** — 1,321 chunks, verified — see `chunking-plan.md`
6. ~~Embed the chunks, build an index, measure retrieval.~~ **baseline done** — BGE-M3 + FAISS, Recall@1 31% / @20 68%, Act accuracy 81% — see `retrieval-baseline.md`
7. ~~Validate chunking assumptions (prefix, budget).~~ **done** — both flat, see `experiments.md` E2/E3
8. ~~Cross-encoder reranking.~~ **done** — R@1 32.7% → **41.9%**, Act acc. 82% → **87%** (E4)
9. **← NEXT:** Query rewriting — 32% of gold sections are never retrieved, which reranking cannot reach.
10. Parse the remaining 25 in-scope acts.
11. Answer generation with citations + refusal path.
12. Prompt improvements (grounding, citation format, refusal behaviour).
13. Citation validation (cited text matches source).
14. Eval dataset extension + failure-case log.
15. Wire the pipeline into `/api/query` (replace the mock module).
16. Docs: architecture diagram, README, AI_USAGE.md.

## Next step in detail (step 9) — full reasoning in `retrieval-plan.md`

- **Query rewriting is now the biggest single bucket.** 31.6pp of gold sections
  are never retrieved at all, and no reranker can reach them. The failures show
  a register mismatch: questions are narrative ("I stole a bag of grain from a
  neighbour's building"), statutes are drafted ("Whoever commits theft in any
  building"). HyDE or LLM query expansion targets exactly this.
- **Still worth testing for the remaining 26.5pp of ranking headroom**:
  structure-aware reranking (28% of chunks cite another section) and metadata
  Act-filtering (cheapest fix for the residual 12.8% wrong-Act rate).
- **Refusal path in parallel**: domain-adapted models are reported to
  hallucinate *more* when evidence is missing, so abstention needs measuring as
  its own metric, not treating as a side effect.

## Open items
- 25 acts unparsed; 6 duplicate PDFs to remove first.
- CrPC: 7 sections missing vs oracle, 4 duplicate numbers. Penal: s.59 in a range.
- Sub-section gloss titles parsed but not yet attached to their subsections.
- 39 chunks exceed 512 tokens; fine for an 8k-window embedder, not for a 512 one.
- CrPC Second Schedule (offence table) absent from every source we have.
