# Retrieval: baseline and measured improvements

BGE-M3 dense retrieval, then cross-encoder reranking. Numbers, not impressions.

**Current served numbers** (dual-granularity index + reranking, `experiments.md`
E7): **R@1 41.8%, R@5 59.0%, R@20 71.4%, MRR 0.498, Act accuracy 87.5%.** The
baseline below is the section-only dense starting point those improved on; it is
kept because the deltas are the argument, not the endpoint.

First measured numbers for the retrieval stage, and what they say to do next.
Everything here comes from `backend/scripts/eval_retrieval.py`, scored against
an external gold standard rather than judged by eye.

```bash
conda activate ainshonglap
cd backend
python scripts/build_index.py            # chunks -> Laws/main/index/
python scripts/eval_retrieval.py         # measure against gold sections
python scripts/eval_retrieval.py --split single_hop --show-failures 10
```

## Setup

| | |
|---|---|
| embedder | BGE-M3 (`BAAI/bge-m3`), local, 1024-dim, CLS pooling, L2-normalised |
| index | FAISS `IndexFlatIP` — exhaustive, so top-k is exact |
| corpus | 1,321 chunks (CrPC, Penal Code, Evidence Act) |
| eval set | 608 questions from `bangladesh-legal-qa-dataset`, each labelled with its gold Act + section |
| retrieval | dense, then `bge-reranker-v2-m3` over the top 20 |

A hit requires **both** the right Act and the right section number. Getting the
section number right in the wrong statute counts as a miss, because in this
product that is worse than returning nothing.

`sentence-transformers` 6.x cannot load BGE-M3 (it looks for an `AutoProcessor`
and raises), so `scripts/embed.py` uses `transformers` directly. BGE-M3's dense
vector is the CLS token normalised, so the wrapper was not buying anything.

## Results

Dense-only, by split. (Overall figures moved slightly to 32.7% / 68.4% / 82.2%
after E2 cut the hierarchy prefix back — within noise, and the per-split shape
is unchanged.)

| split | n | Recall@1 | @5 | @20 | MRR | Act accuracy |
|---|---|---|---|---|---|---|
| **overall** | 608 | **31.2%** | 53.5% | **67.8%** | 0.414 | **80.8%** |
| single_hop | 222 | 33.3% | 58.1% | 74.3% | 0.449 | 82.9% |
| advanced_selection | 248 | 23.8% | 50.0% | 64.9% | 0.352 | 79.4% |
| bar_exam_style | 138 | 41.3% | 52.2% | 62.3% | 0.470 | 79.7% |

## Is the test fair?

Checked before drawing conclusions, because a low score can mean bad retrieval
*or* an impossible test:

- **10 of 608 (2%)** have a gold `(Act, section)` that is absent from our corpus.
- Of those, **9 are mislabelled in the dataset** — Penal Code sections tagged as
  CrPC (s.456 house-breaking, s.325 grievous hurt, s.120B conspiracy).
- **1** is genuinely absent.

So 98% are answerable. The weak scores are real, not an artifact.

## Reading the numbers

**The 31% → 68% gap is a ranking problem, not a retrieval problem.** Two thirds
of the time the correct section *is* being pulled back; it simply is not on top.
That gap is what a reranker exists for, and it is larger than anything a better
embedder would recover.

**Act accuracy 80.8% means ~1 query in 5 returns the wrong statute at rank 1.**
This is Document-Level Retrieval Mismatch — the exact failure the hierarchy
prefix was added to prevent — now a measured quantity rather than a worry. It
is also the most damaging error this system can make: confidently citing a real
section number of the wrong Act.

**advanced_selection is hardest** (23.8% @1), consistent with its design: those
questions require choosing among several plausible sections, so first-rank
precision suffers most.

**Similarity margins are thin.** For "Can the police arrest someone without a
warrant?" the top five score 0.7385, 0.7179, 0.6934, 0.6689, 0.6556 — the right
answer (CrPC s.54) wins by 0.02. Small changes in phrasing flip that ordering,
which is another way of saying the same thing: ranking, not recall.

## Three separable problems

| problem | size | what addresses it |
|---|---|---|
| retrieved but badly ranked | 37 pts (31 → 68) | reranking |
| never retrieved at all | 32 pts (68 → 100) | better embedder, query rewriting |
| wrong statute at rank 1 | 19% | structural signals, metadata filtering |

Changing the embedder only touches the middle row, which is why it is **not**
the obvious first move despite being the most obvious-sounding one.

## Update: reranking measured (E4)

| | R@1 | R@5 | R@20 | MRR | Act% |
|---|---|---|---|---|---|
| dense only | 32.7% | 54.1% | 68.4% | 0.425 | 82.2% |
| + `bge-reranker-v2-m3` | **41.9%** | **57.7%** | 68.4% | **0.493** | **87.2%** |

**+9.2pp on R@1 (~4.8 standard errors)** — the first significant result in this
project, and it confirms the diagnosis below: the bottleneck was ranking, not
recall. Act accuracy rose 4.9pp as a side effect. R@20 is unchanged by
construction, which is the sanity check.

Remaining after E4: **26.5pp** of ranking headroom inside the retrieved set,
**31.6pp** never retrieved at all, **12.8%** wrong statute at rank 1.

## Options, by expected value

1. ~~**Cross-encoder reranker**~~ — **done, adopted.** +9.2pp R@1. See E4 above.
2. **Query rewriting / HyDE** — the failures show a register mismatch: questions
   are narrative ("I stole a bag of grain from a neighbour's building") while
   the statute is drafted ("Whoever commits theft in any building..."). Only
   this attacks the 32% never-retrieved, which no reranker can reach.
3. **Structure-aware reranking** — targets the 19% wrong-Act rate using the
   cross-references already in our metadata; 28% of chunks cite another section.
4. **Metadata filtering by Act** — would cut wrong-Act errors outright, but
   needs a classifier and can fail closed.
5. **Better embedder** — raises the ceiling. Worth doing *after* the above, so
   the spend goes on the part that actually binds.
6. **Hybrid BM25** — statute-specific evidence is weak-to-negative; test late,
   do not assume.

## What was done first, and why it mattered

Before adding any machinery, the two assumptions already baked into chunking
were A/B'd — the hierarchy prefix and the 512-token budget. Both came from
published results on other corpora and neither had been measured here.

**Both were flat** (E2: 3.0pp spread; E3: 0.7pp — against a ±3.7pp interval).
The prefix was cut back, saving 38 tokens a chunk for no measurable loss.

That ordering paid off twice over: it removed a 35% token overhead that was
doing nothing, and it ruled out chunk formatting as a lever *before* a reranker
was stacked on top of it. Had the reranker gone first, its +9.2pp would have
been measured on a base that was 35% more expensive than it needed to be, and
the prefix would probably never have been questioned.

The general lesson for the rest of this project: validating a foundation is
cheaper than building on it, and has a real chance of overturning a decision
rather than confirming one. Two of four experiments so far did exactly that.
