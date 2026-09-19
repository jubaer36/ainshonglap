# Similar projects: what others chose at each step

Survey of legal / Bangla RAG repositories, to sanity-check our stack against
what people actually ship. Short by design — one row per project per stage.

## The three named repositories

| | LegalEase-BD | bangla-legal-assistant | AI_Bangla_FAQ_Bot_RAG |
|---|---|---|---|
| corpus | 347 statutes, 35,472 chunks | Constitution, family/rent law (Bangla) | Bangla FAQ set |
| chunking | pre-split by act + language; method undocumented | undocumented | undocumented |
| embedder | `paraphrase-multilingual-MiniLM-L12-v2` (384-d) | not stated | Bengali SBERT (l3cube-pune) |
| store | ChromaDB (~2.7 GB) | FAISS | FAISS |
| retrieval | hybrid: `0.6×semantic + 0.4×BM25` | plain similarity | topic routing → metadata filter → FAISS |
| reranker | none | none | none |
| LLM | LLaMA-3.3-70B via Groq (temp 0.1) | Gemini 2.5 Flash | GPT-4.1-Nano |
| eval | 62 test queries, **no metrics reported** | none | none |

## Statute-focused projects that publish numbers

These are far closer to our problem than the Bangla FAQ bots — statutes rather
than case law, section-level citation, and in two cases actual metrics.

### `DamnKuldeep/KnowYourRightsAI` — the closest comparator

Indian law, citations traced to exact statutes. **Same embedder as us
(`BAAI/bge-m3`, 1024-d.)** 38,890 chunks over 35,170 sections in LanceDB;
480-word chunks with 80-word overlap, short sections kept whole; RRF over dense
+ BM25, then cross-encoder reranking over a pool of 8 (deployed) or 24 (GPU).

Reports **98% recall@5** (95.2% on CPU config). Our R@5 is 57.7%.

**That gap is not what it looks like, and the difference matters:**

| | theirs | ours |
|---|---|---|
| eval size | **42 gold questions** (+11 refusal, 4 lookup) | **608** |
| hit criterion | "the top 5 almost always contains the right provision" | exact `(Act, section)` match |
| queries | written naturally by citizens | from an independent dataset, unseen by us |

At n=42 the confidence interval is roughly ±6pp at best, so 98% and 90% are
not separable there. More importantly the two numbers measure different things:
theirs is per-question judgement of whether the right provision appeared,
ours is a mechanical `(Act, section)` string match that counts a right section
number under the wrong Act as a miss. **These are not comparable scores**, and
citing the 98% against our 57.7% would be the same error as importing
Anthropic's 49% in E2.

**But one of their choices is directly worth stealing** — see below.

### `LegalRAG` (Indian High Court judgments)

538,079 chunks from 100,000 judgments across 24 jurisdictions. Publishes real
numbers, and one cuts against our design: **BM25 beats dense at top-1 recall,
42.66% vs 19.72%**, with RRF reaching 65.79% candidate recall at top-50.

Worth holding lightly — that is *case law*, where party names and citation
strings are high-signal lexical tokens. Statutes have no such handles, which is
why the statute-specific literature reports the opposite. Still, it is the
strongest published argument for testing hybrid on our corpus rather than
deferring it indefinitely.

### `toddlar00/rag-pipeline` — the most complete pipeline found

Docling GPU layout parsing with OCR fallback, structure-aware token-budgeted
chunking with **TOC-based hierarchical section paths**, nomic MoE embeddings,
Chroma/Qdrant, weighted RRF (dense 0.5, sparse 1.0), **adaptive BGE
cross-encoder reranking**, and **explicit abstention** — it withholds answers
containing uncited text. Independently arrived at several of our decisions.

Notably it reports **no retrieval metrics at all**, preferring "deterministic
validation over numeric benchmarks" — source-fidelity and semantic-hierarchy
gates. A defensible engineering stance, but it means the pipeline cannot tell
whether any individual choice in it actually helps.

### Others

| project | notable choice |
|---|---|
| `rasinmuhammed/ratio` | 313-query eval (288 exact-citation, 25 conceptual); authority-weighted retrieval, CRAG fallback |
| `SidakSethi-Singh/LawSathi-Rag` | statutory **section-aware chunking**, RAGAS evaluation |
| `krisha-kalal/LawSaathi` | layout-aware ingestion, **overlapping parent chunking** to preserve statutory boundaries |
| `iloveai-ml/Legal-RAG` | ~19k chunks, hybrid BM25 + vector with RRF, verifiable citations |
| `sannx4/Citation-Aware-Legal-Research-Engine` | citation **knowledge graph**, authority ranking |
| `hoorangyee/LRAGE` | a legal-domain **RAG evaluation framework** (arXiv 2504.01840) — retriever + reranker + rubric-based generation scoring |
| `yakkalakarthikeya/LegalRAG-AI` | clause-level chunking; faithfulness/hallucination evaluation |
| `RajivD45/Full-RAG-Chatbot` | metadata-aware chunking, BGE embeddings |

## One differentiator per project

Each project's distinctive bet, and whether it is relevant to us. Marked
**[verified]** where the repo/docs were read directly, **[described]** where
this comes from the repo's own summary only.

### Attacking the register gap (question language ≠ statute language)

| project | the idea | relevance |
|---|---|---|
| `KnowYourRightsAI` **[verified]** | LLM pre-generates "the plain-language questions a citizen would ask" for every section, embedded *alongside* the statutory text | **highest.** Index-side fix for our 31.6pp never-retrieved bucket; computed once at ingestion |
| `AI_Bangla_FAQ_Bot_RAG` **[verified]** | LLM **topic routing then metadata filter before** the vector search, not after | narrows the candidate pool up front — a cheaper cousin of Act-filtering for our 12.8% wrong-Act rate |

### Attacking structure and hierarchy

| project | the idea | relevance |
|---|---|---|
| `toddlar00/rag-pipeline` **[verified]** | **TOC-based hierarchical section paths** + RAPTOR multi-level summaries | convergent with our Part>Chapter>Section parse; RAPTOR (recursive summarisation into a tree) is untried by us |
| `krisha-kalal/LawSaathi` **[described]** | **overlapping parent chunking** to preserve statutory boundaries | same family as our E5 dual granularity, arrived at independently |
| `yakkalakarthikeya/LegalRAG-AI` **[described]** | **clause-level** chunking as the retrieval unit | finer than our subsection level; E5 suggests finer alone loses context |
| `RajivD45/Full-RAG-Chatbot` **[described]** | page-type classification driving **metadata-aware** chunking | close to our contents-page detection and furniture stripping |

### Attacking citation trust

| project | the idea | relevance |
|---|---|---|
| `toddlar00/rag-pipeline` **[verified]** | **withholds any answer containing uncited text** — abstention as a hard gate, not a prompt instruction | directly serves the assessment's "unsupported questions" requirement; we have not built this |
| `sannx4/Citation-Aware-Legal-Research-Engine` **[described]** | citation **knowledge graph** + authority ranking | the graph idea matches structure-aware reranking (28% of our chunks cite another section) |
| `rasinmuhammed/ratio` **[described]** | **stance detection** + authority-weighted retrieval + CRAG fallback; 313-query eval split into exact-citation vs conceptual | splitting the eval by question *type* is a cheap idea we could copy tomorrow |
| `iloveai-ml/Legal-RAG` **[described]** | "verifiable citations" as the framing across ~19k chunks | same goal, no published method detail |

### Attacking retrieval mechanics

| project | the idea | relevance |
|---|---|---|
| `LegalRAG` (High Court) **[described]** | scale — 538,079 chunks, and the published finding **BM25 > dense at top-1 (42.66% vs 19.72%)** | the strongest published argument against our dense-only stance, though on case law not statutes |
| `LegalEase-BD` **[verified]** | explicit hand-set score fusion `0.6×semantic + 0.4×BM25`, and **separate EN/BN collections** rather than one multilingual index | the language-split idea matters if Bangla is ever in scope |
| `sougaaat/RAG-based-Legal-Assistant` **[described]** | multi-hop / multi-query with RRF | multi-query is untried by us and cheap |

### Attacking time

| project | the idea | relevance |
|---|---|---|
| `LLM-FORGE` **[described]** | **Temporal Statutory Law Engine** — tracks the 1 July 2024 repeal of the IPC/CrPC/Evidence Act and maps historical sections to their BNS/BNSS/BSA replacements, with statutory warnings | the one genuinely novel category here. We hold 541 CrPC amendment notes parsed but unlinked; this is what they are *for* |

### Attacking evaluation itself

| project | the idea | relevance |
|---|---|---|
| `hoorangyee/LRAGE` **[described]** | a legal-domain **RAG evaluation framework** — retriever + reranker + instance-level rubric scoring for generation (arXiv 2504.01840) | we measure retrieval well and generation not at all; this is the obvious tool when that starts |
| `SidakSethi-Singh/LawSathi-Rag` **[described]** | RAGAS metrics on statutory section-aware chunking | RAGAS is the conventional answer for faithfulness scoring |
| `yakkalakarthikeya/LegalRAG-AI` **[described]** | explicit faithfulness / hallucination evaluation | same gap |

### The three original repositories, briefly

| project | its bet |
|---|---|
| `LegalEase-BD` | **breadth** — 347 statutes, 35k chunks, bilingual, hybrid fusion. Widest corpus of the three |
| `bangla-legal-assistant` | **Bangla-native end to end** — corpus, UI and answers all in Bangla, aimed at practising lawyers |
| `AI_Bangla_FAQ_Bot_RAG` | **routing before retrieval** — topic classification and metadata filtering ahead of the vector search, with an explicit safe fallback |

## The one idea worth stealing

KnowYourRightsAI enriches each section **before** embedding: an LLM generates
"the plain-language questions a person might ask", and those are embedded
alongside the statutory text.

That attacks our largest remaining bucket directly. Our 31.6pp of never-retrieved
gold sections is a **register mismatch** — questions are narrative ("I stole a
bag of grain from a neighbour's building"), statutes are drafted ("Whoever
commits theft in any building"). Query rewriting attacks this from the query
side; section enrichment attacks it from the *index* side, and has the practical
advantage of being computed once at ingestion rather than per query.

Both are testable against our 608-question set. Neither should be adopted
without that.

## Stage-by-stage: options seen in the wild

**Chunking.** Fixed/recursive splitting dominates the Bangla FAQ bots. The
statute-focused projects do better: section-aware (LawSathi), clause-level
(LegalRAG-AI), overlapping parent chunks (LawSaathi), TOC-based hierarchical
paths (rag-pipeline). KnowYourRightsAI chunks at 480 words / 80 overlap but
keeps short sections whole — close to our section-level unit.

**Embedders.** MiniLM 384-d, Bengali SBERT, nomic MoE, generic SBERT — and
`bge-m3` in the closest comparator, the same model we chose independently. No
legal-domain embedder (Kanon, voyage-law) anywhere in this set.

**Stores.** FAISS, Chroma, Qdrant, LanceDB. All local. Nobody treats this as a
difficult decision, which matches our reading.

**Retrieval.** Split. The simpler projects run plain dense; the statute-focused
ones run hybrid BM25 + dense fused with RRF, and `rag-pipeline` publishes its
weights (dense 0.5, sparse 1.0). Our dense-only stance is the minority position
among the serious projects.

**Reranking.** More common than the first survey suggested: KnowYourRightsAI
and `rag-pipeline` both use cross-encoder reranking, matching our E4 result.
The Bangla FAQ bots do not.

**Abstention.** `rag-pipeline` withholds answers containing uncited text;
KnowYourRightsAI keeps 11 questions specifically designed to be refused. Both
treat refusal as a first-class feature — which the statute literature also
warns about and our plan has not built yet.

**Evaluation.** Ranges from nothing to reasonable. KnowYourRightsAI (42 gold
questions), `ratio` (313 queries), LawSathi (RAGAS) all measure something;
`rag-pipeline` deliberately does not, preferring structural gates. `LRAGE`
exists as a legal-specific RAG evaluation framework and is worth a look when
generation evaluation starts.

## What this says about our choices

**Consistent with the better projects:** local vector store, `bge-m3`,
section-aware chunking, cross-encoder reranking, gold-labelled evaluation.

**Where we differ:**

| our choice | field | assessment |
|---|---|---|
| dense-only | statute projects mostly run hybrid RRF | **the weakest of our positions.** Deferred on statute-specific evidence, but the serious comparators disagree, and `LegalRAG` measured BM25 beating dense at top-1 on legal text. Worth testing rather than continuing to defer. |
| no index-side enrichment | KnowYourRightsAI pre-generates likely questions per section | **a gap.** Directly targets our largest remaining bucket. |
| 608-question eval, exact `(Act, section)` match | 42-313 questions, looser criteria | ours is larger and stricter; not a quality claim, a measurement-design difference |
| no abstention yet | two projects treat it as first-class | a gap, and the assessment requires it |
| dual granularity (E5) | parent chunking (LawSaathi), clause-level (LegalRAG-AI) | convergent — arrived at independently |

**The honest read.** The first pass at this survey flattered us because it
sampled the wrong projects — Bangla FAQ bots with no metrics. Against the
statute-focused set the picture is less comfortable: they rerank too, several
evaluate, most run hybrid retrieval we have deferred, and two treat abstention
as a feature we have not built.

What still distinguishes this project is not any individual component but that
**every component choice is attached to a number measured on the same 608
questions** — including two that turned out to do nothing (E2, E3) and one that
was never validly measured at all (E6). None of the surveyed projects report a
negative result, which is not evidence that they never had one.
