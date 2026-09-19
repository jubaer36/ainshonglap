# Experiment log

Each entry: objective, hypothesis, configuration, result, decision. Negative
results are kept — two of the three so far overturned an assumption, which is
more useful than confirming one.

Harness: `backend/scripts/experiments/experiment_retrieval.py`. All runs score against the
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

## E5 — Retrieval granularity (parent-child / small-to-big)

**Objective.** E3 varied the split *budget* and found it flat, but that only
touches the 3-8% of sections large enough to split. It never tested the
retrieval **unit**. Parent-child retrieval embeds finer units and returns the
coarser parent; reported to improve accuracy 15-30% on queries needing broad
context, at the cost of added distractors.

**Hypothesis.** One vector per section blurs a long provision into a single
average. Embedding each subsection separately gives sharper vectors, and because
our eval scores at *section* level, a subsection hit still counts — so the
finer unit costs nothing in how it is judged.

**Config.** BGE-M3, three corpora built from the same parsed JSON. Metadata
always names the parent section, so scoring is identical across variants.
Retrieval over-fetches 60 vectors then dedupes to 20 distinct sections, since
several vectors can collapse to one section.

**Result.**

| granularity | vectors | R@1 | R@5 | R@20 | MRR | Act% |
|---|---|---|---|---|---|---|
| section (was in use) | 1,247 | 32.1% | 52.8% | 66.6% | 0.414 | 81.7% |
| subsection only | 1,879 | 32.7% | 54.9% | 65.6% | 0.421 | 85.2% |
| **both** | 2,246 | **35.5%** | **57.4%** | **70.4%** | **0.452** | **85.2%** |

**The important number is R@20, not R@1.** E4 established that reranking cannot
exceed R@20 — it only reorders what was already found. Indexing both
granularities lifts that ceiling **66.6% → 70.4%**, which is the one thing a
reranker cannot do for itself. R@1 +3.4pp is just under the ±3.7pp threshold on
its own, but four metrics move together and in the same direction, which is
more persuasive than any single one.

**Subsection-only is not the answer.** It sharpens Act accuracy (+3.5pp) and
R@5, but *drops* R@20 to 65.6% — the finest unit loses whole-section context
some questions need. Keeping both granularities is what wins; this is the
"adds distractors" trade-off resolving in our favour because the dedupe step
collapses redundant hits before scoring.

**Caveat.** The section baseline here reads 32.1% against 32.7% in the live
index, because this harness rebuilds chunks from the parsed JSON without the
oversized-section splitting. The comparison between variants is internally
consistent; the small offset is not a result.

**Decision.** Adopt dual-granularity indexing. Cost is 2,246 vectors against
1,247 — irrelevant at this scale.

---

## E6 — Alternative rerankers (INCONCLUSIVE — harness, not models)

**Objective.** Published benchmarks disagree about which reranker is best, and
none cover legal retrieval, so measure the alternatives here.

**Config.** Same dense top-20, same 608 questions. `bge-reranker-v2-m3` re-run
as a control.

**Result.**

| model | R@1 delta | verdict |
|---|---|---|
| `bge-reranker-v2-m3` (control) | **+9.2** | reproduces E4 exactly |
| `mxbai-rerank-base-v2` | −28.0 | **not a valid measurement** |
| `Qwen3-Reranker-0.6B` | −29.8 | **not a valid measurement** |
| `jina-reranker-v2` | crashed | needs `trust_remote_code`, prompts interactively |

**Both negatives are a harness bug, and the cause is confirmed.**
`Qwen3-Reranker-0.6B`'s config declares `"architectures": ["Qwen3ForCausalLM"]`
— it is a causal LM. Loading it through `AutoModelForSequenceClassification`
silently attaches a **freshly randomly-initialised** classification head, so
both models were being ranked by random weights. A −29pp delta is what that
looks like.

Three harness bugs were found before the cause was located: a missing pad
token, a decoder/encoder dispatch keyed on the wrong signal, and reading
`logits[:,0]` (the *irrelevant* class) on a binary head.

**What the control bought.** Re-running BGE alongside is what made this
diagnosable. "mxbai is bad" and "my scorer is inverted" produce identical
evidence; only an unchanged control separates them. It reproduced +9.2 / +3.6 /
+0.0 / +0.068 / +4.9 exactly.

**Decision.** `bge-reranker-v2-m3` stays — not because the alternatives lost,
but because it is the only one measured correctly. Recorded as **unmeasured,
not disproven**. Testing them properly needs each model's own scoring protocol
(Qwen3: chat template plus yes/no token logits from the LM head), which is a
separate piece of work and should not be reported as a model comparison until
it is done.

---

## E7 — E4 and E5 together, in the pipeline that actually serves

**Objective.** E4 (+9.2pp R@1) and E5 (+3.8pp R@20) were measured
independently, each against a section-only dense baseline. Neither had ever
been measured with the other on, and neither ran in anything servable: E4 lived
only in `experiments/rerank.py`, and E5's code was in `build_index.py` but the
index had never been rebuilt with it. The logged 41.9% described a pipeline
nothing could execute.

**Hypothesis.** They should stack. They fix different failures — E5 raises the
candidate ceiling (never-retrieved sections), E4 reorders inside it (retrieved
but badly ranked) — so the gains should be close to additive.

**Config.** `app/rag/` extracted so the API and the eval scripts run the same
retrieve() rather than two copies. Index rebuilt dual-granularity: 2,320
vectors (1,321 section chunks + 999 subsection children). Same 608 questions,
same top-20 candidate set, `bge-reranker-v2-m3`.

**Result.**

| index | rerank | R@1 | R@5 | R@20 | MRR | Act% |
|---|---|---|---|---|---|---|
| section only | off | 32.7% | 54.1% | 68.4% | 0.425 | 82.2% |
| section only | on (E4) | **41.9%** | 57.7% | 68.4% | 0.493 | 87.2% |
| dual (E5) | off | 35.7% | 59.5% | 71.4% | 0.461 | 85.2% |
| dual (E5) | on (E4+E5) | **41.8%** | 59.0% | 71.4% | 0.498 | 87.5% |

**They do not stack. The hypothesis is wrong.** Reranked R@1 is 41.8% with E5
against 41.9% without it — identical. E5's entire +3.0pp dense gain is absorbed
by the reranker: the sections dual granularity pulls into the top 20 are ones
the cross-encoder was already promoting from further down. Two fixes for
"retrieved but badly ranked" were being counted twice.

**What E5 does still buy, and it is not much.** R@20 68.4% → 71.4% and R@5
57.7% → 59.0% under reranking. Both are inside the ±3.7pp interval, so by this
log's own rule neither is a result on its own. MRR +0.005 and Act accuracy
+0.3pp are noise outright.

**Decision.** Keep dual granularity, on a weaker argument than E5 claimed:
it costs nothing at query time (index build only, 999 extra vectors), and R@20
is the ceiling step 9's query rewriting has to work inside — a wider candidate
set is worth having *before* that work, not after. But the honest headline is
that **the pipeline is at 41.8% R@1, which is where E4 alone already put it**,
and E5 did not move it.

**Method note.** This is the failure mode the rest of the log guards against in
the other direction. Every earlier entry asked "is this gain bigger than
noise?" — none asked "is this gain the *same* gain I already counted?" Two
experiments can each be correct against a shared baseline and still not add up.
The only way to know is to run them together, which is now the default because
both arms come from one code path.

---

## E8 — does the generator answer anyway when retrieval missed? (INCOMPLETE)

**Objective.** Served R@5 is 59.0%, so 41% of the time the correct section is
absent from the generator's context — replaced by five plausible, adjacent,
professionally drafted sections. Nothing in the prompt looks broken. Does the
generator notice, or answer from the wrong section?

**Why it matters.** The C2/C3 citation gates in `app/rag/verify.py` were built
on the assumption that it over-answers. That assumption is imported from
GRAB-RAG (arXiv 2608.22228), which measured 3.8-8B models answering 13.6-74.3%
of the time under misleading context against <=1% when evidence is simply
absent. Our model is larger and our wrong passages are merely wrong rather than
adversarially constructed, so the finding transfers in direction at best.

**Config.** `experiments/experiment_abstention.py`, measuring the shipping pipeline
(`answer()` — prompt and gates together, not the prompt alone). Two strata from
the 608-question set: gold_absent (retrieval missed; answering is the failure)
and gold_present (the control, which prices the abstention). Sequential, free
OpenRouter route.

**Result — partial, 21 of 40 planned calls.** The run was interrupted and the
script only persisted at the end, so only the log survives.

| stratum | n valid | answered | refused | errored |
|---|---|---|---|---|
| gold_absent | 16 | 5 | 11 | 4 |
| gold_present | — | — | — | not reached |

5/16 = 31% answered when the gold section was absent. At n=16 the 95% interval
is roughly [15%, 54%]: it rules out "refuses reliably" and essentially nothing
else. **This is not a result.** The control stratum, without which the number
cannot be read at all, was never run.

**What it did establish, incidentally.** The 4 errors were not refusals but
empty responses: reasoning tokens had consumed the whole output budget before
any JSON was emitted. Two harness defects fixed as a result — `max_tokens`
raised, and empty content now raises a diagnostic naming `finish_reason` and the
reasoning-token count instead of a bare JSON parse error. A fifth of calls were
being silently wasted.

**Decision.** Recorded as **unmeasured**, alongside E6. The gates ship
regardless: they are deterministic, cost nothing at request time, and C3 closes
E1's fabricated-quotation failure by construction. But whether the prompt alone
would have sufficed is an open question, and the honest statement is that this
project does not yet know how often its generator over-answers.

**To finish it:** ~19 calls for the control stratum, with incremental writes so
an interrupted run resumes instead of re-spending. Worth running against
deepseek-v4-flash and liquid/lfm-2.5-2.6b both — GRAB-RAG's collapse was
measured in the 2.6B size class, so a gap between them would be the published
finding reproducing on our corpus.

---

## E10 — fitting the D2 abstention threshold

**Objective.** `generation-plan.md` proposed refusing before generating when the
reranker score says nothing retrieved answers the question, and deliberately
shipped `min_score` with **no default** — inventing a constant was the thing to
avoid. This fits it.

**The anecdote that motivated it.** Asked "state some laws of the penal code",
top-1 reranker score was **-0.30**; asked "what is the punishment for theft",
**+6.15**. Opposite signs, apparently clean separation.

**Config.** `experiments/fit_threshold.py`. All 608 gold questions at k=5, recording
the top-1 reranker score and whether the gold section was retrieved. Those two
columns are a binary classifier; sweep the threshold, report both error
directions. No API calls.

**Result.** The distributions overlap heavily.

| | min | p10 | p25 | median | p75 | max |
|---|---|---|---|---|---|---|
| retrieval hit | -6.17 | -3.42 | -1.83 | **-0.06** | 2.90 | 8.40 |
| retrieval miss | -6.80 | -4.64 | -3.46 | **-1.92** | -0.83 | 5.81 |

**AUC 0.727** — real signal, weak separation. 43 of 249 misses outscore the
median hit. The two-example anecdote was two points from the tails.

The cost of using it (precision = share of answered questions whose gold section
was actually in context; 59.0% is R@5, i.e. answering everything):

| threshold | answers | precision | misses refused | **hits lost** |
|---|---|---|---|---|
| none | 100% | 59.0% | 0% | 0% |
| -3.00 | 78.9% | 65.2% | 32.9% | 12.8% |
| -1.48 | 56.2% | 73.4% | 63.5% | 30.1% |
| -1.10 | 50.5% | 75.9% | 70.3% | **35.1%** |
| 0.04 | 35.9% | 80.7% | 83.1% | **51.0%** |
| 2.32 | 18.6% | 92.0% | 96.4% | **71.0%** |

**Reading it honestly.** Refusing 70% of the cases where retrieval had already
failed costs **35% of the questions the system could have answered correctly**.
That is the same brutal shape GRAB-RAG reported for its strongest policy — 13.3%
misleading-context answering bought at the price of discarding 49.8% of
answerable questions. No threshold here is free, and none is obviously right.

**Decision.** **No default threshold.** `min_score` stays opt-in, now with a
published curve behind it rather than a missing number. The argument against
shipping one: the system already has two downstream defences that cost nothing —
the generator's own refusal, and the deterministic C2/C3 citation gates, which
drop claims that cite unretrieved sections or misquote them. A score gate is a
*third* filter applied earliest and most bluntly, where it cannot see what the
generator would have made of the context.

Recommended if one is wanted anyway: **-3.00**, which refuses a third of the
already-failed cases for 12.8% of hits. The aggressive end of this table is a
trap; 71% utility loss for 92% precision is not a legal assistant, it is a
system that mostly declines to work.

**What it does buy, for free.** The score is now a usable *confidence signal*
even without gating — worth surfacing to the reader ("weak match") rather than
spending as a refusal.

---

## What E2-E10 together establish

Both chunk-formatting knobs are flat — retrieval on this corpus is not limited
by how chunks are labelled or sized. Ranking is where the gains were, exactly as
the R@1-vs-R@20 gap predicted. And, per E7, there was only ever one ranking gain
to collect: E4 took it, and E5 could not take it again.

Where the 608 questions stand on the live pipeline (dual index + reranking):

| problem | at baseline | now | remaining |
|---|---|---|---|
| retrieved but badly ranked | 35.7pp | −6.1pp | **29.6pp** |
| never retrieved at all | 31.6pp | −3.0pp (E5) | **28.6pp** |
| wrong statute at rank 1 | 17.8% | −5.3pp | **12.5%** |

**Next.** Query rewriting for the never-retrieved bucket, which is now within
1pp of being the largest and which no reranker can reach.

Generation is now built and served, and is the part of the system with no
numbers attached to it: no faithfulness, citation-precision or abstention
measurement exists. E8 was meant to be the first and is unfinished. Structure-aware
reranking and Act-filtering remain candidates for the ranking gap, but E7 is a
warning about them: they attack the same bucket E4 already emptied, so each one
has to be measured *on top of* the reranker, never against dense alone.

## Method note

The temptation in E2 was to report `act_section` as a 1.8pp win and move on.
Stating the ±3.7pp confidence interval *before* looking at results is what makes
that not an option. Two of three experiments here produced no signal, and
recording them as such is the point — a log of only positive results would be
evidence that the log is wrong, not that the system is good.
