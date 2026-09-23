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

# The statute text is read from Supabase (the law_* tables, migration 002).
# catalog.sqlite and the FAISS index are kept ONLY as a fallback for when
# Supabase cannot answer — see app/rag/fallback.py, which logs every use.
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
    "oss":       {"backend": "groq",       "model": "openai/gpt-oss-20b",
                  "enforces_schema": True,
                  "note": "Groq; separate daily token budget from qwen"},
    "oss120":    {"backend": "groq",       "model": "openai/gpt-oss-120b",
                  "enforces_schema": True,
                  "note": "Groq; default. Strict schema, 2.1s on the probe"},
    "lfm":       {"backend": "openrouter", "model": "liquid/lfm-2.5-2.6b:free",
                  "enforces_schema": True,  "note": "2.6B, ~2s/call"},
    "super":     {"backend": "openrouter", "model": "nvidia/nemotron-3-super-120b-a12b:free",
                  "enforces_schema": True,  "note": "120B MoE"},
    "mercury":   {"backend": "inception",  "model": "mercury-2.5",
                  "enforces_schema": True,
                  "note": "diffusion LLM, 260K ctx; 469 tok/s on the schema probe"},
    "mercury-2": {"backend": "inception",  "model": "mercury-2",
                  "enforces_schema": True,  "note": "previous generation, 128K ctx"},
    "planner":   {"backend": "groq",       "model": "openai/gpt-oss-120b",
                  "key_env": "GROQ_API_KEY_PLANNER", "enforces_schema": True,
                  "internal": True,
                  "note": "the planner, on its own Groq key"},
    "gemini2":   {"backend": "gemini",     "model": "gemini-3.5-flash-lite",
                  "key_env": "GEMINI_API_KEY_2", "enforces_schema": True,
                  "internal": True,
                  "note": "second Gemini key; planner and conversation summariser"},
    "gemini":    {"backend": "gemini",     "model": "gemini-3.5-flash-lite",
                  "enforces_schema": True,
                  "note": "E1 scored 5/5, but on gemini-2.5-flash"},
}

# `internal` routes are machinery, not choices. gemini2 runs the planner and the
# conversation summariser on their own key; offering it in the picker would let
# a reader spend the planner's quota on an answer and then wonder why the next
# question stopped being classified. Resolvable by alias, just not advertised.
# The order the picker offers them in, best first. The registry above is grouped
# by provider, which is the right shape for reading and the wrong one for
# choosing: it opens on qwen and buries the 120B behind it. Anything not named
# here keeps its registry position, after these.
PICKER_ORDER = ("oss120", "qwen", "oss", "gemini")


def selectable() -> dict[str, dict]:
    """The generators a reader may pick, best first."""
    offered = {a: g for a, g in GENERATORS.items() if not g.get("internal")}
    ranked = [a for a in PICKER_ORDER if a in offered]
    return {a: offered[a] for a in ranked + [a for a in offered if a not in ranked]}


DEFAULT_GENERATOR = os.getenv("RAG_GENERATOR", "oss120")

# Used when the chosen generator is out of quota. Wants a different PROVIDER
# from the default, not just a different model: Groq's daily cap is per model,
# but an outage or a key problem is not, and a fallback on the same provider
# would fail with it. Mercury is Inception, enforces the schema, and has its
# own budget.
FALLBACK_GENERATOR = os.getenv("RAG_FALLBACK_GENERATOR", "mercury")

# A pasted document — a case brief, a transcribed page — is a different job from
# a typed question: a wall of narrative prose wrapped around a few statutory
# references, where the work is reading carefully rather than answering briefly.
# gpt-oss-20b is used for those. It also has its own daily budget on Groq, so a
# long input does not eat the default generator's.
#
# Only applies when the caller did not choose a model: an explicit pick from the
# model picker is always honoured.
LONG_INPUT_GENERATOR = os.getenv("RAG_LONG_INPUT_GENERATOR", "oss")

# Generators with the context and capacity for a pasted document. Nothing is
# switched to them automatically: the reader picked a model, and swapping it
# behind their back is the silent-substitution problem again. When a document
# arrives on a smaller model the answer is still produced, and the response
# carries a suggestion they can act on in one click.
LARGE_CONTEXT_MODELS = tuple(
    m.strip() for m in
    os.getenv("RAG_LARGE_CONTEXT_MODELS", "oss120,mercury,super,gemini").split(",")
    if m.strip())

# Offered first in that suggestion.
SUGGESTED_DOCUMENT_MODEL = os.getenv("RAG_DOCUMENT_GENERATOR", "oss120")


def suggests_larger_model(alias: str | None) -> str | None:
    """The model to suggest for a document, or None when the current one will do."""
    current = (alias or DEFAULT_GENERATOR).strip()
    if current in LARGE_CONTEXT_MODELS:
        return None
    return SUGGESTED_DOCUMENT_MODEL
LONG_INPUT_CHARS = int(os.getenv("RAG_LONG_INPUT_CHARS", "700"))

# The router runs on every question and only classifies and rewrites, so it
# wants a small fast model, not the one that writes the answer. lfm measured
# fastest of the free routes that enforce JSON schema, on the actual router
# prompt: 3.3s against 5.8s (nexmini) and 12.1s (super).
DEFAULT_ROUTER = os.getenv("RAG_ROUTER_MODEL", "lfm")

# The planner (app/rag/plan.py) decides the category and names the coordinates
# for every question, so its judgement bounds the whole system's. It runs on a
# stronger model than the old regex router's fallback: lfm-2.6B measured
# unstable on exactly this job, labelling identical phrasings differently
# between two prompt revisions.
#
# `planner` is gpt-oss-120b on GROQ_API_KEY_PLANNER — its own key, not the pool
# the generators rotate through. The planner runs before anything else on every
# question, so a burst of answers must not leave it rate-limited: a failed plan
# degrades the question to plain semantic retrieval, which sent "who are you" to
# the statute index and returned sections scoring -3.90. The key is a dedicated
# prefix rather than a fourth numbered one precisely so `keyring` does not sweep
# it into that rotation.
PLANNER_MODEL = os.getenv("RAG_PLANNER_MODEL", "planner")

# Tried when the planner's own model fails. A different PROVIDER, not just a
# different key: an outage or a bad request is not per-key, so a second Groq
# route would fail with it. Gemini 3.5 Flash Lite on GEMINI_API_KEY_2 was the
# planner before this and is measured on the job (E16), which makes it the
# obvious standby rather than a guess.
PLANNER_FALLBACK = os.getenv("RAG_PLANNER_FALLBACK", "gemini2")

# Planning replaces pattern routing. Off falls back to refine.classify_pattern.
PLAN_QUERIES = os.getenv("RAG_PLAN", "1") == "1"


def generator_key_env(name: str | None) -> str | None:
    """Which API key an alias uses, when it is not the provider's default.

    `gemini2` is the same model as `gemini` on a different key. Without this the
    two would share one quota, which defeats the point of having a fallback.
    """
    spec = GENERATORS.get((name or "").strip())
    return spec.get("key_env") if spec else None


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
# 5 was E4's figure, chosen when every question was "what does s.X say". It is
# too tight for a question whose answer is split across two Acts: "if I cannot
# prove myself innocent, am I guilty" needs the Evidence Act burden rule AND the
# CrPC acquittal provision, and the Evidence cluster alone fills all five slots.
# 8 is below MAX_CONTEXT_SECTIONS (10), so the generator budget still binds.
FINAL_K = int(os.getenv("RAG_FINAL_K", "8"))

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
