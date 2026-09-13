# PDF Parsing: Options Considered and Why

Scope: parsing `Laws/*.pdf` (CrPC 1898, Penal Code 1860, Evidence Act 1872,
and related Bangladesh criminal-law acts) into hierarchical, citation-grade
JSON (Act → Part → Chapter → Section → Subsection → Clause).

## What we actually know about these PDFs (checked, not assumed)

Before picking a tool, we opened the actual files (text extraction +
rendered page screenshots, see conversation history). This is what decides
which options below are even relevant:

- **Native digital text, not scans.** `pypdf`/`PyMuPDF` extract clean text
  with zero OCR garbling, verified on CrPC, Penal Code, Evidence Act. → any
  OCR-first tool is solving a problem we don't have.
- **Genuine two-column layout, but an unusual kind**: a narrow left
  "gloss" column carrying a short bold heading (e.g. *"Punishment for
  attempting to commit offences..."*), and a wide right column with
  `<bold section number>. <body text>`. This is a glossary/marginal-note
  layout, not the side-by-side running-text columns (like a two-column
  academic paper) that most layout models are benchmarked on.
- Plain linear text extraction pairs each note to its section
  **inconsistently** — sometimes the note lands before its section number,
  sometimes after, depending on the page (confirmed on Penal Code
  ss.264–267 vs. ss.47/146). Reading-order-based tools can't be trusted
  blindly here; whatever we use has to key off actual coordinates for the
  note↔section match, not stream order.
- Footnotes (amendment history) sit below a horizontal rule at the
  page bottom, numbered continuously across the whole document (not
  per-page), in a smaller font.
- Sub-lists nest genuinely (numeric `(1)` containing lettered `(a)(b)(c)`,
  with trailing text after the list closes belonging back to the parent) —
  confirmed on Penal Code S.263A.
- `Illustrations`/`Explanation` blocks are italic, centered sub-headings
  **inside the body column**, not in the gloss column.
- Front-matter TOC pages exist and look enough like real sections
  (`154. Information in cognizable cases` with no body) to get
  mis-parsed as duplicate stub records if not explicitly skipped — this is
  very likely why the external reference dataset we compared against
  (`bangladesh-legal-qa-dataset`) has ~20 duplicate section keys and ~11
  empty stub entries per file.

None of this needs OCR. All of it needs **coordinate-aware** extraction,
because the hazard is column association, not character recognition.

## Options, kept vs. cut

### Kept — extraction layer

| Tool | Verdict | Reasoning |
|---|---|---|
| **PyMuPDF** (`fitz`/`pymupdf`) | **Primary, non-negotiable** | Already in use; gives per-character/line bounding boxes (`get_text("dict")`), which is exactly what's needed to cluster lines into the gloss column vs. body column by `x0` and match a note to its section by `y`-range overlap — the specific hazard our own inspection found. Free, fast, no GPU, actively maintained. |
| **PyMuPDF4LLM** | Worth a spike, not primary | Layered on PyMuPDF; converts to Markdown and detects headers by **font size**, with an "IdentifyHeaders" mechanism you can override ([PyMuPDF docs](https://pymupdf.readthedocs.io/en/latest/pymupdf4llm/index.html)). Our gloss-column headings are **bold, not necessarily larger** — font-size-keyed detection likely won't catch them reliably, and it doesn't natively solve the two-column note/body pairing problem either. Cheap to trial since it's the same underlying library; not something to build the pipeline around. |

### Kept — worth a comparison spike before committing to fully custom code

| Tool | Verdict | Reasoning |
|---|---|---|
| **Docling** (IBM Research, now under the [Linux Foundation's Agentic AI Foundation](https://docling.org/about/)) | Spike candidate | Apache-2.0, actively developed (37k+ GitHub stars in its first year), outputs structured JSON/Markdown with reading-order recovery and layout preservation. Its layout model is trained on a broad document mix, not specifically glossary-margin legal codes — genuinely unknown whether it resolves our gloss-column pairing correctly until tested. Worth a 30-minute trial on one PDF before writing more custom code, since if it works it saves the column-matching logic entirely. |
| **Marker** ([datalab-to/marker](https://github.com/datalab-to/marker)) | Lower-priority spike | Apache-2.0 code (model weights under a research-friendly OpenRail-M variant), benchmarks reasonably on `olmocr-bench` including multi-column layouts. Runs a full OCR pipeline (Surya) even on digital-native PDFs, which is unnecessary overhead for text we can already extract cleanly — try Docling first. |

### Kept — primary structuring approach

| Approach | Verdict | Reasoning |
|---|---|---|
| **Custom regex/rule-based legal-structure parser on top of PyMuPDF coordinates** | **Primary plan** | Bangladesh statutes follow a predictable, finite grammar: `PART`/`CHAPTER` headings, `\d+[A-Z]{0,2}\.` section starts, `(1)(2)(3)` / `(a)(b)(c)` / `(i)(ii)(iii)` nested lists, bracketed amendment substitutions. A deterministic parser means every classification (*"this is Section 54, not 54A"*) is traceable to an explicit rule — this matters directly for a tool whose job is citation accuracy. If Docling/Marker's generic layout output turns out reliable for column separation, we'd still run our own regex structuring on top of their cleaner text rather than trusting a generic model's semantic tagging for section boundaries. |

### Cut for now

| Option | Why cut |
|---|---|
| **OCR tools** (Tesseract, PaddleOCR, OCRmyPDF, DeepSeek-OCR, dots.ocr, MinerU's OCR mode) | Our PDFs are confirmed native text, not scans — OCR adds latency, a new failure surface (misread digits in section numbers are a *citation-correctness* bug, not a cosmetic one), and zero benefit here. Revisit only if a specific act we need is unavailable except as a scan. |
| **Unstructured.io (`hi_res`)** | Real capability (Detectron2/YOLOX layout detection, Title/NarrativeText/ListItem tagging with bounding boxes — [docs](https://docs.unstructured.io/open-source/concepts/models)), but it's tuned for generic messy documents (forms, mixed file types) and needs a heavier install (layout models, optional GPU) for a problem — clean text — we don't have. Docling/Marker cover the same "try a generic parser first" spike more cheaply. |
| **Cloud APIs** (LlamaParse, Azure Document Intelligence, AWS Textract, Google Document AI, Reducto) | Cost-per-page for documents that are already clean, digital, and freely licensed government text — there's no messy-scan or complex-table problem here that justifies sending statute text to a third party and paying for it. None of them have anything specific for gloss-margin legislative layout that beats a coordinate-based custom pass. Keep as a documented fallback only if every open-source path fails on some specific PDF. |
| **LLM/VLM as the primary structure extractor** | Wrong tool for a legal citation system: *"looks like Section 54"* with no deterministic check is a hallucination risk on the exact thing (section numbers, citations) that must never be wrong. Kept only as a **bounded fallback** — invoked solely when the deterministic parser flags a page as ambiguous (e.g. column association failed, or a bracket didn't close), and even then its output gets validated against the source substring rather than trusted outright. |

## Recommended pipeline (phased, matches plan.md's step 2/3)

```
PDF (PyMuPDF, per-page coordinates)
  → column split (gloss note vs. body) by x0, matched by y-range overlap
  → Part/Chapter heading detection (centered, full-width, bold caps)
  → section/subsection/clause structuring (regex + stack-based nesting,
     see nested-list design already agreed)
  → footnote block isolation (below page-bottom rule) → bracket resolution
  → TOC-page filter (skip before first real section body)
  → hierarchical JSON tree (Act → Part → Chapter → Section → Subsection)
  → flattened retrieval records (one per leaf, full ancestry in metadata)
```

Spike Docling on one PDF first (cheap, ~30 min) — if it cleanly separates
the gloss column from body text, it replaces the coordinate-clustering step
above and we still run our own regex structuring on its output. If it
doesn't, we already have the coordinate-based design worked out and lose
nothing by having tried.

## Target schema

Tree (source of truth, one file per Act):

```json
{
  "act": "The Code of Criminal Procedure, 1898",
  "act_number": "V of 1898",
  "children": [
    {
      "type": "chapter", "number": "V", "title": "Of Arrest, Escape and Retaking",
      "children": [
        {
          "type": "section", "number": "54", "title": "Arrest without warrant",
          "children": [
            {"type": "subsection", "number": "1", "text": "...", "children": [
              {"type": "clause", "number": "a", "text": "..."}
            ]}
          ]
        }
      ]
    }
  ]
}
```

Flattened retrieval record (what actually gets embedded/indexed):

```json
{
  "id": "crpc_1898_ch5_s54_sub1",
  "text": "Code of Criminal Procedure, 1898 > Chapter V > Section 54: Arrest without warrant > Subsection (1)\n\n[actual text]",
  "metadata": {
    "act": "The Code of Criminal Procedure, 1898",
    "act_number": "V of 1898",
    "chapter": "V",
    "section": "54",
    "subsection": "1",
    "clause": null,
    "page_start": 32,
    "page_end": 32,
    "source_pdf": "The Code of Criminal Procedure, 1898 (bdcode.gov.bd copy).pdf"
  }
}
```

Prepending the hierarchy path to the embedded text (not just storing it as
metadata) is deliberate — it gives the embedding model context that a bare
"Any police-officer may arrest..." string doesn't carry on its own.

This is close to, but not identical to, the schema used by the external
`bangladesh-legal-qa-dataset` corpus we already inspected — same core
fields (Act/Chapter/Section/Subsection), but we add page numbers and
source-file provenance for citation display, and use a proper recursive
`children` field instead of flat `Subsections`/`Clauses` lists, since we
confirmed real nesting exists (S.263A) that a flat list can't represent.

## Sources

- [Docling.org — About](https://docling.org/about/)
- [ibm-granite/granite-docling-258M on Hugging Face](https://huggingface.co/ibm-granite/granite-docling-258M)
- [datalab-to/marker on GitHub](https://github.com/datalab-to/marker)
- [Unstructured — Models docs](https://docs.unstructured.io/open-source/concepts/models)
- [PyMuPDF4LLM docs](https://pymupdf.readthedocs.io/en/latest/pymupdf4llm/index.html)
