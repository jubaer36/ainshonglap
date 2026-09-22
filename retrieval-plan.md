# Retrieval: options and decision

What to do with the 1,321 chunks. Decided from published results for
**statutory** retrieval specifically — which repeatedly disagrees with general
RAG advice — cross-checked against measurements of our own corpus.

## What our corpus actually looks like

| | |
|---|---|
| chunks | 1,321 (3 acts); ~10k projected at 28 acts |
| median / max tokens | 133–172 / 945 |
| chunks citing another section | **371 (28%)** |
| chunks naming a different Act | 60 (5%) |
| most cross-referenced | Penal Code (91), CrPC (15), Evidence Act (10) |

Two consequences. The corpus is **small** — small enough that infrastructure
choice is nearly free and heavyweight retrieval machinery is unlikely to pay
for itself. And it is **cross-referential**: more than a quarter of chunks point
at another provision, which is the precise condition the statutory-retrieval
literature says breaks similarity-only search.

## The finding that shapes the design

[Chae et al., *Beyond Case Law: Evaluating Structure-Aware Retrieval and Safety
in Statute-Centric Legal QA*](https://arxiv.org/pdf/2604.06173) names the
problem the **statutory retrieval gap**: in statutes, "relevant evidence is
distributed across hierarchically linked documents... conventional retrievers
fail and models often hallucinate under incomplete context."

Their example is ours. A provision states a requirement but delegates the
*definition* to another Act; answering correctly means following the delegation
chain. CrPC s.4 does exactly this, handing definitions to the Penal Code.

Their fix — **Structure-Aware Reranking**, using explicit statutory
cross-reference links as a graph — measured a **1-hop hit rate of 0.156 against
0.041 for cosine-kNN neighbourhoods**. Roughly 4× better at bridging to the
right evidence, because "structural links expose 'gap' evidence that
similarity-based neighbourhoods miss."

We can build that graph cheaply: section numbers are already structured
metadata on every chunk.

## Options

### Embedding model

A legal-specific benchmark now exists —
[MLEB](https://huggingface.co/blog/isaacus/introducing-mleb) — so this is
measurable rather than a matter of taste.

| model | MLEB NDCG@10 | notes |
|---|---|---|
| Kanon 2 Embedder | 86.03 | commercial |
| Voyage 3 Large | 85.71 | commercial |
| Voyage 3.5 | 84.07 | commercial |
| Voyage Law 2 | 79.63 | commercial, legal-tuned, compact |
| **BGE-M3** | not ranked here | **free, self-hosted, 8192-token window** |

MLEB's own conclusion is the useful part: "the qualities that make an embedding
model good at general multilingual information retrieval are not necessarily
the same as those that make a model good at legal information retrieval." A
general leaderboard is the wrong instrument for this choice.

**Decision: start with BGE-M3.** Its 8192-token window swallows our 39
oversized chunks without truncation, it keeps ingestion free and offline, and
the statute paper above tested precisely BGE-M3 and Qwen3-Emb-0.6B. A paid
model is an upgrade to be *measured*, not assumed — and the baseline already
showed the generator is not our weak link.

### Hybrid BM25 + dense — evidence says not yet

This is where general advice and statute-specific evidence part company.

- **General RAG**: hybrid clearly wins — recall ~0.72 (BM25) → ~0.91 (hybrid).
- **Statutes**: "Dense retrieval consistently outperforms BM25 on Korean
  statutory text... applying RRF on top of dense retrieval did not provide
  consistent benefits, with RRF slightly degrading Recall and nDCG."
- **Short-statute corpora** (closest to ours): "a cheap bge-small hybrid
  retriever was not beaten by a heavier retriever with cross-encoder
  reranking" — heavier machinery bought no measurable improvement.

**Decision: dense-only first.** Hybrid becomes a hypothesis to test against our
own eval set, not a default to adopt on general reputation.

### Vector store — deliberately boring

At 1,321 chunks now and ~10k at full corpus, the store is not a real decision.
FAISS or Chroma locally is sufficient; Qdrant if metadata filtering and
persistence are wanted without effort. Anything requiring a server is
overbuilt at this scale.

Retrieval must filter and cite on `act / chapter / section / page`, all of
which already sit on every chunk.

### Reranking — approaches considered

Reordered after the baseline was measured: the dominant failure turned out to
be *ranking*, not recall (R@1 33% against R@20 68%), which makes a cross-encoder
the first move rather than the third.

| approach | verdict |
|---|---|
| **Cross-encoder** (`bge-reranker-v2-m3`) | **first choice.** Reads query and chunk together instead of comparing two independently produced vectors — exactly the thin-margin problem the baseline shows. Reported 50-67% win rate on queries under 300 chars, and **93% of our eval questions are under 300** (median 198). Free, local, pairs with the embedder in use. |
| Qwen3-Reranker-0.6B | alternative. Flat across query lengths with no penalty past 300 chars, marginally stronger on MTEB. Worth trying if the 7% long-query tail proves to matter. |
| ColBERT / late interaction | good technology, wrong problem. Its advantage is throughput under load (20.7ms p50 vs cross-encoder tail latency in seconds) — we have no QPS pressure, and it costs a much larger token-level index plus full re-indexing. |
| **Structure-aware reranking** | strong corpus-specific evidence (1-hop hit rate 0.156 vs 0.041 for cosine-kNN; 28% of our chunks cite another section). Second, after the cross-encoder. |
| **Metadata Act-filtering** | cheapest available fix for the measured 18% wrong-Act rate. Hard-filter when the question names an act. Risk: fails closed when it does not. |
| LLM-as-reranker (RankGPT) | deferred. Expensive per query, slow, non-deterministic — awkward in a citation tool, and it would be an LLM solving a problem smaller models handle. |
| RRF / score fusion | nothing to fuse while dense-only, and statute evidence on RRF is weak-to-negative. |

**Ceiling to keep in mind:** reranking only reorders what retrieval already
found, so it cannot beat Recall@20 (68%). The other 32% needs query rewriting.

## Safety: plan for it now, not after

The same paper reports a counterintuitive result: **domain-adapted models are
*more* likely to hallucinate when key statutory evidence is missing.** Legal
fine-tuning makes refusal behaviour worse, not better.

This speaks directly to the assessment's requirement to handle unsupported and
ambiguous questions. The refusal path and its evaluation should be built
alongside retrieval rather than bolted on, and "was evidence actually
retrieved?" should gate answering.

## Plan

Status: the dense baseline, evaluation harness, cross-encoder reranker,
dual-granularity index and shared serving pipeline are built. Live results are
R@1 41.8%, R@5 59.0%, R@20 71.4%, MRR 0.498 and Act accuracy 87.5% (E7).
The fitted score threshold is weak (AUC 0.727) and remains opt-in (E10);
downstream citation gates are live, while generation abstention is not yet
measured completely.

1. **Baseline** — BGE-M3, local store, dense-only. Index 1,321 chunks with
   metadata.
2. **Eval set before tuning.** `Laws/bangladesh-legal-qa-dataset` ships 2,165 QA
   pairs carrying Act and section metadata — a ready-made gold standard for
   "did retrieval return the right section?". Use the English single-hop split
   first.
3. **Measure**: Recall@k and nDCG@k against gold sections. Nothing is adopted
   before this exists.
4. **Then test, in descending order of evidence**: cross-encoder reranking was
   adopted; structure-aware cross-reference expansion and hybrid BM25 remain
   untested.
5. **Refusal path** in parallel: abstain when retrieval is weak, and measure
   abstention as its own metric.

## Current next questions

- Can model-generated statutory query facets raise the 71.4% candidate ceiling?
- Can explicit cross-reference expansion improve multi-section coverage on top
  of the existing reranker?
- What relevance threshold gives useful comprehensive discovery without flooding
  the result with adjacent but immaterial provisions?

These now sit inside `query-routing-plan.md`, which separates the fixed-count
focused answer path from broad, thresholded and paginated discovery. Parsing the
remaining Acts remains a corpus-expansion task, not a prerequisite for measuring
the current three-Act pipeline.
