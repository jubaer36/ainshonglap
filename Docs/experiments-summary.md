# Experiments and results

Each experiment's purpose, result and outcome in short. The full details
(setup, tables, discussion) are in [experiments.md](experiments.md).

Retrieval experiments are scored on 608 questions with known answers. A hit
needs the right Act **and** the right section. At this size, a difference under
3.7 points is noise, not a result.

- **R@1 / R@5 / R@20:** how often the right section is 1st / in the top 5 / in the top 20.
- **Right Act:** how often the top result comes from the right Act.

## Summary

| | purpose | outcome |
|---|---|---|
| E1 | Can a model answer without retrieval? | No: invented law. Retrieval is required |
| E2 | Does a heading prefix on chunks help? | No difference |
| E3 | Is 512 tokens the right chunk size? | No difference |
| E4 | Does a reranker help? | Yes: +9.2 points top-1. Adopted |
| E5 | Embed subsections as well as sections? | Higher top-20. Adopted |
| E6 | Other rerankers? | Couldn't be measured fairly |
| E7 | E4 + E5 together? | They don't add up; top-1 stays 41.8% |
| E8 | Does the model answer when retrieval missed? | Unfinished |
| E10 | Refuse when the search score is low? | Too costly; no default set |
| E11 | Groq for speed and strict JSON? | Adopted, with retrieval mandatory |
| E12 | An exact-lookup catalog? | Built and verified. Adopted |
| E13 | A planner model instead of patterns? | 42/42 correct types. Adopted |
| E14 | Mercury (diffusion model)? | Slower here. Selectable only |
| E15 | Voice input and memory | Built, not measured |
| E16 | Gemini as planner? | Faster at the time. Later replaced |
| E17 | Why was an answerable question refused? | The answer prompt, not retrieval |
| E18 | Planner on its own key; two Acts in one question | Fixed; both Acts now work |
| E19 | Move the laws to Supabase? | Identical results. Adopted |
| E20 | Why is memory slow? | Gemini took 34–64 s. Moved to Groq |
| E21 | OpenRouter's free search models? | Two beat the local pipeline |

## Retrieval

**Where it stands** (608 questions):

| setup | R@1 | R@5 | R@20 | right Act |
|---|---|---|---|---|
| BGE-M3 only, first baseline | 32.7% | 54.1% | 68.4% | 82.2% |
| BGE-M3 + reranker (default) | 41.8% | 59.0% | 71.4% | 87.5% |
| BGE-M3 + Nemotron reranker | **50.8%** | 67.1% | 71.4% | **91.6%** |
| nemotron-3-embed, no reranker | 48.4% | **75.0%** | **86.5%** | 88.5% |

### E2: heading prefix on each chunk
**Purpose:** check whether adding "Act > Chapter > Section" to each chunk helps
search, as published work claimed.
**Result:** all five versions within 3 points of each other. No difference.
**Outcome:** kept a short prefix; it is close to free either way.

### E3: chunk size
**Purpose:** check whether 512 tokens is the right maximum chunk size.
**Result:** 256, 512 and 1024 were within 0.7 points. Only 3–8% of sections are
long enough to be affected.
**Outcome:** kept 512 and stopped tuning it.

### E4: reranking
**Purpose:** the right section was usually in the top 20 but ranked too low.
**Result:** bge-reranker-v2-m3 raised top-1 from 32.7% to 41.9% (+9.2 points)
and right-Act from 82.2% to 87.2%. The first clear win.
**Outcome:** adopted.

### E5: sections and subsections
**Purpose:** check whether embedding subsections as well as whole sections
finds more.
**Result:** top-20 rose from 66.6% to 70.4%. Subsections alone were worse.
**Outcome:** adopted (index both).

### E6: other rerankers
**Purpose:** compare mxbai, Qwen3 and Jina rerankers with bge-reranker.
**Result:** the others scored badly because the test harness loaded them
wrongly (random weights), not because they are bad. Jina crashed.
**Outcome:** bge-reranker kept; the others recorded as unmeasured.

### E7: E4 and E5 together
**Purpose:** confirm the two gains add up in the real serving pipeline.
**Result:** they don't. With the reranker on, top-1 is 41.8% either way; the
reranker already found what E5 added.
**Outcome:** both kept (E5 costs nothing at query time), but the honest
number is 41.8%.

### E21: OpenRouter's free search models
**Purpose:** test free embedders and a reranker on OpenRouter against the local
pipeline.
**Result:** nemotron-3-embed alone beat it on every measure (top-20 71.4% ->
86.5%). The Nemotron reranker gave the best top-1 (50.8%) and right-Act
(91.6%). nemotron-embed-vl was not clearly better; LFM-2.5 was worse. Adding the
local reranker on top of nemotron-3-embed made it worse. Nemotron embed +
reranker together stopped at 119 of 608 questions (daily API limit).
**Outcome:** kept as selectable search options. The deployed build uses
nemotron-3-embed.

## Answering

### E1: no retrieval at all
**Purpose:** see what a model does with no law in front of it.
**Result:** the small local model (Qwen2.5-VL-3B) invented section text on 3 of
5 questions, including getting Evidence Act s.25 backwards. Gemini got all 5
right.
**Outcome:** retrieval and citation checks are required; effort went to
retrieval first.

### E8: answering when retrieval missed
**Purpose:** check whether the model answers anyway when the right section
wasn't retrieved.
**Result:** 5 of 16 answered, but the run was cut short and the comparison
group never ran. Not a result.
**Outcome:** unfinished. The citation checks ship regardless.

### E10: a score threshold for refusing
**Purpose:** refuse before answering when the search score is low.
**Result:** scores overlap heavily (AUC 0.727). Refusing 70% of failed searches
would also throw away 35% of questions that could be answered.
**Outcome:** no threshold set by default.

### E11: Groq as the answer provider
**Purpose:** faster answers that still follow the strict JSON format.
**Result:** 0.95–1.31 s per call, JSON format kept exactly. Without retrieval it
still got the law wrong.
**Outcome:** adopted for speed, never for its own legal knowledge.

### E14: Mercury (a diffusion model)
**Purpose:** test Inception's Mercury, claimed to be much faster.
**Result:** follows the JSON format, but on the planner's short calls it was 2–4
times slower than Groq.
**Outcome:** offered as a selectable model, not the default.

### E17: a refusal traced end to end
**Purpose:** find out why "If I can't prove I'm innocent, am I guilty?" was
refused.
**Result:** retrieval found the right sections (Evidence Act ss.101–106). The
answer prompt's "don't reason from general principles" rule made the model
decline. Raising the number of sections from 5 to 8 didn't fix it.
**Outcome:** a known limit, recorded with the likely fixes.

## Planning and data

### E12: exact-lookup catalog
**Purpose:** answer "Chapter XVI" or "sections 378 to 382" exactly, not by
similarity.
**Result:** 3 Acts, 76 chapters, 1,247 sections, 878 cross-references, all
checks passed. It also surfaced known parser gaps (a missing CrPC s.99A).
**Outcome:** adopted.

### E13: a planner model
**Purpose:** replace pattern matching, which missed phrasings nobody
anticipated.
**Result:** 42/42 question types right, 25/27 lookups right (then fixed).
Split "not a legal question" from "law we don't hold".
**Outcome:** adopted.

### E16: Gemini as planner
**Purpose:** the Groq planner had become slow (43 s when rate-limited).
**Result:** Gemini planned in 1.3–1.8 s, after fixing a schema detail Gemini
rejected.
**Outcome:** adopted then; later replaced (E18).

### E18: planner on its own key
**Purpose:** fix a two-Act question ("sections 1 to 5 of the Evidence Act and the
CrPC") that was wrongly refused.
**Result:** the plan format could only hold one Act. Changed to a list; the
planner moved to gpt-oss-120b on its own Groq key (1.0–2.3 s).
**Outcome:** adopted. Both Acts now come back.

### E19: laws served from Supabase
**Purpose:** move the laws from local files to Supabase without changing any
result.
**Result:** all 2,320 vectors bit-identical, 13/13 checks, 10/10 searches
identical.
**Outcome:** adopted; local files kept as a logged fallback.

## Voice and memory

### E15: voice input and memory
**Purpose:** record what was built without claiming numbers.
**Result:** both work in use. Follow-ups resolve ("what if he survived?"). Voice
accuracy on real speech is not measured.
**Outcome:** built, not measured.

### E20: slow memory updates
**Purpose:** find why each memory update took about 20 s.
**Result:** Gemini took 34–64 s per call, so every update hit the 20 s timeout.
**Outcome:** the summariser moved to Groq gpt-oss-120b; the summary was made
shorter.
