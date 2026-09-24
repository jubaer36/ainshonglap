# Experiment log

Every experiment run while building the system: what we wanted to know, what we
did, what we found, and what we decided. Failed and inconclusive experiments are
kept too, because several of them overturned an assumption or exposed a bug in
the testing itself. A shorter version is in
[experiments-summary.md](experiments-summary.md).

## How to read the numbers

Retrieval experiments use the same **608 questions**, each with a known correct
answer (the Act and section it comes from). A result counts as correct only if
both the Act and the section are right.

- **R@1:** how often the correct section is the first result.
- **R@5 / R@20:** how often it is somewhere in the top 5 / top 20.
- **MRR:** a single score for how high the correct section is ranked on average
  (1.0 means always first).
- **Right Act:** how often the first result comes from the correct Act.

**What counts as a real difference.** With 608 questions, results naturally
wobble by about 3.7 percentage points ("points"). A gap smaller than that could
be chance, so it is not treated as a result. This rule was set before looking at
any numbers, and it decided several experiments below.

---

## E1: Can a model answer without retrieval?

**Question.** Before building anything, how well does a model answer Bangladesh
criminal-law questions from its own knowledge?

**What we did.** Asked two models the same 5 questions with no law provided: a
small local model (Qwen2.5-VL-3B) and Gemini 2.5 Flash.

**What we found.** The small model invented law on 3 of 5 questions. It made up
a quotation of CrPC section 154, and said a confession to police *is*
admissible, which is the exact opposite of Evidence Act section 25. Gemini
answered all 5 correctly.

**Decision.** A model's own knowledge can't be trusted, so every answer must
come from retrieved text. Retrieval became the first thing to get right.

---

## E2: Does a heading on each chunk help search?

**Question.** Each chunk of law was given a heading like "Act > Part > Chapter >
Section: title" before being turned into a vector. Published work reported this
cuts search failures by about half. Does it help here?

**What we did.** Built the search index five times, each with a different
heading, and scored each on the 608 questions.

**What we found.**

| heading | R@1 | R@5 | R@20 | MRR | Right Act |
|---|---|---|---|---|---|
| none | 31.4% | 53.1% | 67.3% | 0.410 | 81.4% |
| Act only | 30.3% | 52.6% | 66.9% | 0.402 | 82.6% |
| title only | 31.1% | 52.0% | 66.6% | 0.404 | 79.6% |
| Act + section | **33.2%** | 53.6% | 67.8% | **0.424** | **82.7%** |
| full path | 31.2% | 53.5% | 67.8% | 0.414 | 80.6% |

All five are within 3 points of each other, so none is really better. Even the
"Act only" heading, which should have stopped wrong-Act results, made no real
difference. The published result probably came from a very different setup
(model-written summaries plus keyword search plus reranking), not a simple
heading.

**Decision.** Kept the short "Act + section" heading since it costs little, but
it is not a proven win. The real problem lay elsewhere: ranking.

---

## E3: Is 512 tokens the right chunk size?

**Question.** Long sections are split into chunks of at most 512 tokens. That
number was a guess. Does it matter?

**What we did.** Tried a maximum of 256, 512 and 1024 tokens.

**What we found.**

| max size | chunks | R@1 | R@5 | R@20 | Right Act |
|---|---|---|---|---|---|
| 256 | 1,486 | 31.9% | 53.1% | 66.9% | 80.3% |
| 512 | 1,321 | 31.2% | 53.5% | **67.8%** | 80.6% |
| 1024 | 1,259 | 31.2% | 53.5% | 66.8% | 80.8% |

Less than 1 point apart. The reason is simple: only 3 to 8% of sections are long
enough to be split at all, so the setting barely touches the data.

**Decision.** Kept 512 and stopped tuning it.

---

## E4: Does a reranker help?

**Question.** The right section was in the top 20 about 68% of the time but
first only 33% of the time. It was being found, then ranked too low. Can a
reranker fix the order?

**What we did.** A reranker (bge-reranker-v2-m3) reads the question and each of
the top 20 sections together and scores them again. This is more accurate than
comparing two separate vectors, which is how the first search works.

**What we found.**

| | R@1 | R@5 | R@20 | MRR | Right Act |
|---|---|---|---|---|---|
| search only | 32.7% | 54.1% | 68.4% | 0.425 | 82.2% |
| + reranker | **41.9%** | **57.7%** | 68.4% | **0.493** | **87.2%** |
| change | **+9.2** | +3.6 | 0.0 | +0.068 | **+4.9** |

A 9.2-point gain on R@1, well beyond the noise: the first real result. It also
cut wrong-Act results. R@20 didn't move, as expected, since the reranker only
reorders the same 20 sections. That was a useful check that the test was sound.

**Decision.** Adopted. It costs about 1 second per question on a GPU.

---

## E5: Should subsections be indexed too?

**Question.** Each section had one vector, which blurs a long section into one
average. Would giving each subsection its own vector find more?

**What we did.** Built three indexes: sections only, subsections only, and both.
A subsection hit counts as a hit on its section.

**What we found.**

| indexed | vectors | R@1 | R@5 | R@20 | Right Act |
|---|---|---|---|---|---|
| sections only | 1,247 | 32.1% | 52.8% | 66.6% | 81.7% |
| subsections only | 1,879 | 32.7% | 54.9% | 65.6% | 85.2% |
| **both** | 2,246 | **35.5%** | **57.4%** | **70.4%** | **85.2%** |

Indexing both raised R@20 from 66.6% to 70.4%. That matters because a reranker
can only reorder what search found, so R@20 is its ceiling. Subsections alone
were worse, because some questions need the whole section.

**Decision.** Adopted: index both.

---

## E6: Are other rerankers better?

**Question.** Published comparisons disagree on the best reranker, and none
cover legal text. How do others compare with bge-reranker?

**What we did.** Tested mxbai-rerank, Qwen3-Reranker and Jina's reranker on the
same questions, with bge-reranker run again as a control.

**What we found.** bge-reranker reproduced its E4 result exactly. mxbai and Qwen3
appeared to make results about 29 points *worse*, and Jina crashed. The drops
were a bug in our test, not the models: Qwen3 is a different kind of model, and
loading it the standard way silently gave it random weights. Running the control
alongside is what made the bug visible.

**Decision.** Kept bge-reranker. The others are recorded as **not measured**, not
as worse; testing them properly needs each model's own scoring method.

---

## E7: Do E4 and E5 add up?

**Question.** The reranker (E4) and indexing subsections (E5) were each measured
alone. Do their gains add up when both are on, in the pipeline the app serves?

**What we did.** Moved search into shared code used by both the app and the
tests, rebuilt the index with both granularities, and measured all four
combinations.

**What we found.**

| index | reranker | R@1 | R@5 | R@20 | MRR | Right Act |
|---|---|---|---|---|---|---|
| sections | off | 32.7% | 54.1% | 68.4% | 0.425 | 82.2% |
| sections | on | **41.9%** | 57.7% | 68.4% | 0.493 | 87.2% |
| both | off | 35.7% | 59.5% | 71.4% | 0.461 | 85.2% |
| both | on | **41.8%** | 59.0% | 71.4% | 0.498 | 87.5% |

They don't add up. With the reranker on, R@1 is 41.8% with or without E5. The
sections E5 pulled into the top 20 were ones the reranker was already moving up.

**Decision.** Kept both, since E5 costs nothing when answering and raises R@20
slightly. But the honest number is 41.8%, which the reranker alone already
reached. Lesson: two improvements can each look real and still be the same
improvement counted twice.

---

## E8: Does the model answer when retrieval missed? (unfinished)

**Question.** About 41% of the time the right section is not among the 5 given
to the model. Does the model notice, or answer from the wrong sections anyway?

**What we did.** Ran the full answering pipeline on questions where retrieval
missed, planning a second group where it succeeded as a comparison.

**What we found.** The run was cut short: 5 of 16 valid answers went ahead
anyway, and the comparison group never ran. With 16 questions this proves
nothing. It did expose a bug: some models spent their whole output budget
"thinking" and returned nothing. That was fixed.

**Decision.** Unfinished. The citation checks were kept regardless, since they
cost nothing and block invented quotes.

---

## E10: Should the app refuse when the search score is low?

**Question.** Could the app refuse before answering when the best search score
is low, instead of handing the model weak sections?

**What we did.** Recorded the top reranker score for all 608 questions, and
whether the right section had been found, then tested every possible cut-off.

**What we found.** The scores of good and bad searches overlap heavily. Any
useful cut-off throws away many answerable questions:

| refuse below | questions answered | right section in context | failed searches refused | **good answers lost** |
|---|---|---|---|---|
| no cut-off | 100% | 59.0% | 0% | 0% |
| -3.00 | 78.9% | 65.2% | 32.9% | 12.8% |
| -1.10 | 50.5% | 75.9% | 70.3% | **35.1%** |
| 2.32 | 18.6% | 92.0% | 96.4% | **71.0%** |

Refusing 70% of failed searches would cost 35% of the questions the app could
have answered correctly.

**Decision.** No cut-off by default. The model's own refusals and the citation
checks already catch bad answers more precisely. (-3.00 is the suggested value
if one is ever wanted.)

---

## E11: Groq as the answer provider

**Question.** The free OpenRouter models were slow and unreliable. Could Groq
give fast answers while keeping the strict answer format the checks rely on?

**What we did.** Sent two test questions to Qwen 3.8 27B on Groq: one requiring
the strict JSON format, one normal.

**What we found.** Answers came back in 0.95 to 1.31 seconds, in exactly the
required format. But without any law provided, it still got the law wrong (it
placed Evidence Act sections 25 to 27 in the CrPC).

**Decision.** Adopted Groq for speed and format, never for its own legal
knowledge. Retrieval and citation checks stay mandatory.

---

## E12: A catalog for exact lookups

**Question.** For "Chapter XVI" or "sections 378 to 382", similarity search is
the wrong tool: it returns what *reads like* Chapter XVI. How do we answer these
exactly?

**What we did.** Built a database (the catalog) of every Act, chapter, section,
subsection and cross-reference from the parsed law, and a checker that compares
it with the parsed files.

**What we found.** 3 Acts, 76 chapters, 1,247 sections, 1,873 subsections and 878
cross-references. All checks passed. It also confirmed some known parser gaps
(CrPC section 99A is missing; five section numbers appear twice) and found 60
references that point to another Act without naming it. A bug that read "2nd
Schedule" as a section was fixed along the way.

**Decision.** Adopted. It is a correctness fix for exact requests, not a search
change.

---

## E13: A planner model instead of patterns

**Question.** The old router used text patterns, so any phrasing nobody had
thought of fell through to search. "Show me the sections about documentary
evidence" came back as five loosely related guesses. Can a model decide what
kind of question it is instead?

**What we did.** A planner model reads each question and returns only a plan:
its type and where to look (never an answer, never database code). The code
then checks every section, chapter and Act it names. Tested on 41 written test
questions.

**What we found.** 42 of 42 question types right, and 25 of 27 lookups right on
the first run. The two misses were our code's fault (it kept "16" where the Act
says "XVI") and were fixed. It handled cases the patterns never could:

| question | plan |
|---|---|
| show me the sections about documentary evidence | Evidence Act, Chapter V |
| state the laws from the murder chapter | Penal Code, Chapter XVI |
| show me section 25 | asks which Act (all three have one) |
| show me Chapter XCIX of the Penal Code | refused: no such chapter |

The first version sent the full chapter list on every call, which hit Groq's
per-minute limit after three questions. It now sends the list only when a
chapter is described by subject. One gap also appeared: "not a legal question"
and "law we don't hold" were one type, so a question about the Dowry
Prohibition Act could be answered from memory. They were split into two types.

**Decision.** Adopted. An earlier test had rejected a planner model because a
small model (LFM 2.6B) labelled the same question differently between runs; a
stronger model changed the result.

---

## E14: Mercury, a faster kind of model?

**Question.** Inception's Mercury generates text differently (a "diffusion"
model) and is claimed to be much faster. Is it faster here?

**What we did.** Checked it follows the strict answer format, then timed it as
the planner.

**What we found.** It follows the format. But on the planner's short calls it
was slower than Groq:

| planner | typical time |
|---|---|
| Qwen on Groq | **0.51 s** |
| mercury-2 | 1.02 s |
| mercury-2.5 | 2.16 s |

Its speed advantage shows on long outputs; the planner's outputs are about 50
words, so fixed per-call overhead dominates.

**Decision.** Offered as a selectable model, not the default.

---

## E15: Voice input and memory (built, not measured)

**Question.** Record what these features do, without claiming numbers.

**Voice** (Deepgram nova-3). Speech becomes text that the user checks before
sending, never an automatic answer. The reason: if "379" is misheard as "279",
every later step succeeds on the wrong section and nothing can catch it. So
the metric that matters is **section-number accuracy**, not overall word
accuracy. Tested on synthetic audio only; accuracy on real speech is not
measured.

**Memory.** After each answer, a short summary of the conversation is written
and sent with the next question. The planner decides whether a question is a
follow-up and rewrites it to stand on its own:

| follow-up | rewritten to |
|---|---|
| "what if he had survived with only injuries?" | "What offence is committed if three men beat a man, causing him injuries but not death?" |
| "and the section right before it?" (after s.302) | "what does section 301 of the Penal Code say" |
| "what is the capital of France?" | not treated as a follow-up (correct) |

One bug found: the planner rewrote "the section before it" correctly but then
filled the section number from the original words, which had none, so it listed
the whole Act. Fixed.

**Also seen.** A two-issue scenario ("three men beat a man... he died") retrieved
only the *hurt* sections, never the homicide ones, and the model correctly
refused. This is a search limit, not an answering one.

---

## E16: Gemini as the planner

**Question.** The planner runs before everything else, so if it is slow,
everything is slow. On Groq it had become slow and unreliable. Is Gemini better?

**What we found.**

| planner | time | result |
|---|---|---|
| Qwen on Groq, rate-limited | 43.3 s | correct, but unusable |
| Qwen on Groq, retries used up | 15.0 s | failed |
| Gemini 3.5 Flash Lite | **1.3 to 1.8 s** | correct |

When the planner failed, the question fell back to plain search, so "who are
you" was sent to the law index and refused. From outside it looked like the app
couldn't understand a simple question; the step-by-step log showed the real
cause. Gemini also rejected one detail of the plan format (an empty option) that
other providers accept; fixing it made the format work everywhere.

**Decision.** Gemini became the planner, with Groq as backup. Later replaced (E18).

---

## E17: Why was an answerable question refused?

**Question.** "If I can't prove myself innocent in a court, am I guilty?" was
refused. Was the law missing, was search wrong, or was it the answering model?

**What we found.** One example, so it shows a cause, not a rate.

- **The law is there.** Evidence Act sections 101 to 106 set out who must prove
  what; section 101 even says "A must prove that B has committed the crime."
- **Search was right.** It returned sections 101, 102, 103, 105 and 106.
- **The answering model refused**, following its own instructions. No section
  literally says "failing to prove innocence doesn't make you guilty"; that
  follows from section 101, and the prompt forbids reasoning beyond the text.
  The prompt works for "what is the punishment for X", where the answer is
  written in the law, but not for questions like this one.

Two more findings: the reranker put the exception (section 105) above the rule
(section 101), and the one directly quotable answer (CrPC section 265H, on
acquittal) never reached the model because the rewritten question pulled search
towards the Evidence Act. Raising the number of sections given to the model from
5 to 8 didn't help; the extra slots went to unrelated sections.

**Decision.** Recorded as a known limit. Likely fixes: search with the original
and the rewritten question separately and merge the results; drop search words
that never appear in the law; loosen the prompt for this kind of question.

---

## E18: The planner gets its own key, and two Acts in one question

**Question.** "State five laws from the top five sections of the Evidence Act
and the Code of Criminal Procedure" was refused as "law we don't hold", although
it names two held Acts.

**What we found.** Two problems. The planner misjudged the question, and the plan
format could only hold **one** Act, so a two-Act request couldn't be expressed
at all. An earlier question had even returned one Act's section and silently
dropped the other.

**What we did.** Changed the plan to hold a list of Acts, and moved the planner
to gpt-oss-120b on its own Groq key, so answer traffic can't use up its limit.

**What we found after.** The same question plans correctly in 1.0 to 2.3 seconds
and returns sections from both Acts. Checked that ambiguous cases still ask
which Act.

**Decision.** Adopted. Lesson: E13's 42 of 42 was measured on an older planner,
and the first sign it no longer held came from a real user, not the tests.

---

## E19: Moving the laws to Supabase

**Question.** Move the law text and vectors from local files to Supabase
(Postgres with pgvector), without changing any result.

**What we did.** Loaded the existing data without recomputing anything, using
exact (not approximate) vector search, then compared both stores in every way.

**What we found.**

| check | result |
|---|---|
| vectors after upload | 2,320 of 2,320 identical to the bit |
| full comparison script | 13 of 13 checks passed |
| 60 search probes | 56 identical; 4 differ only in the order of chunks with identical scores |
| catalog lookups | 17 of 17 identical |
| full search on 10 questions | 10 of 10 identical, same order and scores |

It also fixed a display bug: the side panel read a different file from the
answers and showed page-break fragments on five sections.

**Decision.** Supabase became the main store. The local files are kept as a
backup, and every time they are used it is logged. A simulated outage made one
network attempt for 51 lookups, and answers stayed identical.

---

## E20: Why were memory updates slow?

**Question.** Each memory update took 18 to 20 seconds, and a follow-up question
waits for it. Why?

**What we did.** Called the Gemini model directly with a one-word request, with
and without the app's 20-second time limit.

**What we found.**

| call | result |
|---|---|
| Gemini, summariser key, no limit | answered after **64.1 s** |
| Gemini, naming key, no limit | answered after **33.8 s** |
| Gemini through the app (20 s limit) | timed out after 19.8 s |

Nothing was broken; Gemini was just very slow. Every update waited 20 seconds,
timed out, and was then written by the backup model.

**Decision.** The summariser moved to Groq gpt-oss-120b on its own key. The
summary was also made shorter, while the previous question and answer are kept
word for word, since follow-ups rely on them most. Conversation naming stayed on
Gemini, because nothing waits for it.

---

## E21: OpenRouter's free search models

**Question.** Do OpenRouter's free models (three embedders and a reranker) beat
the local search pipeline?

**What we did.** Same 2,320 chunks, same 608 questions, same scoring, with each
model used as its documentation says. The test first reproduced the local
numbers exactly, so any difference comes from the models. Each result was also
compared question by question with the local pipeline.

**What we found.**

| setup | R@1 | R@5 | R@20 | MRR | Right Act |
|---|---|---|---|---|---|
| local: BGE-M3 + bge-reranker | 41.8% | 59.0% | 71.4% | 0.498 | 87.5% |
| BGE-M3 + **Nemotron reranker** | **50.8%** | 67.1% | 71.4% | 0.575 | **91.6%** |
| **nemotron-3-embed**, no reranker | 48.4% | **75.0%** | **86.5%** | **0.595** | 88.5% |
| nemotron-3-embed + bge-reranker | 46.2% | 66.3% | 86.5% | 0.556 | 87.7% |
| nemotron-embed-vl, no reranker | 44.2% | 66.3% | 78.1% | 0.536 | 88.8% |
| lfm-2.5-embedding, no reranker | 29.6% | 47.4% | 66.8% | 0.381 | 76.3% |

- The **Nemotron reranker** orders results best: +9 points on R@1 and 91.6%
  right Act.
- **nemotron-3-embed** finds far more: R@20 up from 71.4% to 86.5%, the biggest
  gain in how many right sections are found at all.
- Adding the local reranker on top of nemotron-3-embed made it **worse**, so
  that option runs with no reranker.
- nemotron-embed-vl was not clearly better; LFM was worse and can't read long
  chunks.
- Nemotron embed plus the Nemotron reranker together only reached 119 questions
  before the daily limit. On those, the reranker added nothing.

**Cost.** Each question costs one OpenRouter request. The free daily limit is
1,000 requests per account (50 without credit), and this test alone used a full
day's limit.

**Decision.** Kept as selectable search options. The deployed site uses
nemotron-3-embed; the local pipeline remains the default elsewhere, since it has
no daily limit. Not yet known: whether these models saw the test questions
during their training, which would inflate their scores.

---

## What the experiments show overall

- **How chunks are cut and labelled doesn't matter here** (E2, E3). The gains
  came from ranking and from better models.
- **Ranking was the first big gain** (E4), and a second trick for the same
  problem added nothing on top (E7).
- **Better models moved the numbers that techniques couldn't** (E21): the
  share of right sections never found fell from 28.6% to 13.5%.

| problem | at the start | now (local default) | with OpenRouter options |
|---|---|---|---|
| right section never found (not in top 20) | 31.6% | 28.6% | 13.5% |
| right Act not first | 17.8% | 12.5% | 8.4% |

**Still open:**
- Answer quality has no numbers yet (E8 is unfinished).
- Questions raising two legal issues can miss one (E15, E17).
- Rewriting the question before searching is the next thing to test for
  sections that are never found.

## A note on method

The heading test (E2) could easily have been reported as a 1.8-point win. Deciding
the 3.7-point noise rule *before* looking at results is what stopped that. Two of
the first three experiments found nothing, and they are kept here on purpose: a
log with only successes would be a sign the log is wrong, not that the system is
good.
