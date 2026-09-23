"""Adding and removing sources while the app is running.

The same steps as the command-line pipeline — parse, review, chunk, embed,
catalog, load, verify — run as background jobs the frontend can start, watch
and steer. Nothing here is a second implementation: every step calls the
script that already does it (`parse_law_pdf`, `parse_docling_document`,
`chunk_sections`, `build_index`, `build_catalog`, `load_supabase`), so the
command line and the app cannot drift apart.

**Core Acts cannot be removed.** The Code of Criminal Procedure, the Penal
Code and the Evidence Act are the corpus the assistant is built around;
`config.CORE_ACTS` names them, and removal refuses them here — not just in the
UI, since an API call does not go through the UI.

**One job at a time.** Jobs run on a single worker thread, in order. Two index
rebuilds overlapping would each write a half-finished index over the other.

**Adding is two steps, with a person in between.** `start_add` parses with
PyMuPDF and previews Docling on a few sampled pages; the job then waits in
`review` until someone looks at the pages and calls `choose`. That is the same
review `scripts/ingest_document.py` writes as HTML, delivered as data instead.

Embedding happens in this process, through the model the API already has
loaded — no second copy on the GPU — and only for new text: `build_index`
reuses the vector of every chunk it has seen before. Adding one Act embeds that
Act; removing one embeds nothing.

Jobs are held in memory. A restart forgets them, which loses nothing that
matters: the corpus itself is either fully loaded (it has a commit marker in
`law_loads`) or it is not.
"""

from __future__ import annotations

import base64
import json
import pathlib
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from app.rag import config

SCRIPTS = config.BACKEND / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def manageable() -> str | None:
    """Why this deployment cannot add or remove sources, or None if it can.

    Adding runs the whole offline pipeline — parse (PyMuPDF and Docling), chunk,
    embed, catalog, load — from `backend/scripts/`. The deployed container ships
    none of that on purpose (backend/Dockerfile): it serves the corpus from
    Supabase, and sources are added on a machine that has the pipeline, then
    loaded, and picked up by every deployment within a minute. Without this
    check an admin's click would end in an ImportError halfway through a job.
    `RAG_SOURCES=0` switches it off explicitly.
    """
    import importlib.util
    import os

    if os.getenv("RAG_SOURCES", "1") == "0":
        return "source management is switched off here (RAG_SOURCES=0)"
    if not (SCRIPTS / "ingest_document.py").exists():
        return "this deployment does not include the ingestion pipeline"
    for module, what in (("fitz", "PyMuPDF"), ("docling", "Docling")):
        if importlib.util.find_spec(module) is None:
            return f"this deployment does not include {what}"
    return None

REVIEW = config.LAWS.parent / "review"
REMOVED = config.LAWS.parent / "removed"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
SAMPLE_PAGES = 4

_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ingest")
_jobs: dict[str, "Job"] = {}
_lock = threading.Lock()


class Refused(ValueError):
    """A request the corpus rules do not allow — reported to the caller as-is."""


@dataclass
class Job:
    id: str
    kind: str                     # "add" | "remove"
    slug: str
    name: str
    status: str = "queued"        # queued | reviewing | review | building | done | failed | discarded
    log: list[str] = field(default_factory=list)
    error: str | None = None
    review: dict | None = None
    created: float = field(default_factory=time.time)
    # Private to the job: where the upload was saved, and the PyMuPDF parse.
    pdf: str | None = None
    act_no: str = ""
    staged: dict | None = None

    def say(self, line: str) -> None:
        self.log.append(line)
        from app.rag import trace

        trace.step("ingest", f"{self.slug}: {line}")

    def public(self, with_review: bool = True) -> dict:
        out = {"id": self.id, "kind": self.kind, "slug": self.slug, "name": self.name,
               "status": self.status, "log": self.log[-40:], "error": self.error}
        if with_review and self.review is not None:
            out["review"] = self.review
        return out


def is_core(slug: str) -> bool:
    return slug.strip().lower() in config.CORE_ACTS


def get(job_id: str) -> Job | None:
    return _jobs.get(job_id)


def _submit(job: Job, fn) -> Job:
    with _lock:
        _jobs[job.id] = job

    def run():
        try:
            fn(job)
        except Exception as exc:          # every failure is reported, none raised
            job.status = "failed"
            job.error = f"{type(exc).__name__}: {exc}"
            job.say(f"failed — {job.error}")

    _worker.submit(run)
    return job


# ------------------------------------------------------------------ adding

def start_add(pdf_bytes: bytes, filename: str, slug: str, name: str, act_no: str) -> Job:
    """Save the upload and queue its review. Returns at once with the job."""
    from app.rag import catalog

    slug = slug.strip().lower()
    name = " ".join((name or "").split())
    if not SLUG_RE.match(slug):
        raise Refused("the short name must be 2–31 lowercase letters, digits or "
                      "underscores, starting with a letter (e.g. arms, police_act)")
    if not name:
        raise Refused("give the document a title")
    if slug in catalog.act_slugs() or is_core(slug):
        raise Refused(f"'{slug}' is already in the corpus — remove it first to replace it")
    if len(pdf_bytes) > MAX_UPLOAD_BYTES:
        raise Refused(f"the file is over {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    if not pdf_bytes.startswith(b"%PDF"):
        raise Refused("that file is not a PDF")

    # The source of law lives in Laws/main, beside the others; citations refer
    # to it by file name. Never overwrite a different file of the same name.
    safe = re.sub(r"[^\w\s.,()'-]", "", pathlib.Path(filename or f"{slug}.pdf").name).strip()
    if not safe.lower().endswith(".pdf"):
        safe += ".pdf"
    dest = config.LAWS / safe
    if dest.exists() and dest.read_bytes() != pdf_bytes:
        raise Refused(f"a different file named '{safe}' is already in Laws/main")
    dest.write_bytes(pdf_bytes)

    job = Job(id=uuid.uuid4().hex[:12], kind="add", slug=slug, name=name,
              pdf=str(dest), act_no=act_no.strip())
    job.say(f"saved {safe} ({len(pdf_bytes) / 1024:.0f} KB)")
    return _submit(job, _review)


def _review(job: Job) -> None:
    """PyMuPDF on the whole file, Docling on sampled pages, as a review payload."""
    import ingest_document as ing
    import parse_docling_document as docling_doc
    import parse_law_pdf as statute

    job.status = "reviewing"
    pdf = pathlib.Path(job.pdf)

    job.say("parsing with PyMuPDF (statute parser)")
    try:
        res = statute.parse(job.slug, pdf=str(pdf), act_name=job.name,
                            act_no=job.act_no, backend="pymupdf")
        err = None
    except (Exception, SystemExit) as exc:
        res, err = None, f"{type(exc).__name__}: {exc}"
    job.staged = res

    pages = ing.sample_pages(pdf, SAMPLE_PAGES, job.slug)
    job.say(f"running Docling on sampled pages {[p + 1 for p in pages]}")
    from docling.document_converter import DocumentConverter

    converter = DocumentConverter()
    out_pages = []
    for p in pages:
        try:
            d = converter.convert(str(pdf), page_range=(p + 1, p + 1)).document
            dres, derr = docling_doc.to_parsed(d, job.name, job.act_no, pdf.name), None
        except Exception as exc:
            dres, derr = None, f"{type(exc).__name__}: {exc}"
        out_pages.append({
            "page": p + 1,
            "image": "data:image/png;base64," + ing.page_image(pdf, p, dpi=90),
            "pymupdf": _units(ing.on_page(res, p)) if res else [],
            "pymupdf_error": err,
            "pymupdf_note": _page_note(res, p) if res else None,
            "docling": _units(ing._sections(dres)) if dres else [],
            "docling_error": derr,
        })

    job.review = {"pages": out_pages,
                  "pymupdf": ing.summary(res) if res else None,
                  "pymupdf_error": err}
    job.status = "review"
    job.say("ready for review — compare each page with both parsers, then choose")


def _units(sections: list[dict]) -> list[dict]:
    return [{"number": str(s["section_number"]), "title": s.get("section_title"),
             "pages": [s["page_start"] + 1, s["page_end"] + 1],
             "text": s["text"][:1500], "truncated": len(s["text"]) > 1500,
             "children": len(s.get("children") or [])} for s in sections]


def _page_note(res: dict, page: int) -> str | None:
    import ingest_document as ing

    if ing.on_page(res, page):
        return None
    first = min((s["page_start"] for s in ing._sections(res)), default=None)
    if res.get("preamble") and (first is None or page < first):
        return ("No section on this page — its text went to the preamble, which is not "
                "searched. Right for a title or contents page, wrong if it is body text.")
    return "No section starts or continues on this page."


def choose(job_id: str, parser: str) -> Job:
    """Accept the review with one parser; queue the build."""
    job = _jobs.get(job_id)
    if job is None or job.kind != "add":
        raise Refused("no such job")
    if job.status != "review":
        raise Refused(f"this job is {job.status}, not waiting for review")
    if parser not in ("pymupdf", "docling"):
        raise Refused("choose pymupdf or docling")
    if parser == "pymupdf" and not (job.staged and _count(job.staged)):
        raise Refused("PyMuPDF found no sections in this document — saving it would make "
                      "it unsearchable. Choose Docling.")
    job.status = "building"
    job.say(f"chose {parser}")
    _worker.submit(_guard(job, lambda j: _build(j, parser)))
    return job


def _guard(job: Job, fn):
    def run():
        try:
            fn(job)
        except Exception as exc:
            job.status = "failed"
            job.error = f"{type(exc).__name__}: {exc}"
            job.say(f"failed — {job.error}")
    return run


def _count(res: dict) -> int:
    return sum(len(ch.get("sections", [])) for ch in res.get("chapters", []))


def _build(job: Job, parser: str) -> None:
    import chunk_sections
    import parse_docling_document as docling_doc

    pdf = pathlib.Path(job.pdf)
    if parser == "docling":
        job.say("parsing the whole document with Docling (reads every page's layout)")
        res = docling_doc.to_parsed(docling_doc.convert(pdf), job.name, job.act_no, pdf.name)
        doc_type = "document"
    else:
        res, doc_type = job.staged, "statute"
    if not _count(res):
        raise Refused("the chosen parser produced nothing to index")
    res = {**res, "parser": parser, "doc_type": doc_type}
    config.PARSED_DIR.mkdir(parents=True, exist_ok=True)
    (config.PARSED_DIR / f"{job.slug}.json").write_text(
        json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    job.say(f"parsed: {_count(res)} {'passages' if doc_type == 'document' else 'sections'}")

    job.say("chunking")
    chunks = chunk_sections.chunk_act(job.slug, chunk_sections.DEFAULT_MAX)["chunks"]
    config.CHUNKS_DIR.mkdir(parents=True, exist_ok=True)
    (config.CHUNKS_DIR / f"{job.slug}.json").write_text(
        json.dumps(chunks, indent=2, ensure_ascii=False), encoding="utf-8")
    job.say(f"chunked: {len(chunks)} chunks")

    _rebuild_and_sync(job)
    _cleanup_review(job.slug)
    job.status = "done"
    job.say(f"done — {job.name} is searchable and in the planner's scope")


# ------------------------------------------------------------------ removing

def start_remove(slug: str) -> Job:
    from app.rag import catalog

    slug = slug.strip().lower()
    if is_core(slug):
        raise Refused(f"'{slug}' is a core Act and cannot be removed")
    if slug not in catalog.act_slugs() and not (config.PARSED_DIR / f"{slug}.json").exists():
        raise Refused(f"no source '{slug}'")
    a = catalog.act(slug)
    job = Job(id=uuid.uuid4().hex[:12], kind="remove", slug=slug,
              name=a["name"] if a else slug)
    return _submit(job, _remove)


def _remove(job: Job) -> None:
    from app.db import supabase

    job.status = "building"
    # Supabase first: this is what users see, and the app drops the Act as soon
    # as its catalog refreshes.
    supabase.client().table("law_acts").delete().eq("slug", job.slug).execute()
    job.say("removed from Supabase")

    dest = REMOVED / job.slug
    dest.mkdir(parents=True, exist_ok=True)
    for folder in (config.PARSED_DIR, config.CHUNKS_DIR):
        f = folder / f"{job.slug}.json"
        if f.exists():
            shutil.move(str(f), str(dest / f"{folder.name}-{job.slug}.json"))
    job.say(f"local files moved to Laws/removed/{job.slug}/ (recoverable)")

    _rebuild_and_sync(job)
    job.status = "done"
    job.say(f"done — {job.name} is no longer searched or offered")


# ------------------------------------------------------------------ shared tail

def _rebuild_and_sync(job: Job) -> None:
    """Local index + catalog from Laws/main, then Supabase from those, then check."""
    import build_catalog
    import build_index

    job.say("updating the vector index (new text only)")
    build_index.build(log=lambda line: job.say(line.strip()))

    # Other embedders' vectors (E21), only for models already in use here — a
    # vector file exists. New chunks only; a failure (the OpenRouter daily cap)
    # does not stop the job: that profile then falls back to the local one,
    # logged, until `build_index.py --embedder <name>` is re-run.
    import load_supabase as L
    for name in L.alt_models():
        try:
            build_index.build_alt(name, log=lambda line: job.say(line.strip()))
        except Exception as exc:
            job.say(f"  {name} vectors NOT updated ({type(exc).__name__}: "
                    f"{str(exc)[:120]}) — that profile falls back to local until rebuilt")

    job.say("rebuilding the catalog")
    build_catalog.build(config.CATALOG)

    job.say("loading into Supabase")
    _load_all(job)

    _refresh_caches()
    job.say("checking Supabase against the local copy")
    _verify(job)


def _load_all(job: Job) -> None:
    """Every Act into Supabase, skipping unchanged ones by digest — load_supabase's loop."""
    import sqlite3

    import load_supabase as L
    import numpy as np

    from app.db import supabase

    client = supabase.client()
    db = sqlite3.connect(config.CATALOG)
    chunks = json.loads((config.INDEX_DIR / "chunks.json").read_text(encoding="utf-8"))
    emb = np.load(config.INDEX_DIR / "embeddings.npy")
    for slug in [r[0] for r in db.execute("select slug from acts order by slug")]:
        rows = L.build(slug, db, chunks, emb)
        d = L.digest(rows)
        have = client.table("law_loads").select("digest").eq("act_slug", slug).execute().data
        if have and have[0]["digest"] == d:
            continue
        L.load(client, slug, rows, d)
        job.say(f"  loaded {slug}: {len(rows['law_sections'])} sections, "
                f"{len(rows['law_chunks'])} vectors")

    # Reloading an Act cascades away its alternate vectors; put them back.
    from app.rag.store import alt_paths, texts_digest
    for name in L.alt_models():
        alt = np.load(alt_paths(name)[0])
        meta = json.loads(alt_paths(name)[1].read_text(encoding="utf-8"))
        if meta.get("texts_digest") != texts_digest(chunks):
            continue                            # stale: not loaded, falls back
        try:
            for slug in [r[0] for r in db.execute("select slug from acts order by slug")]:
                L.load_alt(client, slug, name, L.build_alt(slug, db, name, chunks, alt),
                           log=lambda line: job.say(line.strip()))
        except Exception as exc:                # e.g. migration 003 not applied
            job.say(f"  {name} vectors not loaded to Supabase ({type(exc).__name__}); "
                    f"served from the local file instead")


def _refresh_caches() -> None:
    """Make the running app see the new corpus now, not in a minute."""
    from app.rag import catalog, corpus_db, library, store

    store.load_store.cache_clear()
    store.load_alt.cache_clear()
    catalog._local.cache_clear()
    catalog.act_slugs.cache_clear()
    library._library.cache_clear()
    corpus_db._state["checked"] = 0.0
    catalog.connect()


def _verify(job: Job) -> None:
    cmd = [sys.executable, str(SCRIPTS / "verify" / "verify_supabase.py"), "--queries", "12"]
    run = subprocess.run(cmd, cwd=config.BACKEND, capture_output=True, text=True)
    tail = [l for l in run.stdout.splitlines() if "PASS" in l or "FAIL" in l or "checks passed" in l]
    job.say(tail[-1] if tail else "verifier printed nothing")
    if run.returncode != 0:
        failed = [l.strip() for l in tail if "FAIL" in l]
        raise RuntimeError("Supabase and the local copy disagree: " + "; ".join(failed[:3]))


def _cleanup_review(slug: str) -> None:
    shutil.rmtree(REVIEW / slug, ignore_errors=True)


def discard(job_id: str) -> Job:
    """Drop an unfinished add: nothing was indexed, and the upload is removed."""
    job = _jobs.get(job_id)
    if job is None or job.kind != "add":
        raise Refused("no such job")
    if job.status not in ("review", "failed"):
        raise Refused(f"this job is {job.status} and cannot be discarded now")
    if job.pdf and not (config.PARSED_DIR / f"{job.slug}.json").exists():
        pathlib.Path(job.pdf).unlink(missing_ok=True)
    _cleanup_review(job.slug)
    job.status = "discarded"
    job.review = None
    job.say("discarded — nothing was added")
    return job
