# Verification

> Scope: parsing and chunking. Retrieval is measured rather than verified
> (`retrieval-baseline.md`, `experiments.md`). Generated answers are checked
> per claim at request time by the C2/C3 gates in `app/rag/verify.py` — the
> cited section must have been retrieved, and the quoted span must appear
> verbatim in it. That check is only cheap because parsing is verified here:
> the cited section's exact text is known.

How we know the parse and the chunks are correct, and what is still
outstanding. Two suites, one per stage.

```bash
conda activate ainshonglap
cd backend
python scripts/verify/verify_parsed.py                  # PDF -> parsed JSON
python scripts/verify/verify_chunks.py                  # parsed JSON -> chunks
python scripts/verify/verify_parsed.py --act crpc --verbose
```

## The principle: never let the parser mark its own homework

`parse_law_pdf.py` reports `orphaned: 0` in its own output. That is worth
having, but proves nothing on its own — a parser that drops text can equally
drop it from its own accounting. So every check in `verify_parsed.py`
**re-reads the source PDF**, and where possible compares against a second,
independently produced copy of the law.

| source | role |
|---|---|
| the PDF | ground truth for text, page numbers, layout |
| the parsed JSON | what is being tested |
| `bdlaws.jsonl` (HF `Ashik2380/bdlaws`) | independent scrape, oracle only — never a content source |

## Stage 1 — the parse (`verify_parsed.py`)

**C1 — text recovery.** For every body line in the PDF, its word sequence must
appear somewhere in the JSON. Line-level rather than word-frequency: "32
occurrences of 'the' are absent" is unactionable, whereas a lost line can be
printed and read. Quotes, dashes and whitespace are folded so formatting is not
mistaken for loss.

**C2 — page coverage.** Every body page must be spanned by at least one
section. A page contributing nothing means a page was lost wholesale.

**C3 — section continuity.** Lists numbering gaps, then re-checks each against
the PDF — a gap is a defect only if the source actually prints that section.
Most are genuine repeals: CrPC has 131 gaps in 1–565 and **none** are real.
Duplicates are judged on the full number, so `4` and `4A` are not confused.

**C4 — oracle diff.** Sections and text compared against `bdlaws.jsonl`.
Catches errors the parser and the PDF reader would otherwise agree on.

**C5 — hierarchy integrity.** Chapters appear once and are not interleaved; a
chapter does not span two Parts; sections ascend within a chapter; chapters run
in roman-numeral order; every section belongs to exactly one chapter.

**C6 — child nesting.** Subsection/clause/subclause runs start at `1`/`a`/`i`,
and Explanation/Illustration/Exception/Proviso blocks sit at section level
rather than buried inside the last subsection.

**C7 — citation soundness.** `page_start <= page_end`, pages inside the
document, and each section's text opens with its own number — so a citation
cannot point at a page whose content is something else.

### Current state

| act | sections | C1 recovery | C2 pages | C3 real gaps | C4 phantom / missing | C5 | C6 | C7 | |
|---|---|---|---|---|---|---|---|---|---|
| Evidence Act | 182 | **100.000%** | 82/82 | 0 | **0 / 0** | clean | 8 | 0 | **PASS** |
| Penal Code | 557 | **100.000%** | 183/183 | 0 | 0 / 1 | 1 | 6 | 0 | attention |
| CrPC | 508 | 99.987% | 194/194 | 0 | 0 / 7 | 3 | 230 | 0 | attention |

**Zero phantom sections in any act** — nothing invented. **Zero real numbering
gaps.** The Evidence Act matches its oracle exactly, 182 for 182.

### What it caught

Five real defects, none of which showed up in the parser's own statistics:

1. **Contents pages leaking into the body.** The body-start detector let the
   *first* page of its window be sparse, so the last contents page opened the
   run. Evidence began at p9 instead of p10 and the Penal Code at p33 instead
   of p34, importing title-only entries as duplicate sections (Evidence
   ss.160–167, Penal ss.503–511).
2. **Unpaired gloss notes dropped silently.** A marginal note matching no
   section never reached the JSON. That was the entire text shortfall —
   recovery went 98.2% → **100%** once they were preserved in
   `unpaired_gloss_notes`. Keeping them also makes pairing failures visible.
3. **CrPC s.565 swallowing 24 pages.** See below.
4. **Two bugs in the checker itself** — worth recording, because a check that
   cries wolf gets ignored. Duplicates were folded to a base integer (`4` vs
   `4A`, 43 false positives), and `PART`/`CHAPTER` heading words were counted
   as missing content when they are stored structurally as `number` + `title`.

### The limit of C1, stated plainly

C1 proves nothing is **missing**. It does not prove anything is **correctly
placed** — text filed under the wrong section still counts as recovered.

CrPC s.565 was carrying 10,918 words across 24 pages: after the last section,
every remaining line was appended to it, so the act's whole endnote apparatus
ended up inside the final section. C1 scored it as recovered, because it was.
The defect only became visible when section *sizes* were measured for chunking —
one section 13× larger than the next.

Fixed by treating a page that is entirely footnote-sized as endnote apparatus
and dropping it whole. Verified afterwards that nothing statutory was lost:
of 1,221 dropped lines, **1,218 are 7.6pt** against a 9.0pt body, all fall on
pages 193–216 (s.565 ends on p193), and **none** matches a section opener.

Coverage checks catch deletion. Size and shape checks catch misfiling.

## Stage 2 — the chunks (`verify_chunks.py`)

Recomputed from the parsed JSON, never from anything the chunker reported.
**3/3 acts pass all seven checks.**

**K1 — text fidelity.** Each section's chunks, concatenated in order, must
reproduce that section's text as a **word sequence**. This is the check that
matters, and the first version of it was wrong: it compared
`set(source) - set(chunks)`, and a set comparison passes even when text is
duplicated or reordered.

**K2 — no duplication.** No section's text may appear twice across its chunks.

**K3 — coverage, both directions.** Every section yields at least one chunk;
every chunk maps to a real section.

**K4 — unique ids.** Chunk ids become vector-store keys, so a collision means
one chunk silently overwrites another at index time.

**K5 — metadata and prefix agree.** Each chunk's metadata matches its source
section, and the hierarchy prefix agrees with the metadata. A chunk whose
prefix and metadata disagree would cite the wrong provision.

**K6 — size.** Distribution and count over budget.

**K7 — well-formed.** Every chunk has both a prefix and a body.

| act | sections | chunks | K1 | K2 | K3 | K4 | K5 | K7 | median / max tok | >512 | |
|---|---|---|---|---|---|---|---|---|---|---|---|
| CrPC | 508 | 536 | 0 | 0 | 0 | 0 | 0 | 0 | 172 / 715 | 21 | **PASS** |
| Penal Code | 557 | 583 | 0 | 0 | 0 | 0 | 0 | 0 | 133 / 664 | 7 | **PASS** |
| Evidence Act | 182 | 202 | 0 | 0 | 0 | 0 | 0 | 0 | 156 / 945 | 11 | **PASS** |

### What it caught

1. **Duplicate chunk ids** — `crpc_s100`, `crpc_s344`, `crpc_s429`, `penal_s2`,
   `penal_s499_suba-b`. The parse's known duplicate sections finally bit, one
   stage downstream, where they would have silently destroyed chunks in the
   index. Fixed with an occurrence suffix.
2. **Colliding merged labels** — s.499 emitted the label `a-b` twice.
3. **A bug in the verifier itself** — it keyed sections by number, so duplicate
   sections were pooled and compared against a single source, reporting false
   K1/K2/K5 failures.

Three of the defects found across both stages have been in verification code
rather than in the data. **Check the checker** is not a slogan here; it is the
most common failure mode on this project.

## Outstanding

- **CrPC** — 1 lost line (`"313 CHAPTER XXIII"`, a heading whose footnote marker
  digits are not kept); duplicates 100, 344, 429, 526 from mid-document
  fragments like a bare `"100."` or `"344. (10)"`; 7 sections missing vs oracle
  (138, 205A, 265I, 432, 438, 478, 99A).
- **Penal** — s.2 appears under two chapters; s.59 is inside the printed range
  `58.- 59.` and captured under s.58 (flagged via `section_number_to`).
- **Evidence** — 7 sections carry a trailing footnote sentence (ss.10, 37, 47A,
  57, 67A, 83, 149). Not yet fixed, deliberately: this act sets footnotes at
  9.7pt against a 10.7pt body *and* uses 9.7pt for its gloss column, so size
  cannot separate the two roles. Four attempts to catch the footnotes each cost
  real content — per-page font baselines broke CrPC's endnote detection, and
  looser size or position rules deleted 19 Evidence and 9 Penal sections. A
  bounded, visible defect beats a larger invisible one. The proper fix is
  horizontal position (footnotes start at the body margin, gloss notes sit in
  the margin column), not more size tuning.
- **C6's 230 CrPC "sequence problems"** are probably not defects: a section
  whose `(1)` was repealed legitimately begins at `(2)`. The check cannot yet
  tell that apart from real loss, so its number should not be trusted.
- **Amendment notes are separated but not linked.** All 541 CrPC endnotes are
  preserved in `dropped_as_furniture`, but not yet connected to the `N[` markers
  in section text that refer to them. Correctly excluded from the statute, not
  yet usable for "when did this change?" questions.
