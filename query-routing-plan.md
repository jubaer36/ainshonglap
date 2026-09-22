# Query planning, structural lookup and broad-coverage retrieval

> **Status.** The planner and the catalog are built and measured (E12, E13);
> the executors, the threshold and conversation scope are not. Where the
> implementation diverged from this design, the divergence is recorded in
> *What was actually built* at the end — read that before treating anything
> above it as current.

## Problem statement

The current system is good at focused questions whose answer is concentrated in
one or a few provisions. It is not yet designed for requests that require a
different operation:

| request | current limitation | correct operation |
|---|---|---|
| “Someone kills/steals…” | generator sees only a fixed top-k of sections | focused or comprehensive semantic retrieval |
| “Give an overview of the Penal Code” | top-k retrieval cannot represent a whole Act | hierarchical outline/summary |
| “List important Penal Code laws” | “important” is subjective and top-k similarity is not importance | clarification or an explicit selection policy |
| “Show Chapter XVI / section 379” | semantic ranking is unnecessary and can miss the named coordinate | exact catalog query |
| “Who are you / what can you do?” | no legal source is required | capability response |
| “Show that section” | history is currently ignored | plan using typed conversation scope |

The narrow-answer behavior is not a mysterious model limitation. It is
configured by `RAG_FINAL_K` (5 originally, now 8), and only sections surviving
into generated claims are shown. Simply increasing that number adds distractors,
latency and prompt tokens without defining what “all relevant laws” means — E17
raised it 5 → 8 on a traced refusal and the three new slots took noise, while
the section that would have answered the question sat outside the candidate pool
entirely.

## Design decision

Use a model-authored **query plan**, followed by deterministic validated
execution.

The model decides what the user is asking for. Code remains responsible for
facts, corpus coordinates, SQL parameters, retrieval, pagination and citation
verification. This avoids both extremes:

- regexes should not be the primary intent router;
- the model should not invent SQL, section coordinates or statutory content.

```text
question + conversation scope
        |
        v
Groq Qwen planner (strict JSON schema)
        |
        v
plan validator -- invalid/ambiguous scope --> clarification
        |
        +--> meta executor
        +--> exact lookup executor --------> SQLite catalog
        +--> browse executor --------------> SQLite catalog
        +--> overview executor ------------> hierarchy + grounded prose
        +--> legal executor ---------------> FAISS + reranker + citation gates
        +--> general executor -------------> clearly ungrounded answer
```

Model choice makes routing non-deterministic in origin, but strict schema and
validation make its effects controlled and reviewable.

## Planner contract

One small, temperature-zero call should return a plan, never an answer:

```json
{
  "intent": "meta | lookup | browse | overview | legal | general",
  "operation": "capabilities | exact_fetch | enumerate | outline | semantic_search | general_answer",
  "act_slugs": ["penal"],
  "chapter": "XVI",
  "section_numbers": ["379"],
  "search_queries": ["punishment for theft", "definition of theft"],
  "coverage": "focused | comprehensive",
  "needs_clarification": false,
  "clarification": ""
}
```

All fields should be required because Groq strict mode expects a closed schema;
irrelevant strings/lists are empty. The planner receives a compact capability
manifest (known Act slugs and supported operations), not the full statute text.

Validation rules:

1. `act_slugs` must exist in the catalog.
2. Sections and chapters must exist under the selected Act.
3. A section number shared by multiple Acts requires an Act or clarification.
4. `lookup`, `browse` and `overview` never fall through silently to a nearby vector hit.
5. Invalid model coordinates produce a clarification or visible not-found result.
6. The planner cannot supply answer text, citations or SQL.

The existing patterns can remain only as an outage fallback or as features fed
to evaluation. They should no longer decide the normal route before the model.

## Structural catalog: SQLite plus the vector index

Use both systems; they solve different problems.

- SQLite answers identity, hierarchy, filtering, enumeration and exact lookup.
- FAISS answers semantic similarity for narrative legal questions.
- The parsed JSON/PDF remains the source of truth. SQLite is a rebuildable read model.

SQLite is sufficient at this corpus size and adds no service dependency. A build
script should create `Laws/main/catalog.sqlite` from parsed JSON using tables such
as:

```text
acts(id, slug, name, act_no, source_pdf)
chapters(id, act_id, number, title, part)
sections(id, act_id, chapter_id, number, title, text, page_start, page_end)
subsections(id, section_id, number, type, text)
cross_references(source_section_id, target_act_id, target_section_number)
```

Add indexes on `(act_id, number)`, `(act_id, chapter_id)` and section titles.
FTS5 over titles/text can support catalog search, but it does not replace vector
retrieval. Application code builds parameterized SQL from the validated plan;
the LLM never writes executable SQL.

## Resolution by request type

### 1. Meta questions

The planner selects `meta/capabilities`. Return a concise capability description
assembled from live index/catalog metadata: covered Acts, section counts,
grounding guarantees and limitations. No retrieval or legal generation call is
needed.

### 2. Exact section or chapter requests

The planner extracts Act/chapter/section scope. The validator checks it, then the
catalog executor returns every matching record in statutory order. This is fully
deterministic after the model has decided the scope.

Examples:

- “State Penal Code section 379” -> one exact section
- “List sections 378 to 382” -> an exact inclusive range
- “Show Chapter XVI of the Penal Code” -> every section in that chapter
- “Show section 25” -> clarify the Act when multiple Acts contain section 25

Results need pagination for large chapters or Acts, not truncation disguised as
completeness.

### 3. Whole-Act overview

Do not retrieve a top-k of sections. Fetch the full hierarchy from the catalog:
chapter titles, parts, section counts and optionally representative section
titles. The model may turn that verified outline into prose, but it must not add
penalties or rules absent from the outline.

“Important laws” needs clarification because importance depends on purpose. Ask
whether the user means common offences, police procedure, evidence, exam topics,
or another criterion. If the product later offers a default list, its ranking
policy must be explicit and evaluated; vector similarity alone is not importance.

### 4. Focused legal questions

Keep the existing high-precision path for ordinary questions:

1. Embed the planner's statutory search phrases plus the original question.
2. Retrieve a broad candidate pool and collapse children to sections.
3. Cross-encoder rerank.
4. Send a small evidence set to Qwen.
5. Apply the existing retrieved-section and verbatim-quote gates.

The generator should answer the question, not dump every loosely related law.

### 5. Comprehensive related-law requests

When the planner sets `coverage=comprehensive`, use a discovery pipeline rather
than changing the focused answer's `FINAL_K` globally:

1. Decompose the request into statutory facets in the planner call: definition,
   offence, punishment, exceptions, procedure and evidence where applicable.
2. Retrieve candidates for every facet (for example, 50 vectors each).
3. Merge and deduplicate by `(Act, section)`.
4. Rerank against both the original question and matching facet.
5. Apply a fitted absolute relevance threshold rather than an arbitrary top N.
6. Expand explicit cross-references by one hop, marking why each was included.
7. Group results by Act/chapter and paginate them.

**E17 supplies evidence that step 2 matters more than it looks.** `plan.query`
already concatenates the raw question with the planner's rewrite and terms,
specifically so a rewrite cannot lose a provision the plain question would have
found. On the traced question it lost them anyway: the rewrite's statutory
register dominated the embedding, and retrieval on each half alone returned
near-disjoint sets — the rewrite gave Evidence ss.101-110 and no CrPC, the raw
question gave CrPC ss.245/265H and almost no Evidence. Only the union contains
the answer. Concatenating into one embedding is not the same as retrieving twice
and merging, and the difference is a provision.

Worth guarding at the same time: the planner emitted `presumption of innocence`
as a search term, a phrase with **zero** occurrences anywhere in the three Acts.
Terms with no lexical support in the catalog can be dropped before embedding —
FTS5 answers that in microseconds.

The response should separate:

- a concise generated answer based on the strongest verified evidence; and
- a deterministic “Related provisions” list containing every section above the
  measured threshold, including title, Act, relevance reason and source link.

This prevents a 20-section discovery result from becoming one enormous LLM
prompt. It also avoids claiming mathematical completeness: “all results above
the measured threshold” is testable; “all relevant laws” is not.

## Conversation context

The current frontend sends history, but assistant messages have no usable text
and the backend ignores the field. Add typed conversation scope instead of
replaying an unlimited transcript:

```json
{
  "active_act": "penal",
  "active_chapter": "XVI",
  "active_sections": ["299", "300", "302"],
  "last_intent": "browse"
}
```

The server should return updated scope with each answer and the client should
send it on the next request. Then “show that section” can be planned against a
known referent without asking the model to infer it from missing assistant text.

## API and presentation changes

Extend the response contract with structured discovery metadata rather than
encoding everything as prose:

```text
plan.intent / plan.coverage        optional debug metadata in development
related_sections[]                 deterministic catalog records
related_total                      count before pagination
related_next_cursor                continuation cursor
conversation_scope                 resolved Act/chapter/sections for follow-ups
```

The frontend should render related provisions as a grouped, expandable list and
offer “load more.” Citation previews continue to use exact section keys.

## Evaluation required before adoption

The existing 608-question set has one gold Act/section per question. It measures
focused retrieval but cannot measure “all related provisions.” Add two suites:

### Router and scope set

Expand `router_cases.py` with at least:

- paraphrases for every intent;
- explicit and ambiguous section numbers;
- chapter ranges and described chapters;
- whole-Act overviews versus Act enumeration;
- “important” requests that require clarification;
- follow-ups using conversation scope;
- adversarial requests containing both meta and legal language.

Measure intent accuracy, exact scope accuracy, clarification accuracy and
invalid-coordinate rejection separately.

### Multi-section coverage set

Have a human label all material provisions for a smaller set of representative
scenarios. Measure section-level precision, recall and F1, plus Act accuracy and
the number of unsupported inclusions. Fit the relevance threshold only on a
development split and report held-out results.

Generation evaluation remains separate: citation validity, claim support,
refusal quality and latency.

## Implementation phases

1. **Baseline:** freeze and expand router/structural/comprehensive test cases.
2. **Planner:** add the strict plan schema and run it in shadow mode beside the current router.
3. **Catalog:** build SQLite from parsed JSON and verify row/text parity against the source read model.
4. **Executors:** implement meta, exact lookup, browse and overview against validated plans.
5. **Focused path:** route existing RAG through the plan without changing measured retrieval.
6. **Coverage path:** add facet retrieval, merge, threshold, cross-reference expansion and pagination.
7. **Context:** add typed conversation scope to backend and frontend contracts.
8. **Evaluation:** compare routing and multi-section coverage before replacing the current router.
9. **UI:** add grouped related provisions, counts and load-more behavior.

## Acceptance criteria

- Every known exact Act/section request returns the exact catalog record.
- Invalid or ambiguous coordinates never silently return the nearest section.
- Meta and overview requests make no vector-search call.
- Legal claims still pass the existing citation gates.
- Comprehensive results are thresholded, grouped and paginated, not capped at five.
- Router and scope accuracy are published on a pinned regression set.
- Multi-section recall/precision are published on human-labelled scenarios.
- Planner failure has an explicit safe fallback and never executes model-written SQL.

---

## What was actually built

Implemented: the catalog (E12) and the planner (E13). Not implemented: the
executors, the relevance threshold, conversation scope, the API and UI changes,
and the multi-section ground-truth set.

Four departures from the design above, each for a reason worth keeping.

**1. Four categories, not six intents.** `meta`, `structural`, `semantic`,
`general`. Lookup, browse and overview all became *operations* inside
`structural` because they differ only in which catalog query runs — same
validator, same fetcher, same failure modes. Six top-level intents would have
been three ways of spelling "ask the database".

**2. `coverage: focused | comprehensive` was dropped.** The design had the
planner choose between a focused answer and a broad one. That choice does not
belong to the planner: how many provisions are relevant is a property of the
retrieval scores, not of the question's phrasing. The fixed `FINAL_K` is replaced
by a fitted relevance threshold instead (`plan.md` phase 4) — at most 10 sections to
the generator, every section above the threshold shown to the reader as catalog
records. A question that touches two provisions shows two; one that touches
eighteen shows eighteen, with no planner field involved.

**3. The chapter list is not in the planner prompt.** The design assumed one
call with a capability manifest. Sending all 76 chapters cost 1,873 tokens and
exhausted Groq's input-tokens-per-minute budget after three questions. It is now
two tiers: the act list always (778 tokens), the chapter list only when the
planner reports a chapter described by subject rather than numbered.

**4. Patterns were removed outright, not kept as a fallback.** The design said
they could stay "as an outage fallback or as features fed to evaluation". They
are gone from the decision path entirely, because a fallback that silently
produces a worse route is how the old router degraded without anyone noticing.
A planner outage now falls back to plain semantic retrieval on the raw question
— the behaviour the system had before planning existed — and says so in
`plan.error`.

The design's own instruction to run the planner in shadow mode before replacing
the router was not followed either. It was written when the router ran on
lfm-2.6B; the pinned 41-case set (27 of them the router's own cases) gave a
direct before/after without a shadow period, and the planner's 42/42 left
nothing for a shadow run to decide.

### Validation rules, as implemented

Rules 1-6 from *Planner contract* all hold, with one addition the design did not
anticipate: a chapter number is **canonicalised to the Act's own numbering**
(`16` → `XVI`). The catalog matches either, so this changes no result, but a
plan carrying the reader's spelling leaks it into answer headings and breaks
every equality check downstream. This was the only scope failure in E13.
