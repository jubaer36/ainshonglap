"""retrieve(question) -> ranked sections. The whole of E4 + E5 in one call.

Three stages, each one an experiment that moved a number on the 608-question
eval set:

  1. dense search over a dual-granularity index (E5). Sections AND their
     subsections both have vectors, which lifted Recall@20 from 66.6% to 70.4%
     — the ceiling stage 3 cannot raise for itself.
  2. collapse to distinct sections. Several vectors can point at one section, so
     the top-20 *vectors* can be far fewer than 20 sections. Over-fetching then
     de-duplicating is what makes the candidate set 20 sections deep again.
  3. cross-encoder rerank of those candidates (E4, +9.2pp Recall@1).

A child vector carries its PARENT section's metadata, so a subsection hit is a
section hit and the citation still points at the section.

`retrieve_many` exists so the eval scripts run this exact code rather than
their own copy of it — the previous split is how the logged numbers came to
describe a pipeline nothing could actually serve.

Which embedder and reranker run is a **retrieval profile** (E21,
`config.RETRIEVAL_PROFILES`): the server default, `profile=` here, or
`use_profile()` for the rest of one request. The stages above are the same for
every profile; only the models change. A profile that needs OpenRouter falls
back to the local one when OpenRouter cannot serve it, and says so in the log.
"""

from __future__ import annotations

import contextvars
import logging
from dataclasses import dataclass, field

from app.rag import config
from app.rag.encoder import embed
from app.rag.reranker import rerank_scores
from app.rag.store import AltMissing, load_alt, load_store

log = logging.getLogger("ainshonglap")

# The profile for the rest of this request, when the caller chose one. A context
# variable rather than a parameter threaded through the planner, the executors
# and the answer code: those call `retrieve()` from half a dozen places, and
# none of them should have to know retrieval has variants.
_profile: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "retrieval_profile", default=None)


def use_profile(name: str | None) -> None:
    """Retrieve with `name` for the rest of this request (None = server default)."""
    if name:
        config.retrieval_profile(name)          # raises on an unknown name
    _profile.set(name or None)


def active_profile() -> str:
    return config.retrieval_profile(_profile.get())[0]

# How many vectors to pull before collapsing to sections. 3x was enough for the
# top-20 to survive de-duplication in E5, where 60 vectors were fetched for 20
# sections.
OVERFETCH = 3



@dataclass
class Retrieved:
    """One section, as returned to a caller."""

    act: str
    section: str
    section_title: str | None
    text: str
    score: float
    dense_score: float
    rank: int
    metadata: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        """Stable citation key, e.g. `crpc-s154`."""
        # The slug comes from the catalog, so an Act loaded later is keyed
        # correctly without a code change (a fixed list once keyed the Arms
        # Act's sections `act-s13`, which nothing could resolve).
        from app.rag import acts

        return f"{acts.slug_for_name(self.act) or 'act'}-s{self.section}"


def _collapse(chunks, scores, ids, dense_k):
    """Vectors -> distinct sections, keeping each section's best-scoring vector."""
    seen: dict[tuple[str, str], dict] = {}
    for vec_score, j in zip(scores, ids):
        if j < 0:                       # FAISS pads with -1 when it runs short
            continue
        rec = chunks[int(j)]
        m = rec["metadata"]
        key = (m["act"], str(m["section"]))
        if key in seen:
            continue
        seen[key] = {"chunk": rec, "dense": float(vec_score)}
        if len(seen) >= dense_k:
            break
    return list(seen.values())


def _to_results(cands, k):
    out = []
    for rank, c in enumerate(cands[:k], start=1):
        m = c["chunk"]["metadata"]
        out.append(Retrieved(
            act=m["act"], section=str(m["section"]),
            section_title=m.get("section_title"),
            text=c["chunk"]["text"], score=c["score"], dense_score=c["dense"],
            rank=rank, metadata=m,
        ))
    return out


def _search(qemb, n: int, embedder: str = "bge-m3") -> list[tuple[list[dict], list[float], list[int]]]:
    """Nearest chunks per query vector — from Supabase, or the local FAISS index
    if Supabase fails.

    Returns, per query, (chunks, scores, ids) in the shape `_collapse` takes, so
    grouping into sections, reranking and everything after are the same code
    whichever answered. Both are the same exact inner-product scan over the same
    vectors (`verify_supabase.py` checks it), so a fallback changes where the
    result came from, not what it is — unless an Act was loaded to Supabase and
    not rebuilt locally, which is why every fallback is logged.
    """
    from app.rag import corpus_db, fallback

    source = "vector search" if embedder == "bge-m3" else f"vector search ({embedder})"
    try:
        out = []
        for q in qemb:
            rows = corpus_db.match(q, n, embedder)
            out.append(([{"text": r["text"], "metadata": r["metadata"]} for r in rows],
                        [r["score"] for r in rows], list(range(len(rows)))))
        fallback.recovered(source)
        return out
    except corpus_db.Unavailable as exc:
        fallback.used(source, str(exc))

    # Raises AltMissing when this embedder has no vectors for the current
    # corpus; the caller then drops to the local profile.
    index, chunks = load_alt(embedder) if embedder != "bge-m3" else load_store()[:2]
    scores, ids = index.search(qemb, min(n, index.ntotal))
    return [(chunks, list(scores[i]), list(ids[i])) for i in range(len(qemb))]


def _fell_back(what: str, why: object) -> None:
    log.warning("FALLBACK  retrieval: %s failed (%s); using %s",
                what, str(why)[:160], config.LOCAL_PROFILE)


def retrieve_many(questions: list[str], k: int | None = None, *,
                  dense_k: int | None = None, rerank: bool = True,
                  batch: int | None = None, progress: bool = False,
                  profile: str | None = None) -> list[list[Retrieved]]:
    """Same pipeline as `retrieve`, batched over many questions.

    `rerank=False` skips the reranking stage whatever the profile says.
    """
    from app.rag import remote_retrieval, trace

    if not questions:
        return []
    k = k or config.FINAL_K
    dense_k = dense_k or config.DENSE_K
    name, spec = config.retrieval_profile(profile or _profile.get())
    embedder = spec["embedder"]
    reranker = spec["reranker"] if rerank else None

    hits = None
    if embedder != "bge-m3":
        try:
            qemb = remote_retrieval.embed(questions, embedder, "query")
            hits = _search(qemb, dense_k * OVERFETCH, embedder)
        except (remote_retrieval.Unavailable, AltMissing) as exc:
            _fell_back(f"{embedder} search", exc)
            name, embedder = config.LOCAL_PROFILE, "bge-m3"
            reranker = "bge" if rerank else None
    if hits is None:
        qemb = embed(questions, batch=batch)
        hits = _search(qemb, dense_k * OVERFETCH)
    trace.step("retrieve", f"profile {name}")

    results = []
    for i in range(len(questions)):
        chunks, scores, ids = hits[i]
        cands = _collapse(chunks, scores, ids, dense_k)
        if reranker == "nemotron" and cands:
            try:
                got = remote_retrieval.rerank(questions[i],
                                              [c["chunk"]["text"] for c in cands])
            except remote_retrieval.Unavailable as exc:
                # This question and the rest of the batch: one failure is
                # enough to know, and 600 failing calls would say nothing more.
                _fell_back("nemotron rerank", exc)
                reranker = "bge"
            else:
                for c, s in zip(cands, got):
                    c["score"] = s
                cands.sort(key=lambda c: -c["score"])
        if reranker == "bge" and cands:
            pairs = [(questions[i], c["chunk"]["text"]) for c in cands]
            for c, s in zip(cands, rerank_scores(pairs)):
                c["score"] = s
            cands.sort(key=lambda c: -c["score"])
        elif reranker is None:
            for c in cands:
                c["score"] = c["dense"]
        results.append(_to_results(cands, k))
        if progress and (i + 1) % 100 == 0:
            print(f"  {i+1}/{len(questions)}", flush=True)
    return results


def retrieve(question: str, k: int | None = None, *,
             dense_k: int | None = None, rerank: bool = True,
             profile: str | None = None) -> list[Retrieved]:
    """Top-k sections for a question, best first.

    `rerank=False` gives the dense-only ordering — kept so the two can be
    compared on one code path rather than across two scripts, which is how the
    eval numbers and the served numbers diverged in the first place.
    """
    if not question.strip():
        return []
    return retrieve_many([question], k, dense_k=dense_k, rerank=rerank,
                         profile=profile)[0]


def warmup() -> None:
    """Build the catalog and load both models up front.

    Called at API startup so the first user query does not pay ~30s of model
    load, and so a missing index fails at boot instead of inside a request.
    """
    # The vectors stay in Postgres, so nothing is loaded from disk; the local
    # FAISS index is read only if a fallback ever needs it. Building the
    # in-memory catalog here moves its one-off cost off the first question.
    from app.rag import catalog

    catalog.connect()
    embed(["warmup"])
    rerank_scores([("warmup", "warmup")])
