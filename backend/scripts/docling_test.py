"""Docling spike (pdf-parsing-options.md) — parse just the first N pages of
one Act and inspect the output before deciding whether Docling replaces the
custom coordinate-based column-matching step.

Slices the PDF down to the first N pages first (via PyMuPDF) so Docling's
layout model only has to run over a small sample while we're iterating.

Run (from backend/, ainshonglap conda env active):

    python scripts/docling_test.py
"""

from __future__ import annotations

import pathlib

import pymupdf
from docling.document_converter import DocumentConverter

LAWS_DIR = pathlib.Path(__file__).resolve().parents[2] / "Laws"
SOURCE_PDF = LAWS_DIR / "The Code of Criminal Procedure, 1898 (bdcode.gov.bd copy).pdf"
NUM_PAGES = 20

OUT_DIR = pathlib.Path(__file__).resolve().parent / "docling_out"


def make_slice(source: pathlib.Path, num_pages: int, out_path: pathlib.Path) -> None:
    doc = pymupdf.open(source)
    sliced = pymupdf.open()
    sliced.insert_pdf(doc, from_page=0, to_page=num_pages - 1)
    sliced.save(out_path)
    print(f"Sliced first {num_pages} pages -> {out_path}")


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    slice_path = OUT_DIR / "crpc_first20.pdf"
    make_slice(SOURCE_PDF, NUM_PAGES, slice_path)

    converter = DocumentConverter()
    result = converter.convert(str(slice_path))
    doc = result.document

    md = doc.export_to_markdown()
    md_path = OUT_DIR / "crpc_first20.md"
    md_path.write_text(md, encoding="utf-8")
    print(f"Markdown -> {md_path} ({len(md)} chars)")

    json_path = OUT_DIR / "crpc_first20.json"
    doc.save_as_json(json_path)
    print(f"JSON -> {json_path}")

    print("\n--- markdown preview (first 3000 chars) ---\n")
    print(md[:3000])


if __name__ == "__main__":
    main()
