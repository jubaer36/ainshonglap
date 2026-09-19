# Chunking: options and decision

What to feed the embedder. Decided from the measured shape of our own parsed
data, cross-checked against published results for legal retrieval.

## What our data actually looks like

Measured over the three parsed acts (tokens ≈ words × 1.35):

| act | sections | median | p90 | p95 | >512 tok | have subsections to split at |
|---|---|---|---|---|---|---|
| CrPC | 508 | 126 | 378 | 508 | 24 (4.7%) | **24 of 24** |
| Penal Code | 557 | 97 | 256 | 378 | 16 (2.9%) | **16 of 16** |
| Evidence Act | 182 | 111 | 410 | 563 | 14 (7.7%) | **14 of 14** |

Three facts decide the design:

1. **Sections are small.** Median 96-126 tokens — comfortably inside any
   embedding window. The common case needs no splitting at all.
2. **Oversize is rare.** 3-8% of sections exceed 512 tokens.
3. **Every oversized section has subsections to split at — 54 of 54.** So a
   size-based fallback splitter is not needed: the document supplies a legal
   boundary everywhere one is required.

That last point is the whole argument. We never have to cut mid-provision.

## Options considered

### 1. Fixed-size windows (e.g. 512 tokens, 50 overlap)
**Rejected.** The standard default, and actively harmful here. Legal section
boundaries are drawn deliberately by the legislature to group conditions with
their consequences; a fixed window cuts across them, so a retrieved chunk can
carry a condition without its consequence. [Reuter et al., *Towards Reliable
Retrieval in RAG Systems for Large Legal Datasets*](https://arxiv.org/pdf/2510.06999)
names this directly: legal texts are "organized in complex layouts with nested
sections, subsections, and dense cross-references. Standard chunking strategies
ignore document hierarchy, which cuts off these logical connections. As a
result, retrieved chunks may appear relevant but lose their intended meaning
when disconnected from their structural context."

Our data makes it pointless as well as harmful: with a 126-token median, a
512-token window would staple ~4 unrelated sections together.

### 2. Semantic / embedding-similarity chunking
**Rejected.** Splits on topic drift rather than structure. Same objection —
it overrides boundaries the legislature already drew, and it costs an embedding
pass at ingestion to arrive at worse boundaries than the ones printed in the
document.

### 3. Section-level chunking, split at subsections when oversized
**Adopted.** One chunk per section; when a section exceeds the budget, split at
its own subsection boundaries, which our parse already records in `children[]`.
Matches the structure-preserving approach that measures best in the literature,
and our data shows it is always available.

### 4. Parent-child / auto-merge retrieval
**Adopted as a later refinement (step 8, not now).** Embed the small unit
(subsection), return the parent (section) for generation — so retrieval is
precise but the model still sees the whole provision. Worth doing, but it is a
retrieval-side change; it does not alter how we chunk, so it can wait until
there is an eval set to measure it against.

### 5. Context prepended to each chunk
**Adopted, then cut back after measurement — see `experiments.md` E2.** The
reasoning below is what justified it; an A/B on our own corpus later found the
effect undetectable (3.0pp spread against a ±3.7pp interval), including versus
no prefix at all. The prefix is now `Act, Section N: title` rather than the full
Part>Chapter path, saving 38 tokens a chunk. Recorded here rather than rewritten
because importing a published number without its setup was the mistake worth
remembering. Two results originally cited:

- [Anthropic, *Contextual Retrieval*](https://www.anthropic.com/engineering/contextual-retrieval):
  prepending chunk-specific context before embedding cut retrieval failures by
  **49%**, and **67%** combined with BM25 and reranking.
- The legal paper above identifies **Document-Level Retrieval Mismatch** — the
  retriever returning text from *entirely the wrong statute* — as a critical
  failure mode, and fixes it by injecting document-level context into each
  chunk. Notably, a *generic* summary outperformed one built with legal-expert
  templates, so this should stay simple.

DRM is a live risk for us specifically: the CrPC, Penal Code and Evidence Act
share vocabulary heavily, and a bare "Whoever commits..." chunk carries nothing
that identifies which act it came from. We have the fix for free — the parse
already stores full ancestry inline on every section, so the prefix is a
formatting step, not an LLM call:

```
The Code of Criminal Procedure, 1898 > Part III > Chapter V >
Section 54: When police may arrest without warrant

54.(1) Any police-officer may, without an order from a Magistrate...
```

## Decision

1. One chunk per **section**; hierarchy path prepended to the embedded text.
2. Sections over budget split at **subsection** boundaries, never mid-provision.
3. Keep `act / part / chapter / section / page_start / page_end` as metadata for
   filtering and for rendering the citation.
4. Defer parent-child auto-merge until there is an eval set to prove it helps.

## Blocker — fixed

CrPC s.565 was 14,739 tokens spanning 24 pages: the parser appended every line
after the last section to it, so it had swallowed the act's entire endnote
apparatus. Chunking that would have emitted one enormous, mostly-junk chunk.

Fixed by dropping pages that are entirely footnote-sized. s.565 is now 280
words on one page, and the largest section in the corpus is 1,917 tokens
(Penal s.499, defamation with its exceptions) rather than 14,739. The size
table above reflects the corrected parse.

Worth recording how it surfaced: seven verification checks passed it, because
C1 asks only whether text is *present*, and those endnote lines were present —
inside s.565. It showed up the moment section sizes were measured for chunking.
Coverage checks catch deletion; size and shape checks catch misfiling.


## Result

`backend/scripts/chunk_sections.py` -> `Laws/main/chunks/<act>.json`.

| act | sections | chunks | split | median | p95 | max | >512 tok |
|---|---|---|---|---|---|---|---|
| CrPC | 508 | 536 | 23 | 172 | 500 | 715 | 21 |
| Penal Code | 557 | 583 | 16 | 133 | 450 | 664 | 7 |
| Evidence Act | 182 | 202 | 14 | 156 | 507 | 945 | 11 |
| | | **1,321** | | | | | |

A finished chunk:

```
The Code of Criminal Procedure, 1898 > Part III: General Provisions >
Chapter V: Of Arrest, Escape And Retaking > Section 54: When police may arrest without warrant

54.(1) Any police-officer may, without an order from a Magistrate and
without a warrant, arrest- firstly, any person who has been concerned...
```

The hierarchy prefix costs a median 40 tokens (max 76) — roughly 25% overhead
on a 172-token median chunk. Worth re-measuring against an eval set once one
exists, since the 49% figure it is justified by comes from someone else's corpus.

## Verification

`backend/scripts/verify/verify_chunks.py` — recomputed from the parsed JSON, never
trusting what the chunker reported. **3/3 acts pass all seven checks.**

| | |
|---|---|
| K1 | each section's chunks, in order, reproduce its text as a word *sequence* |
| K2 | no section's text appears twice |
| K3 | every section yields a chunk; every chunk maps to a real section |
| K4 | chunk ids unique — they become vector-store keys |
| K5 | metadata matches the source section, and the prefix agrees with metadata |
| K6 | size distribution |
| K7 | every chunk has both a prefix and a body |

Three real bugs surfaced, and the first check to find them had to be rewritten
first: the original fidelity test compared `set(source) - set(chunks)`, and a
set comparison passes even when text is duplicated or reordered.

1. **Duplicate chunk ids** (`crpc_s100`, `crpc_s344`, `crpc_s429`, `penal_s2`).
   Ids become vector-store keys, so each collision would have silently
   overwritten a real chunk at index time. The parse's known duplicate sections
   finally bit, one stage downstream. Fixed with an occurrence suffix.
2. **Colliding merged labels** — s.499 emitted the label `a-b` twice.
3. **A bug in the verifier itself** — it keyed sections by number, so duplicate
   sections were pooled and compared against a single source, reporting false
   failures. Now keyed by `(number, occurrence)`.

That third one is the recurring lesson: check the checker. Several defects
found across parsing and chunking have been in verification code, not data.

## Known limits

- **39 chunks exceed 512 tokens** (max 945 — Evidence s.32, dying declarations).
  Only binds if the embedder caps at 512; BGE-M3 handles 8192. Decide when
  picking the model.
- Evidence's 7 footnote-tainted sections carry that text into their chunks.
- The duplicate sections behind the id collisions still exist in the parse —
  now safely disambiguated rather than silently colliding, but worth fixing at
  the parser.
