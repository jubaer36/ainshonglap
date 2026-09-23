"""Add a new document: parse it, show a person the result, let them choose.

Two parsers, built for different documents:

    PyMuPDF   parse_law_pdf.py — numbered statutes. Very accurate on Acts laid
              out like the three already served; on anything else it finds no
              sections and puts the whole text in `preamble`, where nothing
              downstream ever reads it.
    Docling   parse_docling_document.py — any document, by page layout:
              headings, paragraphs, lists, tables. Cited by passage, not section.

Which one fits is judged by a person, not guessed by a rule. A rule would have
to know what "parsed properly" means for a document it has never seen, and the
failure it must catch is the silent kind: text that is all present, but in the
wrong place. So:

    1. REVIEW   python scripts/ingest_document.py --pdf <file> --slug <slug>

       Parses the whole document with PyMuPDF, picks a few pages spread through
       it, runs Docling on just those pages, and writes one HTML page showing,
       for each sampled page, the page image beside what each parser made of
       it. Nothing is written to the corpus.

    2. CHOOSE   answer the prompt, or run again with --use pymupdf|docling

       pymupdf  saves the parse already made
       docling  parses the WHOLE document with Docling, then saves that

    Saved to Laws/main/parsed/<slug>.json, marked with `parser` and `doc_type`.
    Then the usual pipeline: chunk, index, catalog, load, verify — printed at
    the end.

The review lives in Laws/review/<slug>/ (outside Laws/main, so it is not
tracked). Sampling is seeded by the slug, so the same document shows the same
pages every time unless --seed says otherwise.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import logging
import pathlib
import random
import re
import sys

import pymupdf

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "scripts"))

import parse_docling_document as docling_doc  # noqa: E402
import parse_law_pdf as statute  # noqa: E402

LAWS = statute.LAWS                      # Laws/main
REVIEW = LAWS.parent / "review"          # Laws/review — untracked
PREVIEW_CHARS = 1500

# Docling's OCR engine logs a page of setup advice on every run. None of it
# bears on the review, and it buries the prompt.
for noisy in ("RapidOCR", "docling", "docling_core"):
    logging.getLogger(noisy).setLevel(logging.WARNING)


# ------------------------------------------------------------------ sampling

def sample_pages(pdf: pathlib.Path, n: int, seed: str) -> list[int]:
    """`n` pages spread through the document: one from each equal slice.

    Spread rather than uniform, because the failures differ by region — front
    matter and contents at the start, schedules and endnotes at the end, the
    body in between. Pages with no text layer are still eligible: a scan is
    exactly what a person should see.
    """
    doc = pymupdf.open(pdf)
    total = len(doc)
    rng = random.Random(seed)
    if total <= n:
        return list(range(total))
    bounds = [round(i * total / n) for i in range(n + 1)]
    return sorted(rng.randrange(bounds[i], max(bounds[i] + 1, bounds[i + 1]))
                  for i in range(n))


def page_image(pdf: pathlib.Path, page: int, dpi: int = 100) -> str:
    """The page as a base64 PNG, so the review is one self-contained file."""
    pix = pymupdf.open(pdf)[page].get_pixmap(dpi=dpi)
    return base64.b64encode(pix.tobytes("png")).decode()


# ------------------------------------------------------------------ what each parser made of a page

def _sections(res: dict) -> list[dict]:
    return [s for ch in res.get("chapters", []) for s in ch.get("sections", [])]


def on_page(res: dict, page: int) -> list[dict]:
    """Units that start on or run across `page`."""
    return [s for s in _sections(res) if s["page_start"] <= page <= s["page_end"]]


def order_rate(res: dict) -> float | None:
    """Share of sections whose number is not lower than the one before.

    A statute read correctly climbs 1, 2, 3 … 302, 302A, 303. A document the
    number rule does not fit either finds nothing (None) or picks up numbers in
    running text, which jump about.
    """
    nums = []
    for s in _sections(res):
        m = re.match(r"\d+", str(s["section_number"]))
        if m:
            nums.append(int(m.group()))
    if len(nums) < 2:
        return None
    return sum(1 for a, b in zip(nums, nums[1:]) if b >= a) / (len(nums) - 1)


def summary(res: dict) -> dict:
    secs = _sections(res)
    in_units = sum(len(s["text"]) for s in secs)
    pre = len(res.get("preamble") or "")
    return {"units": len(secs), "in_units_share": in_units / max(1, in_units + pre),
            "preamble_chars": pre, "in_order": order_rate(res)}


# ------------------------------------------------------------------ the review page

def _unit_html(s: dict, unit: str) -> str:
    title = f" — {html.escape(s['section_title'])}" if s.get("section_title") else ""
    text = s["text"]
    more = len(text) > PREVIEW_CHARS
    kids = f'<div class="meta">{len(s["children"])} subsection/clause(s) nested</div>' \
        if s.get("children") else ""
    return (f'<div class="unit"><div class="head">{unit} {html.escape(str(s["section_number"]))}'
            f'{title}<span class="pages">p.{s["page_start"] + 1}'
            f'{"–" + str(s["page_end"] + 1) if s["page_end"] != s["page_start"] else ""}</span></div>'
            f'<pre>{html.escape(text[:PREVIEW_CHARS])}{" …" if more else ""}</pre>{kids}</div>')


def column_statute(res: dict | None, err: str | None, page: int) -> str:
    if err:
        return f'<div class="bad">PyMuPDF failed: {html.escape(err)}</div>'
    units = on_page(res, page)
    out = [_unit_html(s, "Section") for s in units]
    if not units:
        out.append('<div class="bad">No section starts or continues on this page.</div>')
        first = min((s["page_start"] for s in _sections(res)), default=None)
        if res.get("preamble") and (first is None or page < first):
            out.append('<div class="meta">This page\'s text went to <b>preamble</b>, which is '
                       'not searched — right for a title or contents page, wrong if this page '
                       'is part of the body:</div>'
                       f'<pre>{html.escape(res["preamble"][:PREVIEW_CHARS])}</pre>')
    dropped = [d for d in res.get("dropped_as_furniture", []) if d["page"] == page]
    if dropped:
        out.append('<div class="meta">Removed as header/footnote on this page:</div><pre class="dim">'
                   + html.escape("\n".join(d["text"] for d in dropped[:12])) + "</pre>")
    return "".join(out)


def column_docling(res: dict | None, err: str | None) -> str:
    if err:
        return f'<div class="bad">Docling failed: {html.escape(err)}</div>'
    units = _sections(res)
    if not units:
        return '<div class="bad">Docling found no text on this page.</div>'
    return "".join(_unit_html(s, "Passage") for s in units)


def banner(name: str, stats: dict | None) -> str:
    if stats is None:
        return f'<div class="bad">{name}: no result</div>'
    order = "n/a" if stats["in_order"] is None else f'{stats["in_order"]:.0%}'
    return (f'<div class="stat"><b>{name}</b> — {stats["units"]} sections · '
            f'{stats["in_units_share"]:.0%} of text inside sections · '
            f'{stats["preamble_chars"]:,} chars left in preamble · numbering in order: {order}</div>')


CSS = """
body{font:14px/1.5 system-ui,sans-serif;margin:0;background:#f6f6f4;color:#1b1b1b}
header{padding:16px 24px;background:#1b1b1b;color:#fff}
header h1{font-size:18px;margin:0 0 6px}
.stat{margin:3px 0;font-size:13px}.stat b{color:#fff}
.hint{padding:12px 24px;background:#fff8e1;border-bottom:1px solid #e8d9a8;font-size:13px}
.page{margin:18px 24px;background:#fff;border:1px solid #ddd;border-radius:8px;overflow:hidden}
.page>h2{margin:0;padding:10px 14px;font-size:14px;background:#efefec;border-bottom:1px solid #ddd}
.cols{display:grid;grid-template-columns:minmax(0,1.1fr) minmax(0,1fr) minmax(0,1fr)}
.col{padding:12px;border-right:1px solid #eee;min-width:0}.col:last-child{border-right:0}
.col h3{margin:0 0 8px;font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:#666}
.col img{width:100%;border:1px solid #ccc}
.unit{margin-bottom:10px;border:1px solid #e3e3e3;border-radius:6px}
.head{padding:5px 8px;background:#f3f3f1;font-weight:600;font-size:12.5px}
.pages{float:right;font-weight:400;color:#777}
pre{white-space:pre-wrap;word-break:break-word;margin:0;padding:8px;font:12.5px/1.45 ui-monospace,monospace}
.dim{color:#888}.meta{font-size:12px;color:#555;padding:4px 8px}
.bad{padding:8px;background:#fdecec;color:#8a1c1c;border-radius:6px;margin-bottom:8px;font-size:13px}
@media (max-width:900px){.cols{grid-template-columns:1fr}}
"""


def write_review(pdf: pathlib.Path, slug: str, pages: list[int], statute_res, statute_err,
                 previews: dict[int, tuple]) -> pathlib.Path:
    out = REVIEW / slug
    out.mkdir(parents=True, exist_ok=True)
    blocks = []
    for p in pages:
        d_res, d_err = previews[p]
        blocks.append(
            f'<section class="page"><h2>Page {p + 1}</h2><div class="cols">'
            f'<div class="col"><h3>The page</h3><img src="data:image/png;base64,{page_image(pdf, p)}"></div>'
            f'<div class="col"><h3>PyMuPDF (statute parser)</h3>{column_statute(statute_res, statute_err, p)}</div>'
            f'<div class="col"><h3>Docling (layout parser)</h3>{column_docling(d_res, d_err)}</div>'
            "</div></section>")
    doc = (f"<!doctype html><meta charset='utf-8'><title>Parse review · {html.escape(slug)}</title>"
           f"<style>{CSS}</style><header><h1>{html.escape(pdf.name)}</h1>"
           f"{banner('PyMuPDF, whole document', summary(statute_res) if statute_res else None)}"
           f"<div class='stat'>Docling: shown for the sampled pages only — choosing it parses the whole document.</div></header>"
           "<div class='hint'>For each page, compare the image with each column. Missing text, text "
           "under the wrong heading, headers or footnotes mixed into the body, or a statute's numbered "
           "sections not recognised are all reasons to reject a parser. Then answer the prompt in the "
           f"terminal, or run <code>ingest_document.py … --use pymupdf|docling</code>.</div>"
           + "".join(blocks))
    path = out / "review.html"
    path.write_text(doc, encoding="utf-8")
    return path


# ------------------------------------------------------------------ saving the choice

def save(res: dict, slug: str, parser: str, doc_type: str) -> pathlib.Path:
    res = {**res, "parser": parser, "doc_type": doc_type}
    statute.OUT.mkdir(parents=True, exist_ok=True)
    path = statute.OUT / f"{slug}.json"
    path.write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def next_steps(slug: str) -> str:
    return (f"\nNext, to make it searchable:\n"
            f"  python scripts/chunk_sections.py --act {slug}\n"
            f"  python scripts/verify/verify_chunks.py\n"
            f"  python scripts/build_index.py\n"
            f"  python scripts/build_index.py --embedder nemotron-3-embed   # OpenRouter, new chunks only\n"
            f"  python scripts/build_catalog.py && python scripts/verify/verify_catalog.py\n"
            f"  python scripts/load_supabase.py --all && python scripts/verify/verify_supabase.py\n"
            f"  python scripts/load_supabase.py --embedder nemotron-3-embed --all\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--pdf", required=True, help="the document; ideally placed in Laws/main/")
    ap.add_argument("--slug", required=True, help="citation key prefix, e.g. arms")
    ap.add_argument("--name", help="title used in citations (default: the filename)")
    ap.add_argument("--act-no", default="", help='e.g. "XI of 1878"; empty for non-statutes')
    ap.add_argument("--pages", type=int, default=4, help="how many pages to sample")
    ap.add_argument("--seed", default=None, help="sampling seed (default: the slug)")
    ap.add_argument("--use", choices=("pymupdf", "docling"),
                    help="skip the review and save this parser's result")
    args = ap.parse_args()

    pdf = pathlib.Path(args.pdf)
    if not pdf.is_absolute():
        pdf = (LAWS / pdf) if (LAWS / pdf).exists() else pdf.resolve()
    if not pdf.exists():
        raise SystemExit(f"no such PDF: {pdf}")
    if LAWS not in pdf.parents:
        print(f"  note: {pdf.name} is not in Laws/main/. Citations refer to the source PDF "
              "by name, so move it there before serving it.")
    name = args.name or re.sub(r"\.pdf$", "", pdf.name, flags=re.I)
    staged = REVIEW / args.slug / "pymupdf.json"

    def run_statute():
        try:
            return statute.parse(args.slug, pdf=str(pdf), act_name=name,
                                 act_no=args.act_no, backend="pymupdf"), None
        except (Exception, SystemExit) as exc:          # a scan has no text layer
            return None, f"{type(exc).__name__}: {exc}"

    choice = args.use
    if choice is None:
        print(f"Parsing {pdf.name} with PyMuPDF …")
        statute_res, statute_err = run_statute()
        if statute_res:
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_text(json.dumps(statute_res, ensure_ascii=False), encoding="utf-8")

        pages = sample_pages(pdf, args.pages, args.seed or args.slug)
        print(f"Running Docling on sampled pages {[p + 1 for p in pages]} …")
        from docling.document_converter import DocumentConverter

        converter = DocumentConverter()
        previews = {}
        for p in pages:
            try:
                d = converter.convert(str(pdf), page_range=(p + 1, p + 1)).document
                previews[p] = (docling_doc.to_parsed(d, name, args.act_no, pdf.name), None)
            except Exception as exc:
                previews[p] = (None, f"{type(exc).__name__}: {exc}")

        path = write_review(pdf, args.slug, pages, statute_res, statute_err, previews)
        print(f"\nReview: {path}\n  (open it in a browser: file://{path})")
        if statute_res:
            s = summary(statute_res)
            order = "n/a" if s["in_order"] is None else f"{s['in_order']:.0%}"
            print(f"  PyMuPDF: {s['units']} sections, {s['in_units_share']:.0%} of text inside "
                  f"sections, numbering in order {order}")
        if not sys.stdin.isatty():
            print("\nThen run again with --use pymupdf or --use docling.")
            return
        answer = input("\nUse [p]yMuPDF, [d]ocling, or [q]uit? ").strip().lower()[:1]
        choice = {"p": "pymupdf", "d": "docling"}.get(answer)
        if choice is None:
            print("Nothing saved.")
            return

    if choice == "pymupdf":
        if staged.exists():
            res = json.loads(staged.read_text(encoding="utf-8"))
        else:
            res, err = run_statute()
            if err:
                raise SystemExit(f"PyMuPDF could not parse it: {err}")
        if not _sections(res):
            raise SystemExit("PyMuPDF found no sections in this document — saving it would "
                             "make it unsearchable. Use --use docling.")
        path = save(res, args.slug, "pymupdf", "statute")
    else:
        print(f"Parsing the whole of {pdf.name} with Docling … (slower: it reads the layout of every page)")
        res = docling_doc.to_parsed(docling_doc.convert(pdf), name, args.act_no, pdf.name)
        if not _sections(res):
            raise SystemExit("Docling found no text in this document either. Nothing saved.")
        path = save(res, args.slug, "docling", "document")

    n = len(_sections(res))
    print(f"\nSaved {path}  ({n} {'sections' if choice == 'pymupdf' else 'passages'}, parser: {choice})")
    print(next_steps(args.slug))


if __name__ == "__main__":
    main()
