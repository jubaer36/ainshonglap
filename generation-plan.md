# Generation plan — step 12

Answer generation: turn retrieved sections into a cited answer, or refuse.
This began as the option survey; the `Status` section records what is now built.
The focused path still supplies five sections, while `query-routing-plan.md`
defines the planned separate comprehensive-discovery path.

---

## What the brief requires

From `entry-assessment.md`, core requirements this step owns:

- LLM integration (self-hosted, vLLM, or free/paid API)
- section-level citations **and relevant source excerpts**
- proper handling of **ambiguous** and **unsupported** questions
- a clear disclaimer: legal information, not legal advice

Four requirements, and three of them are about *not answering wrongly* rather
than about answering. That ordering is the whole shape of this step.

---

## The constraint our own numbers impose

This is the part that should drive every choice below, so it goes first.

Served retrieval is **R@1 41.8%, R@5 59.0%, R@20 71.4%** (E7). If the generator
gets the top 5 sections, then **41% of the time the correct section is not in
the prompt at all** — but five *plausible, topically adjacent, fluently drafted
statutory sections* are.

That is not the benign "no evidence" case. It is the adversarial one.

[GRAB-RAG](https://arxiv.org/html/2608.22228) tested five abstention policies
across three small models (3.8–8B) under four context conditions: supportive,
degraded, missing, and **misleading** (fluent passages supporting a wrong
answer). The finding:

> models under P1–P4 almost never answer, with rates at 1% or below [when
> evidence is absent], yet climb to between **13.6% and 74.3%** when the
> retrieved context includes a fluent passage supporting a wrong answer.

And when they did answer under misleading context, **59.6% echoed the planted
wrong entity verbatim**.

Read that against our retrieval profile. Wrong-but-adjacent statute sections are
*exactly* the misleading condition, and our pipeline produces them 41% of the
time by construction. A prompt that says "abstain if the context is
insufficient" is answering the wrong question: the context will not look
insufficient, it will look like five sections of the Penal Code.

Two caveats before this is over-weighted: GRAB-RAG used 3.8–8B models, and
Gemini 2.5 Flash may behave better; and their misleading passages were
adversarially constructed by entity swap, while ours are merely wrong. Neither
caveat changes the direction, only possibly the magnitude. **It should be
measured on our own data, not assumed in either direction.**

Corroboration from the legal domain: Stanford RegLab's
[preregistered evaluation](https://arxiv.org/abs/2405.20362) of Lexis+ AI and
Westlaw AI-Assisted Research found they

> each hallucinate between **17% and 33%** of the time

— that is *with* RAG, on proprietary curated corpora, at commercial scale,
against vendor claims of being "hallucination-free". The paper's framing is that
RAG reduces hallucination relative to general-purpose chatbots but does not
eliminate it. Retrieval is not a hallucination fix; it is a hallucination
*reduction*, and the residue is the product.

Our own E1 says the same thing locally: Qwen2.5-VL-3B with no retrieval
fabricated a quotation of CrPC s.154 and asserted the exact inverse of Evidence
Act s.25. Gemini 2.5 Flash answered 5/5 correctly.

---

## Decision A — which model

| option | for | against |
|---|---|---|
| **Groq Qwen 3.8 27B** | current default; strict JSON schema passed; direct probes returned in 0.95-1.31s | free-tier token limits; two ungrounded probes hallucinated section/Act details, so retrieval and gates are mandatory |
| **Gemini Flash-Lite** | native structured output; Gemini 2.5 Flash scored 5/5 in E1 and has the closest published Bangladesh-jurisdiction evidence | external dependency; current Flash-Lite version has not received the same local legal evaluation |
| **OpenRouter LFM / Nemotron Super** | schema-capable fallbacks; LFM is a small fast router | shared free-route latency and availability |
| **Local baseline models** | useful historical comparison | removed from the production generator registry; unconstrained output and weak E1 result |

**Current decision:** Groq `qwen/qwen3.8-27b` is the default behind the common
LLM interface. Gemini and the two measured schema-capable OpenRouter routes are
fallbacks. Schema compatibility is established, but legal quality is not: E11
showed that fast, valid JSON can still contain wrong statutory content. The
retrieved-section and verbatim-quote gates remain mandatory, and the shipping
Groq model needs the unfinished generation evaluation.

---

## Decision B — the shape of the answer

| option | description | evidence |
|---|---|---|
| **B1 free-form + inline cites** | one call, "answer and cite sections" | the default everywhere; no structural guarantee that a cite corresponds to anything |
| **B2 quote-first / extract-then-answer** | model first extracts verbatim spans from the retrieved sections, then answers using only those | [Anthropic's guidance](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/reduce-hallucinations) and the ["According to..." paper](https://arxiv.org/pdf/2305.13252); a fabricated verbatim quote must exist in text sitting in the context window, so it is **instantly checkable by string match** |
| **B3 structured JSON, per-claim citation** | schema where every claim carries a `section` + `quote`; unpopulated citation ⇒ reject before the user sees it | constrained decoding guarantees schema conformance — but [JSONSchemaBench](https://arxiv.org/pdf/2501.10868) and [related work](https://arxiv.org/html/2605.02363) find **structural compliance clusters above .95 while value accuracy varies far more**. Syntax is free; truth is not |
| **B4 two-call: extract, then write** | separate models/calls for evidence selection and prose | cleanest separation; 2× latency and cost |

**Recommendation: B2 + B3 together** — structured output whose schema *forces*
a verbatim quote field per claim. B3 alone buys well-formedness and nothing
else; B2 is what makes the output mechanically verifiable. Combined, every
claim ships with a span that either appears in the cited section's text or does
not, and that check needs no model.

This also satisfies the brief's "relevant source excerpts" requirement as a
by-product rather than as a separate feature.

---

## Decision C — citation enforcement

Ordered weakest to strongest. These stack; they are not alternatives.

| level | mechanism | cost | catches |
|---|---|---|---|
| C1 | prompt instruction ("cite every claim") | free | nothing reliably — this is what the vendors in the Stanford study were doing |
| C2 | **key validity**: cited section id exists in the retrieved set | trivial | citing a section that was never retrieved, i.e. parametric leakage |
| C3 | **quote fidelity**: the verbatim span string-matches the cited section's parsed text | trivial, deterministic | fabricated quotations — the exact E1 failure |
| C4 | **NLI entailment**: claim ⊨ cited section, via DeBERTa-style NLI | one small model | claims that cite a real section, quote it correctly, and still do not follow from it |
| C5 | **hard gate**: withhold any answer containing uncited text | free once C2–C4 exist | everything above, at the cost of refusing more |

C3 is unusually cheap for us specifically: parsing is deterministic and
verified, so the cited section's text is known exactly. A quote either occurs in
it or it does not. Most projects cannot do this because their chunk text is a
lossy PDF extraction.

C5 is what [`toddlar00/rag-pipeline`](similar-projects.md) does — it withholds
answers containing uncited text, as a gate rather than an instruction. [ALCE](https://www.emergentmind.com/papers/2305.14627)
is the reason to take this seriously: it measures citation precision/recall with
an NLI model and finds that **even the best systems lack complete citation
support ~50% of the time** on long-form answers.

**Recommendation:** C2 + C3 immediately (deterministic, near-free, and they
close the fabricated-quotation hole E1 found). C4 as a measured experiment. C5
last, because its cost is refusals and that trade has to be priced against real
numbers, not guessed.

---

## Decision D — abstention and ambiguity

The brief names two distinct cases and they need different handling.

### D-unsupported ("this is not in the three acts")

| option | mechanism | evidence |
|---|---|---|
| D1 | prompt-only: "say you don't know if unsupported" | **GRAB-RAG P1: reduces misleading-context answering only to 41.6%.** Chain-of-thought (P2) gives 37.8% — no real help. "Sufficiency checks aren't factuality checks" |
| D2 | **score threshold** on the reranker logit | free; we already have calibrated-ish scores (5.83 for a correct top hit vs 2.87 for the neighbour in the theft example). Needs a threshold fitted on the 608 set |
| D3 | **LLM relevance judge** over the candidates before generation | a second opinion that is not the generator's own |
| D4 | generator-side conflict check vs closed-book answer | GRAB-RAG P3: misleading-context answering down to 13.3% — **but discards 49.8% of answerable questions.** The coverage/safety trade is brutal and explicit |
| D5 | NLI verifier | GRAB-RAG P4: recovers more utility than P3, fails when parametric memory and the misleading passage agree |

GRAB-RAG's own conclusion is that **no single policy solves this** and that
external signals beyond the model's own judgement are necessary.

**Recommendation:** D2 + C3 as the spine — a retrieval-score gate in front, a
quote-fidelity gate behind — with D1 as the cooperative layer rather than the
mechanism. D3/D4/D5 are experiments, and D4's 49.8% utility cost is the warning
label on all of them.

The threshold in D2 is fittable and reportable: we have 608 questions with
known-correct sections, so the distribution of reranker scores for hits vs
misses is directly measurable. That makes abstention a **tuned parameter with a
published operating point**, not a vibe.

### D-ambiguous ("which theft? which stage of the proceeding?")

Distinct problem, cheaper answer: detect multiple plausible readings and ask a
clarifying question, or answer both readings and cite both sections. Our
retrieval already surfaces the candidate readings — CrPC s.154 vs s.155
(cognizable vs non-cognizable) is an ambiguity the retrieved set itself exposes.

Worth noting that `AI_Bangla_FAQ_Bot_RAG` and Mina both do **Act-level routing
before section search**; Mina "filters by Act metadata before Section-level
search". That is the same idea as our deferred Act-filtering, and it doubles as
an ambiguity signal — a question that routes to two Acts is ambiguous.

---

## Decision E — how generation gets measured

We measure retrieval well and generation not at all. That asymmetry is the
biggest hole in the project right now.

| option | what it gives | caveat |
|---|---|---|
| **RAGAS** | faithfulness (claim extraction + NLI entailment against context), answer relevancy | criticised as strict on entailment but blind to pragmatics — it marks "Sam Altman founded OpenAI" faithful because he is mentioned, missing the implied exclusivity |
| **DeepEval** | same family, more sensitive to misleading emphasis | LLM-as-judge underneath, so it inherits judge bias |
| **[LRAGE](https://arxiv.org/abs/2504.01840)** | legal-domain RAG evaluation across retriever / reranker / LLM / metric, with **instance-level rubrics** | built for KBL / LegalBench / LawBench; our corpus would need wiring in |
| **ALCE-style citation metrics** | citation precision + recall via NLI | the right shape for the citation requirement specifically |
| **hand-built on our 608** | direct control; same set every other experiment uses | costs the most to build |

**Recommendation:** hand-built, small, on the set we already have — plus
ALCE-style citation precision/recall, which C2/C3 make nearly free to compute.
The three things worth scoring:

1. **citation validity** — C2 + C3 pass rate (deterministic, no judge)
2. **faithfulness** — does the answer follow from the cited sections (judge, second model)
3. **abstention quality** — measured as its own metric, both directions

On (3) we already have material: the 608 set contains ~2% unanswerable, **and 9
of those are mislabelled in the dataset itself** — which we found while
verifying the eval set was fair. That is a small, genuine, hand-checked refusal
set. `KnowYourRightsAI` keeps 11 questions specifically designed to be refused;
ours is comparable in size and came from an independent source.

Abstention needs both error directions reported or it is meaningless: a system
that refuses everything scores perfectly on hallucination.

---

## Delivery path

| # | work | status |
|---|---|---|
| 1 | common hosted-LLM interface; Groq Qwen default | done |
| 2 | structured claim + section id + verbatim quote | done |
| 3 | C2 + C3 deterministic citation gates | done |
| 4 | fit and publish the D2 score-threshold curve | done; no default adopted |
| 5 | generation eval: validity, faithfulness, abstention both directions | current gap; E8 incomplete |
| 6 | disclaimer in the server response, not the prompt | done |
| 7 | comprehensive multi-section discovery separate from focused generation | planned in `query-routing-plan.md` |

Item 6 is deliberate: a disclaimer emitted by the LLM is a disclaimer the LLM
can omit. It belongs in the API response schema.

---

## Open questions

- **Does Groq Qwen collapse under our misleading context?** GRAB-RAG measured
  smaller models. Directly testable: feed the shipping model the 41% of
  questions where the gold section is absent from the top 5 and count answers.
  This is the single most informative experiment available and should run before
  any prompt tuning.
- **Where does the D2 threshold sit**, and what does it cost in refused-but-answerable?
- **Does C4 (NLI) catch anything C3 (string match) misses**, on a corpus where
  quotes are exactly checkable? Possibly not — worth knowing before building it.
- **Should the generator see 5 sections or 20?** More context raises recall of
  the right section from 59.0% to 71.4% but multiplies the misleading-passage
  surface. Untested.
- **Bangla.** Out of scope for now, but Mina and LegalEase-BD both split or
  route by language rather than relying on one multilingual index.

---

## Sources

- [Hallucination-Free? Assessing the Reliability of Leading AI Legal Research Tools](https://arxiv.org/abs/2405.20362) — Stanford RegLab; 17–33% hallucination for RAG-based legal tools
- [Prompt-Based Abstention Fails Under Misleading Context (GRAB-RAG)](https://arxiv.org/html/2608.22228) — the abstention-collapse result
- [Enabling Large Language Models to Generate Text with Citations (ALCE)](https://www.emergentmind.com/papers/2305.14627) — citation precision/recall metrics
- [Mina: A Multilingual LLM-Powered Legal Assistant Agent for Bangladesh](https://arxiv.org/html/2511.08605v1) — Bangladesh Bar Council scores, Act-before-Section routing
- [LRAGE: Legal Retrieval Augmented Generation Evaluation Tool](https://arxiv.org/abs/2504.01840) — legal-domain RAG evaluation with instance-level rubrics
- ["According to..." Prompting Language Models Improves Quoting from Pre-Training Data](https://arxiv.org/pdf/2305.13252)
- [JSONSchemaBench](https://arxiv.org/pdf/2501.10868) / [When Correct Isn't Usable](https://arxiv.org/pdf/2605.02363) — structured output: schema conformance ≠ value accuracy
- [RefusalBench](https://arxiv.org/html/2510.10390v1), [AbstentionBench](https://arxiv.org/html/2506.09038) — refusal benchmarks
- [Reduce hallucinations — Claude docs](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/reduce-hallucinations) — quote-first workflow
- `similar-projects.md` — `toddlar00/rag-pipeline` uncited-text gate; `KnowYourRightsAI` refusal questions

---

## Status

Built and serving (`backend/app/rag/`, `backend/API.md`):

| decision | shipped |
|---|---|
| A model | pluggable hosted backend; Groq Qwen is the default and passed the strict-schema probe. OpenRouter fallbacks pin routing to providers that enforce schemas |
| B answer shape | JSON schema, one claim per assertion, each with section number and verbatim quote |
| C citations | **C2 + C3 live.** C2 resolves the act too, not just the number — all three acts have a s.25. Failing claims dropped; none surviving becomes a refusal. C4 (NLI) not built |
| D abstention | prompt + gates. D2 threshold now **fitted** (E10): AUC 0.727, weak separation. Still no default — refusing 70% of failed retrievals costs 35% of answerable questions. `min_score` is opt-in with a published curve; -3.00 is the least-bad point if one is wanted |
| E evaluation | **not built.** No faithfulness, citation-precision or abstention numbers exist yet |

**E8 is unfinished**, at 21 of 40 calls: on the 16 valid responses where the gold
section was absent from the context, 5 answered rather than refused. At n=16 the
interval is roughly [15%, 54%] — it rules out "refuses reliably" and little
else. Treat as unmeasured.

That matters for reading the rest of this document: the gates were built on
GRAB-RAG's finding, not on our own measurement. They are cheap and
deterministic, so building them first was defensible — but "the generator
over-answers on our corpus" remains imported, not established.

---

## Summary

**The constraint.** R@5 is 59.0%, so **41% of the time the correct section is
absent from the 5 sections handed to the generator** — replaced by plausible,
adjacent, fluently drafted statute. That is the *misleading context* condition,
not the empty one. GRAB-RAG: models answer ≤1% when evidence is absent but
**13.6–74.3% when a fluent wrong passage is present**, and 59.6% of those
answers echo the wrong entity verbatim. Stanford RegLab: commercial legal RAG
tools still hallucinate **17–33%** of the time. Retrieval reduces hallucination;
it does not remove it, and the residue is the product.

**The five decisions.**

| | choice | the argument |
|---|---|---|
| A model | Groq Qwen default; Gemini and schema-capable OpenRouter fallbacks | Groq passed strict output and latency probes, but E11 also demonstrated ungrounded legal hallucinations; the common interface and citation gates are therefore non-optional |
| B answer shape | structured JSON **+** verbatim quote per claim | schema conformance is ~.95 across models while value accuracy varies widely — JSON alone buys well-formedness, the quote field is what makes output checkable |
| C citations | C2 key validity + C3 quote string-match, deterministic | unusually cheap **for us**: parsing is verified, so the cited section's exact text is known and a quote either occurs in it or does not. Closes the E1 hole where Qwen fabricated a quotation of CrPC s.154 |
| D abstention | reranker score threshold + quote gate; prompt as cooperative layer only | prompt-only abstention reaches 41.6% under misleading context; chain-of-thought 37.8%. The aggressive option (P3) hits 13.3% but **discards 49.8% of answerable questions** |
| E evaluation | hand-built on the 608 + ALCE-style citation precision/recall | citation validity is judge-free via C2/C3; abstention reported in **both** directions, or a system that refuses everything scores perfectly |

**Two non-obvious points.**

- The disclaimer belongs in the response schema, not the prompt. A disclaimer
  the model emits is one it can forget to emit.
- Our 608-question set makes abstention a **fitted parameter with a published
  operating point** rather than a prompt instruction — the score distribution
  for hits vs misses is directly measurable.

**First experiment, before any prompt tuning:** feed Groq Qwen the 41% of questions
where the gold section is absent from the top 5 and count how often it answers
anyway. GRAB-RAG measured 3.8–8B models on adversarial entity swaps; whether the
collapse reproduces here, at this model size, on merely-wrong rather than
adversarial passages, is unknown and decides how much of section D gets built.

**Unsettled:** 5 sections in the prompt or 20? 20 raises gold-present from 59.0%
to 71.4% but multiplies the misleading-passage surface. Untested.
