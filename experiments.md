# Experiment log

Each entry: objective, hypothesis, configuration, result, decision. Negative
results are kept — two of the three so far overturned an assumption, which is
more useful than confirming one.

Harness: `backend/scripts/experiment_retrieval.py`. All runs score against the
same 608 gold questions from `bangladesh-legal-qa-dataset` (external, scraped
independently of our PDFs). A hit requires the right **Act and** section.

**Noise floor.** At n=608 and p≈0.32, one standard error is ±1.9pp and the 95%
confidence interval is **±3.7pp**. Any gap smaller than that is not a result.
This is stated up front because two of the experiments below produce spreads
smaller than it, and reading those as wins would be self-deception.

---

## E1 — LLM-only baseline (no retrieval)

**Objective.** Establish the floor before building anything.

**Config.** Qwen2.5-VL-3B-Instruct locally on GPU; Gemini 2.5 Flash via API.
Same 5 Bangladesh criminal-law questions, same system prompt, no retrieval.

**Result.** Qwen invented section content or citations on **3 of 5** — including
a fabricated quotation of CrPC s.154 and the claim that a confession to police
*is* admissible, which is the exact inverse of Evidence Act s.25. Gemini
answered all 5 correctly with accurate citations.

**Decision.** The generator is not the weak link; retrieval is. Do not spend
effort on generator selection until retrieval is measured. Carried into
`retrieval-plan.md` as the reason to prioritise the embedder and ranking.

---

## E2 — Does the hierarchy prefix earn its tokens?

**Objective.** The prefix costs ~38 tokens on every chunk (35% size increase).
It was adopted from published results on other corpora and never tested here.

**Hypothesis.** Prepending `Act > Part > Chapter > Section: title` improves
retrieval, and especially Act accuracy, because a bare "Whoever commits..."
carries nothing identifying which of three overlapping statutes it came from.
Anthropic's Contextual Retrieval reports 49% fewer retrieval failures; the
statute literature names Document-Level Retrieval Mismatch as the failure mode
this should fix.

**Config.** BGE-M3, FAISS `IndexFlatIP`, dense-only, 512-token budget held
constant. Five prefix variants, corpus re-embedded for each.

**Result.**

| variant | med tok | R@1 | R@5 | R@20 | MRR | Act% |
|---|---|---|---|---|---|---|
| none | 110 | 31.4% | 53.1% | 67.3% | 0.410 | 81.4% |
| act_only | 116 | 30.3% | 52.6% | 66.9% | 0.402 | 82.6% |
| title_only | 124 | 31.1% | 52.0% | 66.6% | 0.404 | 79.6% |
| act_section | 130 | **33.2%** | 53.6% | 67.8% | **0.424** | **82.7%** |
| full (in use) | 148 | 31.2% | 53.5% | 67.8% | 0.414 | 80.6% |

Total spread **3.0pp** against a ±3.7pp confidence interval. **No variant is
distinguishable from any other**, including no-prefix-at-all versus the full
hierarchy path.

**The DRM hypothesis specifically failed.** `act_only` exists precisely to stop
wrong-Act retrieval and scores 82.6% Act accuracy against 81.4% for no prefix —
1.2pp, well inside noise.

**Why it probably did not replicate.** Two candidates, not yet separable:
statutory text may already be self-identifying (it names its own concepts
densely), and Anthropic's 49% came from a different setup — LLM-generated
per-chunk summaries *plus* BM25 *plus* reranking — not formatted metadata
bolted onto a dense-only pipeline. Importing the number without the setup was
the error.

**Decision.** Keep `act_section` (nominally best on every metric, 18 tokens
cheaper than `full`), while recording that its lead is **not significant**. The
honest summary is that the prefix is close to free to remove. Effort moves to
ranking, which this experiment shows is untouched by chunk formatting.

---

## E3 — Is 512 the right chunk budget?

**Objective.** 512 was chosen as a round number, not from evidence.

**Hypothesis.** Smaller chunks sharpen retrieval precision (less dilution per
vector); larger ones preserve more context. There should be a visible optimum.

**Config.** As E2, prefix held at `full`, budget varied.

**Result.**

| budget | chunks | med tok | R@1 | R@5 | R@20 | MRR | Act% |
|---|---|---|---|---|---|---|---|
| 256 | 1486 | 153 | 31.9% | 53.1% | 66.9% | 0.417 | 80.3% |
| 512 | 1321 | 148 | 31.2% | 53.5% | **67.8%** | 0.414 | 80.6% |
| 1024 | 1259 | 145 | 31.2% | 53.5% | 66.8% | 0.410 | 80.8% |

**0.7pp spread** — flatter than E2 and far inside noise.

**Why, and this one is explainable.** The budget only affects sections that
exceed it, and only **3–8% of sections do**. Median chunk size barely moves
(153 → 145 tokens across a 4× budget change) because the median section is
~130 tokens and is never split at any of these settings. The knob is nearly
disconnected from the corpus.

**Decision.** Keep 512. Not because it won, but because nothing distinguishes
the options and 512 leaves the fewest chunks over an embedder's typical window.
Stop tuning this parameter; it cannot move the outcome.

---

## E4 — Cross-encoder reranking

**Objective.** Close the ranking gap E2/E3 could not touch: R@1 32.7% against
R@20 68.4% means the right section is usually retrieved and badly ordered.

**Hypothesis.** A cross-encoder reads query and chunk *together* in one forward
pass rather than comparing two independently produced vectors, so it should fix
thin-margin ordering — the baseline's top five for "Can the police arrest
without a warrant?" were 0.7385 / 0.7179 / 0.6934 / 0.6689 / 0.6556, the right
answer winning by 0.02.

**Config.** `BAAI/bge-reranker-v2-m3` (560M, encoder-only XLM-R) rescoring the
dense top-20, on GPU. Chosen over Qwen3-Reranker-0.6B because it is reported to
win 50-67% on queries under 300 characters and **93% of our eval questions are
under 300** (median 198). ColBERT rejected: its advantage is throughput under
load, which we do not have, at the cost of a token-level index.

**Result.**

| | R@1 | R@5 | R@20 | MRR | Act% |
|---|---|---|---|---|---|
| dense only | 32.7% | 54.1% | 68.4% | 0.425 | 82.2% |
| + rerank | **41.9%** | **57.7%** | 68.4% | **0.493** | **87.2%** |
| delta | **+9.2** | +3.6 | +0.0 | +0.068 | **+4.9** |

**+9.2pp on R@1 — about 4.8 standard errors, far outside the ±3.7pp interval.**
The first result in this log that is real rather than noise. Act accuracy also
rose 4.9pp, so reranking helps with wrong-statute errors as a side effect, which
was not the stated hypothesis.

**Sanity check passed.** R@20 moved exactly +0.0. The candidate set is fixed at
20, so any movement there would have meant a bug in the harness rather than a
gain.

**Cost.** 1,052 ms/query (608 queries × 20 candidates in 640s). Acceptable for
evaluation; noticeable in a user-facing path, and worth batching across queries
rather than per-query before it reaches the API.

**Decision.** Adopt. It captured 9.2 of the 35.7pp available ranking headroom
(26%) — real, but the gap is not closed: R@1 41.9% against a 68.4% ceiling.

**Next.** Two separate things remain. Ranking still has ~26pp of headroom inside
the retrieved set, so structure-aware reranking and Act-filtering are still
worth testing. And R@20 itself is unmoved at 68.4% — 32% of gold sections are
never retrieved at all, which no reranker can reach. That is query rewriting's
problem: the questions are narrative ("I stole a bag of grain from a
neighbour's building") and the statute is drafted ("Whoever commits theft in
any building").

---

## What E2, E3 and E4 together establish

Both chunk-formatting knobs are flat — retrieval on this corpus is not limited
by how chunks are labelled or sized. Ranking is where the gains were, exactly as
the R@1-vs-R@20 gap predicted.

| problem | at baseline | after E4 | remaining |
|---|---|---|---|
| retrieved but badly ranked | 35.7pp | −9.2pp | **26.5pp** |
| never retrieved at all | 31.6pp | unchanged | **31.6pp** |
| wrong statute at rank 1 | 17.8% | −4.9pp | **12.8%** |

**Next.** Structure-aware reranking and Act-filtering for the remaining ranking
gap; query rewriting for the never-retrieved third, which is now the single
largest bucket and which no reranker can reach.

## Method note

The temptation in E2 was to report `act_section` as a 1.8pp win and move on.
Stating the ±3.7pp confidence interval *before* looking at results is what makes
that not an option. Two of three experiments here produced no signal, and
recording them as such is the point — a log of only positive results would be
evidence that the log is wrong, not that the system is good.
