"""Single source of truth for model names and paths.

These were previously hardcoded in four scripts, which is how the evaluated
pipeline and the servable one drifted apart: the log reported 41.9% (dense +
reranking) while nothing outside an eval script could rerank at all.
"""

from __future__ import annotations

import os
import pathlib

BACKEND = pathlib.Path(__file__).resolve().parents[2]
ROOT = BACKEND.parent
LAWS = ROOT / "Laws" / "main"

PARSED_DIR = LAWS / "parsed"
CHUNKS_DIR = LAWS / "chunks"
INDEX_DIR = LAWS / "index"
# Structural read model built from PARSED_DIR by scripts/build_catalog.py.
# Rebuildable: the parsed JSON stays the source of truth.
CATALOG = LAWS / "catalog.sqlite"
QA_DIR = ROOT / "Laws" / "bangladesh-legal-qa-dataset" / "qa-splits"

def acts() -> tuple[str, ...]:
    """Slugs of every act currently parsed, discovered from disk.

    Not a hardcoded list: adding a legal PDF should be an ingestion run, not a
    source edit. Whatever `parse_law_pdf.py` wrote is what the rest of the
    pipeline chunks, indexes and serves.
    """
    if not PARSED_DIR.exists():
        return ()
    return tuple(sorted(p.stem for p in PARSED_DIR.glob("*.json")))


# Kept as a name for callers that want the current corpus at import time.
ACTS = acts()

# E4: bge-reranker-v2-m3 measured +9.2pp R@1 and +4.9pp Act accuracy over dense
# alone. E6 could not validly measure the decoder-based alternatives (loading a
# causal LM through AutoModelForSequenceClassification attaches a random head),
# so they are unmeasured rather than rejected.
EMBED_MODEL = os.getenv("RAG_EMBED_MODEL", "BAAI/bge-m3")

# Generator registry: alias -> (backend, model id). One table, because the
# aliases previously lived in two scripts and the API knew none of them.
#
# `enforces_schema` is not a convenience flag. The pipeline requires JSON with a
# verbatim quote per claim; a route that treats the schema as a hint returns
# prose, which surfaces as a false refusal rather than as an error. False =
# measured as not enforcing, None = advertised but unverified. Check any route
# with `python scripts/openrouter_chat.py --json --model <alias>`.
GENERATORS: dict[str, dict] = {
    "qwen":      {"backend": "groq",       "model": "qwen/qwen3.8-27b",
                  "enforces_schema": True,  "note": "Groq free tier; fast, strict JSON schema"},
    "lfm":       {"backend": "openrouter", "model": "liquid/lfm-2.5-2.6b:free",
                  "enforces_schema": True,  "note": "2.6B, ~2s/call"},
    "super":     {"backend": "openrouter", "model": "nvidia/nemotron-3-super-120b-a12b:free",
                  "enforces_schema": True,  "note": "120B MoE"},
    "mercury":   {"backend": "inception",  "model": "mercury-2.5",
                  "enforces_schema": True,
                  "note": "diffusion LLM, 260K ctx; 469 tok/s on the schema probe"},
    "mercury-2": {"backend": "inception",  "model": "mercury-2",
                  "enforces_schema": True,  "note": "previous generation, 128K ctx"},
    "gemini":    {"backend": "gemini",     "model": "gemini-3.5-flash-lite",
                  "enforces_schema": True,
                  "note": "E1 scored 5/5, but on gemini-2.5-flash"},
}

DEFAULT_GENERATOR = os.getenv("RAG_GENERATOR", "qwen")

# The router runs on every question and only classifies and rewrites, so it
# wants a small fast model, not the one that writes the answer. lfm measured
# fastest of the free routes that enforce JSON schema, on the actual router
# prompt: 3.3s against 5.8s (nexmini) and 12.1s (super).
DEFAULT_ROUTER = os.getenv("RAG_ROUTER_MODEL", "lfm")

# The planner (app/rag/plan.py) decides the category and names the coordinates
# for every question, so its judgement bounds the whole system's. It runs on a
# stronger model than the old regex router's fallback: lfm-2.6B measured
# unstable on exactly this job, labelling identical phrasings differently
# between two prompt revisions. Groq Qwen enforces the strict schema the
# validator depends on.
PLANNER_MODEL = os.getenv("RAG_PLANNER_MODEL", "qwen")

# Planning replaces pattern routing. Off falls back to refine.classify_pattern.
PLAN_QUERIES = os.getenv("RAG_PLAN", "1") == "1"


def resolve_generator(name: str | None) -> tuple[str, str]:
    """alias | 'backend:model' | bare model id -> (backend, model id).

    Unknown ids pass through on the current backend, so a route that is not in
    the table can still be tried without editing this file.
    """
    name = (name or DEFAULT_GENERATOR).strip()
    if name in GENERATORS:
        g = GENERATORS[name]
        return g["backend"], g["model"]
    if ":" in name and name.split(":", 1)[0] in {"groq", "openrouter", "gemini",
                                                 "inception"}:
        backend, model = name.split(":", 1)
        return backend, model
    return os.getenv("RAG_LLM_BACKEND", "groq"), name
RERANK_MODEL = os.getenv("RAG_RERANK_MODEL", "BAAI/bge-reranker-v2-m3")

# Candidates fetched densely, then reranked down. 20 was what E4 measured.
DENSE_K = int(os.getenv("RAG_DENSE_K", "20"))
FINAL_K = int(os.getenv("RAG_FINAL_K", "5"))

# E5 indexes sections AND their subsections. Children below this are too thin
# to carry meaning on their own.
MIN_CHILD_TOKENS = 25

# Query rewriting (step 10). Off until it moves a number on the 608-question
# set: it costs an LLM call per query and adds a failure mode, and two other
# techniques imported from published work (E2, E3) did nothing here.
# Routing (app/rag/refine.py): decide whether a question needs retrieval at all
# before running it. On by default — a correctness fix, not a tuning knob. Meta,
# browse, lookup and general questions were previously answered with five
# loosely-matching sections and a refusal. Obvious cases match by pattern at
# zero cost; the rest cost one small-model call.
ROUTE_QUERIES = os.getenv("RAG_ROUTE", "1") == "1"

# Query rewriting into statutory register. Rides along on the routing call, so
# it adds no round trip — but off until it moves a number on the 608-question
# set, because two techniques imported from published work (E2, E3) did nothing
# here.
REFINE_QUERIES = os.getenv("RAG_REFINE", "0") == "1"

EMBED_BATCH = int(os.getenv("RAG_EMBED_BATCH", "8"))
RERANK_BATCH = int(os.getenv("RAG_RERANK_BATCH", "16"))
MAX_SEQ_LEN = 1024


def device() -> str:
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"
