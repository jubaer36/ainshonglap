# Parsing Plan: Laws PDFs → hierarchical JSON

Goal: every retrievable unit carries its full ancestry (Act → Part → Chapter →
Section → Subsection → Clause), its verbatim text, and a citation that can be
checked against the source. Two hazards decide the design: **hierarchy loss**
and **text split across page boundaries**.

## 1. What was actually tested (not surveyed — run against these PDFs)

Every row below was executed against the real Laws PDFs. The two discriminators
are the ones §2 explains: does it pair the gloss note to the right section
(including on the Penal Code's **mirrored** pages), and does it rejoin text
split across a page break.

| Option | Gloss pairing (gloss-left) | Gloss pairing (mirrored) | TOC filtered | Cost | Verdict |
|---|---|---|---|---|---|
| **PyMuPDF + custom regex** | ✅ | ✅ auto-detects the swap | ✅ | free, local | **Adopted** |
| **LandingAI ADE** | ✅ | ⚠️ lost in markdown, **recoverable from grounding bboxes** | ⚠️ manual | ~218 credits/act | Capable; solves a problem we no longer have |
| **unstructured.io** (`fast`) | ✅ inline, correctly ordered | ❌ notes separated from sections | ❌ none | free | Near miss |
| **pdftotext -layout** (poppler) | ⚠️ via character offset | ⚠️ via character offset | ❌ | free | Viable, loses font sizes |
| **pymupdf4llm** | ⚠️ pairs then degrades | untested | ❌ | free | `<mark>` noise; fires OCR on text PDFs |
| **pdfplumber** `layout=True` | ⚠️ as pdftotext | ⚠️ | ❌ | free | No advantage over pdftotext |
| **Docling** | ❌ | ❌ | ❌ | free | **Rejected as primary; kept as the scan fallback** — loses text |
| **bluebell** | n/a — authoring format | n/a | n/a | free | Output target only |

Detail on the ones that decided the design:

- **Docling** — 3 independent failures on 20 pages of CrPC: (a) page-break
  orphans mislabeled — "Jurisdiction in the | case of juveniles" split across
  p13→14, back-half tagged as its own `section_header`; 12 mid-sentence orphan
  blocks in 20 pages. (b) S.4's `(a)`–`(u)` definition list flattened into one
  run-on paragraph. (c) gloss/body pairs hallucinated as markdown tables, with
  `"2 of 16 pdf cells ... dropped from the table"` — real text loss.
- **PyMuPDF + regex** — during the spike: CrPC 23/23 sections titled;
  page-spanning sections correct (S.3 `p0-1`, S.4 `p1-3`); Penal's mirrored
  gloss auto-detected (p45 right → p46 left → p47 right…). Now the sole parser,
  `backend/scripts/parse_law_pdf.py` — see the results table in §4.
- **LandingAI ADE** — full Penal Code: 217 pages, **0 failed**, 44s, **218.2
  credits**. Text quality good (34/40 sampled oracle sections found verbatim).
  But its **elements straddle section boundaries** — only 210 elements open
  with a section number against ~947 section numbers in the text — so the
  structure tree can't give section granularity at the `dpt-3-pro` tier
  (word-level `atomic_grounding` is a `dpt-3-verity` feature). Its element
  bboxes *do* separate columns cleanly, and y-overlap pairing on them scored
  **5/5** on the mirrored sample. Worth keeping for the CrPC Second Schedule
  (a real table problem), not for bulk ingestion.
- **unstructured.io** — types Part/Chapter and gloss notes as `Title`, body as
  `NarrativeText`, correctly ordered on gloss-left pages. Falls down on
  mirrored pages (notes emitted away from their sections) and has no TOC
  filter — **43 duplicated section openings** from contents pages.
- **bluebell** (Laws.Africa, Akoma Ntoso) — recognised `CHAPTER XVI`, then put
  sections 299/300/302, illustrations, subsections and the Exception into one
  flat `hcontainer` of `<p>`s. An authoring dialect for structure you already
  know, not a discoverer of it.
- **slaw** (its predecessor) — archived Aug 2024, dropped PDF support in v2.0,
  Ruby. Architecture still validates ours: extract text separately, then apply
  a grammar.
- **bdlaws.jsonl** (HF `Ashik2380/bdlaws`) — **adopted as the validation oracle,
  not as a corpus.** All 3 acts complete; 0 sections present in the PDFs are
  missing from it. Text verified exact on CrPC 154, Penal 302, Evidence 25. But
  100% flat: 0 Parts, 0 structural Chapters, no subsection→parent link, no
  pages — so it cannot supply the hierarchy this project needs, and an early
  design that took text from here and structure from the PDFs was abandoned in
  favour of parsing both from the PDF. Its value is that it makes correctness
  *measurable*: every parsed section is diffed against it.
- `bangladesh-legal-qa-dataset` — rejected as corpus: CrPC truncated at s.473
  of 565, Evidence at s.42 of 167. Useful only for its 2,165 QA pairs.
- **OCR** (Tesseract/Paddle/MinerU) — cut: PDFs are native text, verified. OCR
  adds a misread-digit risk to section numbers, which is a citation bug.
- **Other cloud APIs** (LlamaParse, Azure DI, Textract, Reducto) — cut on the
  same reasoning that demoted ADE: cost per page for text we already have.
- **LLM/VLM as primary extractor** — cut, kept as bounded fallback. "Looks like
  Section 54" is unverifiable on exactly the field that must never be wrong.

Also surveyed, not tested: **OpenDataLoader PDF** (explicit `--heading-hierarchy`,
infers depth from outline → numbering → visual style; our PDFs have no outline —
verified 0 bookmarks), **CFPB regulations-parser** (deprecated, but states our
exact problem: *"without guidance from paragraph markers/headers, the parser
must make assumptions about the hierarchy"*), **stillaw** (Polish legislation —
independently also settles on `pdftotext`), **open-source-legislation** (useful
schema pattern: Structure nodes vs Content nodes), **Open-US-Law** (its
`act_status` field maps well to our repealed/omitted sections).

### Why the free local option wins over the best paid one

ADE is the most capable parser tested. It is not adopted because its headline
strength — text extraction quality — is a problem we stopped having the moment
the JSONL oracle was verified. What we still need from the PDFs is *structure*,
and there PyMuPDF's **line-level** coordinates beat ADE's **element-level**
boxes, precisely because ADE's elements straddle section boundaries and lines
do not. At ~218 credits/act across 20+ acts, plus a network dependency in the
ingestion path, it would be paying recurring cost to duplicate something we get
free and lose granularity doing it.

## 2. The two hazards, and the mechanism for each

### Hazard A — text split across page boundaries

Docling's failure mode: it parses per page, so a section broken by a page break
becomes two records and the orphan half can be mislabeled as a new section.

**Mechanism: never parse per page.** Extract every line from every page with its
coordinates, strip page furniture, sort into a single ordered stream, *then* run
the section regex over that stream. A page boundary becomes ordinary whitespace
mid-sentence — there is no page-level boundary left for the parser to mistake
for a section start. Already working: CrPC S.3 spans `p0-1`, S.4 spans `p1-3`,
both reassembled intact.

Page furniture must be removed *before* concatenation or it lands mid-sentence.
Two rules, both learned from real bugs during the spike:

- **Headers: content-based, never positional.** A "top 11% of page" rule looked
  right on the Penal Code (running header at y=93.7) and silently ate real
  statutory text from CrPC S.3, whose body starts at y=72.3 with no running
  header at all. Match instead on: a bare page number, or a short line whose
  words overlap the act title.
- **Footnotes: proportional font size + position.** Absolute cutoffs break
  because the tiers differ per document *and per page* (CrPC body 9.0/gloss 8.5;
  Penal p45 body 11.0/gloss 10.0; Penal p52 body 10.7/gloss 9.7/footnote 8.8).
  A flat `base - 0.6` deleted 341 lines on Penal — every gloss note and every
  Illustration. Use `size < 0.88 × body_size` **and** bottom-of-page.
- **Endnote pages: drop whole.** The CrPC ends with 24 pages set entirely in
  footnote type. The bottom-of-page guard spares their upper half, so they were
  appended to the last section — s.565 reached 10,918 words. A page that is
  ≥90% footnote-sized is endnote apparatus and goes entirely. Checked
  afterwards: 1,218 of 1,221 removed lines are 7.6pt against a 9.0pt body, all
  past the final section, none a section opener.

### Hazard B — hierarchy

The gloss/marginal-note column is where section titles live, and linear text
extraction pairs it to the wrong section (note leads its section on some pages,
trails it on others — confirmed on Penal ss.264-267 vs CrPC ss.47/146).

**Mechanism: detect columns per page, pair by geometry.** The body column is
identified by *content* — it is the x-cluster containing lines that open with a
section number; the narrower cluster is the gloss. Then each gloss block is
matched to the section whose opening line it overlaps vertically. This must be
detected, never hardcoded: CrPC keeps the gloss left throughout, while the Penal
Code mirrors it onto the outer margin, swapping sides every page.

Part/Chapter headings are full-width centred lines (`PART IV`, `CHAPTER XVI`)
carried as parser state, so each section inherits the Part/Chapter it falls
under.

**Front matter must be skipped** or contents pages become phantom sections —
they look exactly like real ones (`154. Information in cognizable cases`) but
carry no body. This cannot be decided page by page: a body page of short
definitional sections (Penal p37, ss.8-19, one sentence each) is indistinguishable
from a contents page by numbered-line ratio or line length, and two attempts at
per-page rules each failed — one leaving 13 contents pages in (~250 phantom
sections), the next throwing out p37 and losing ss.8-19 and 43-47. What works:
contents are always a **contiguous prefix**, so find the single page where the
body begins (a sustained run of prose-heavy pages) and cut once. Penal → p34,
Evidence → p10, CrPC → p0 (it has no contents pages at all).

## 3. Architecture: everything from the PDF, validated against an oracle

```
Laws/main/*.pdf
      │
      ▼
 PyMuPDF line extraction (coordinates + font size)
      │
      ├── furniture strip (content-based headers, proportional footnotes)
      ├── front-matter cut (contiguous prefix)
      ├── per-page column detect -> gloss / body / headings
      ├── ONE ordered stream -> sections (page-break safe)
      ├── gloss -> section by y-overlap
      └── sub-structure: stack-based (1) -> (a) -> (i) + typed blocks
      │
      ▼
 Laws/main/parsed/<act>.json   (hierarchical, self-auditing)
      │
      ├──► chunking: one chunk per section, ancestry prepended
      └──► [optional] Akoma Ntoso XML via bluebell

 bdlaws.jsonl ──► ORACLE: diff every parsed section's text (never a source)
```

Rationale for parsing text from the PDF rather than taking it from the oracle:
the PDF is the document being cited, so text and page number must come from the
same place or a citation can point at a page whose words differ. The oracle's
job is to make that text *checkable* — which is stronger than using it as a
source, because it catches parser regressions instead of hiding them.

Within a section, sub-structure is parsed from the text with a **stack keyed on
each open level's expected-next value**. That is what disambiguates `(i)`:
roman one, or the letter i after `(h)`? Whichever level is already expecting it
wins; only otherwise does a new level open.

Built and working in `backend/scripts/parse_law_pdf.py` — results in §4.

## 4. Steps

All of 1-5 are implemented in `backend/scripts/parse_law_pdf.py`; text and
structure both come from the PDF (no JSONL in the parse path).

1. **Extract + normalise** (done) — coordinate-aware line extraction, furniture
   stripping, front-matter skip, single ordered stream.
2. **Structure pass** (done) — column detection, gloss pairing by y-overlap,
   Part/Chapter state, page ranges.
3. **Section assembly** (done) — one ordered stream across all pages, so
   page-split sections reassemble. Text before the first section is captured as
   `preamble` rather than discarded.
4. **Sub-structure** (done) — stack-based subsection/clause/subclause nesting;
   Explanations, Illustrations, Exceptions, Provisos as typed blocks attached to
   the section.
5. **Validation gate** (done) — coverage invariant (`orphaned: 0`) and furniture
   audit ship inside every output file; oracle diffing is a separate pass.
6. **Chunking** (next) — one chunk per section, split at subsection boundaries
   when oversized, hierarchy path prepended to the embedded text. The parsed
   JSON already carries full ancestry inline on each section, so no tree walk.
7. **Extend to remaining acts** (next) — the other 25 in-scope PDFs. Geometry is
   auto-detected per page, but several come from a different source batch, so
   body-start and column detection need re-checking per act.

### Results (3 acts, validated against the oracle)

| act | sections | phantom | missing | titled | chapters | parts | C1 recovery | orphaned |
|---|---|---|---|---|---|---|---|---|
| CrPC | 508 | **0** | 7 | 502 | 42 | 9 | 99.99% | **0** |
| Penal Code | 557 | **0** | 1 | 540 | 23 | 0 | **100%** | **0** |
| Evidence Act | 182 | **0** | **0** | 170 | 11 | 3 | **100%** | **0** |

Sections spanning a page break — the case that defeated every per-page
parser — CrPC 148, Penal 87, Evidence 65, all reassembled.

Three TOC-detection bugs were found and fixed getting here, and the pattern is
worth recording: **every one was silent**, and the summary stats looked
plausible each time. Average-line-length left 13 contents pages in (~250
phantom sections); longest-line over-corrected and threw out a body page of
short definitional sections (losing ss.8-19 and 43-47); the fix was to stop
classifying pages individually and instead find the single point where the body
begins, since contents are always a contiguous prefix. Only diffing against the
oracle surfaced any of them.

## 5. Validation gate (what makes this defensible)

The JSONL gives us something rare: an **independent oracle for text**. So
correctness stops being a matter of eyeballing.

- **Text fidelity** — for every section, parsed PDF text vs JSONL text, after
  whitespace normalisation. Any section below threshold is flagged, not
  silently accepted. (Known caveat: our parser currently leaks footnote text
  into some sections — found on Evidence s.37 — which this gate catches.)
- **Coverage invariant** — every non-furniture line lands in a section, a gloss
  note, or a heading; anything else is counted as `orphaned` in every output
  file. **Currently 0 on all three acts.** This and the furniture audit are the
  checks that caught every silent-drop bug during development, so they ship
  inside the parser rather than living in a separate harness.
- **Structural invariants** — chapter count matches the known figure per act;
  section numbers ascend; no duplicate section numbers; every `null`-numbered
  row is re-attached to exactly one parent.
- **Furniture audit** — every dropped line is written to the report so a
  silent-drop regression is visible. Both silent-drop bugs found during the
  spike were mine, and both would have passed an eyeball test.

## 6. Output

`Laws/main/parsed/{crpc,penal,evidence}.json`, one self-contained file per act:

- `act` — name, act number, source PDF (provenance for every citation)
- `preamble` — enacting text before s.1
- `stats` — the self-audit, incl. `orphaned` (must be 0)
- `columns_per_page` — body/gloss x-positions and which side the gloss sat on,
  page by page; the audit trail for mirrored spreads
- `dropped_as_furniture` — every removed header/footnote with page, y, size, text
- `chapters[]` → `sections[]` → `children[]`

Each section carries `section_number`, `section_title`, `part`, `part_title`,
`chapter`, `chapter_title`, `page_start`, `page_end`, `spans_page_break`,
verbatim `text`, and nested `children`. Ancestry is inline on every section, so
retrieval needs no tree walk and citations are already page-level.

## 7. Known gaps

- **CrPC Second Schedule is in neither source.** Traced the PDF to its end: it
  stops after s.565 and runs footnotes to the last page — no offence table. The
  JSONL has 0 schedule rows. That table answers "is this offence cognizable /
  bailable / compoundable?", among the most common practical CrPC questions.
  Needs separate sourcing from bdlaws.
- **Footnote→section linkage** not yet built. The parser strips footnotes and
  logs them in `dropped_as_furniture`, but does not yet match `N[` markers to
  the footnote text explaining that amendment. CrPC carries 499 such brackets.
- **7 CrPC sections missing** (138, 205A, 265I, 432, 438, 478, 99A) and Penal
  s.59, which the PDF prints as the range `58.- 59.` and the parser folds into
  s.58 (flagged via `section_number_to`).
- **CrPC chapter count is 42**, against 52 CHAPTER matches in the raw PDF text
  survey. Some of those are cross-references rather than headings, but the real
  total should be confirmed.
- **Untitled sections**: CrPC 6, Penal 20, Evidence 13 — mostly
  `[Repealed]`/`[Omitted]` stubs that carry no gloss note in the source.
- **Only 3 of 34 PDFs parsed.** 25 in-scope acts remain, plus 6 duplicate
  copies that should be removed first (step 1 in plan.md, still outstanding).
- Licence on the HF oracle dataset is stated as "unknown". The underlying
  statute text is government material, but confirm before anything ships.


## Ingesting a PDF that is not one of the three acts

The parser takes any legal PDF. Structure detection is content-based — section
numbering, font size relative to the page's body text, column geometry — and
carries no per-act knowledge, so an unseen act goes through the same path.

```bash
python scripts/parse_law_pdf.py --pdf "Laws/The Arms Act, 1878.pdf" \
    --name "The Arms Act, 1878" --act-no "XI of 1878" --slug arms
python scripts/verify/verify_parsed.py          # then chunk_sections.py, build_index.py
```

Chunking and indexing discover whatever is in `Laws/main/parsed/`, so nothing
downstream needs editing to add an act.

Verified on the Arms Act 1878 (unseen, 17 pages): 32 sections, 30 titled, 8
spanning a page break, 609 of 609 lines placed, 0 orphaned.

### Choosing the extractor

`--backend auto` (the default) measures extractable text per page and picks:

| | |
|---|---|
| >= 120 chars/page | PyMuPDF — native text |
| < 120 chars/page | Docling — the page is an image, so OCR is the only option |

Every PDF in this corpus measures 1,000-2,400 chars/page, so Docling never
fires on them. That threshold exists for scanned statutes, where PyMuPDF
extracts nothing and would otherwise produce an empty act **without failing** —
every rule downstream keys off text, so no text means no sections rather than an
error.

**Docling remains the worse parser wherever PyMuPDF works.** Forced onto the
first 6 pages of the Borstal Schools Act:

| backend | lines | section starts |
|---|---|---|
| PyMuPDF | 247 | 13 (ss.1-13) |
| Docling | 61 | 5 (ss.9-13 only) |

Three quarters of the lines gone, and the first eight sections with them —
the same failure this table rejected it for. It is a fallback for the case where
the alternative is nothing, not a second opinion. OCR geometry is also too
unreliable for the gloss-column detection, so scanned documents are treated as
single-column.
