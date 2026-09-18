"""LandingAI ADE (Agentic Document Extraction) spike.

A different class of parser from the others tried: a hosted VLM that returns
Markdown plus a structure tree with per-element grounding (page + bbox), rather
than either raw coordinates (PyMuPDF) or a local layout model (Docling).

The two things to judge it on are the same two that decided every other option:
  1. does it pair the gloss/marginal note with the right section?
  2. does it rejoin text split across a page boundary?

Slices N pages out first so a spike costs a few pages of credit, not 217.

Run (from backend/, ainshonglap conda env active):

    python scripts/landingai_test.py --start 108 --pages 5
    python scripts/landingai_test.py --full          # whole Penal Code
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import time

import pymupdf
from dotenv import load_dotenv

LAWS = pathlib.Path(__file__).resolve().parents[2] / "Laws"
OUT = pathlib.Path(__file__).resolve().parent / "landingai_out"
PDF = LAWS / "The Penal Code, 1860.pdf"


def slice_pdf(src: pathlib.Path, start: int, pages: int, dst: pathlib.Path) -> pathlib.Path:
    doc = pymupdf.open(src)
    out = pymupdf.open()
    out.insert_pdf(doc, from_page=start, to_page=min(start + pages, len(doc)) - 1)
    out.save(dst)
    print(f"sliced pages {start}-{start + pages - 1} -> {dst.name}")
    return dst


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=108)
    ap.add_argument("--pages", type=int, default=5)
    ap.add_argument("--full", action="store_true", help="parse the entire PDF")
    args = ap.parse_args()

    load_dotenv(".env.local", override=True)
    key = os.environ.get("LANDINGAI_API_KEY")
    if not key:
        raise SystemExit("LANDINGAI_API_KEY not set (expected in backend/.env.local)")

    from landingai_ade import LandingAIADE

    client = LandingAIADE(apikey=key)
    OUT.mkdir(exist_ok=True)

    if args.full:
        target, tag = PDF, "penal_full"
    else:
        tag = f"penal_p{args.start}-{args.start + args.pages - 1}"
        target = slice_pdf(PDF, args.start, args.pages, OUT / f"{tag}.pdf")

    print(f"submitting {target.name} ({target.stat().st_size/1e6:.1f} MB) ...")
    t0 = time.time()
    job = client.v2.parse_jobs.create(
        document=target,
        model="dpt-3-pro-latest",
        service_tier="standard",
    )
    print(f"  job_id={job.job_id}  polling...")
    done = client.v2.parse_jobs.wait(job.job_id, timeout=3600)
    print(f"  status={done.status}  elapsed={time.time()-t0:.0f}s")

    res = done.result
    (OUT / f"{tag}.md").write_text(res.markdown or "", encoding="utf-8")
    (OUT / f"{tag}.json").write_text(res.model_dump_json(indent=2), encoding="utf-8")
    print(f"  markdown -> {tag}.md ({len(res.markdown or '')} chars)")
    print(f"  full     -> {tag}.json")

    print("\n--- markdown preview ---\n")
    print((res.markdown or "")[:3000])


if __name__ == "__main__":
    main()
