"""retrieve(question) -> ranked sections.

Embed the question with nemotron-3-embed (OpenRouter), search the vectors,
collapse hits to distinct sections, optionally rerank. No local model: if
OpenRouter fails, meaning search raises SearchUnavailable (a 503).
"""

from __future__ import annotations

import contextvars
import logging
from dataclasses import dataclass, field

from app.rag import config
from app.rag.store import AltMissing, search_alt

log = logging.getLogger("ainshonglap")


class SearchUnavailable(RuntimeError):
    """Meaning search cannot run right now (OpenRouter or the vectors)."""


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


def _search(qemb, n: int, embedder: str) -> list[tuple[list[dict], list[float], list[int]]]:
    """Nearest chunks per query vector — Supabase, or the local copy if it fails.

    Returns, per query, (chunks, scores, ids) in the shape `_collapse` takes.
    Both are the same exact inner-product scan over the same vectors
    (`verify_supabase.py` checks it), so a fallback changes where the result
    came from, not what it is — and every fallback is logged.
    """
    from app.rag import corpus_db, fallback

    source = f"vector search ({embedder})"
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
    try:
        return search_alt(embedder, qemb, n)
    except AltMissing as exc:
        raise SearchUnavailable(f"no {embedder} vectors to search: {exc}") from exc


def retrieve_many(questions: list[str], k: int | None = None, *,
                  dense_k: int | None = None, rerank: bool = True,
                  batch: int | None = None, progress: bool = False,
                  profile: str | None = None) -> list[list[Retrieved]]:
    """Same pipeline as `retrieve`, batched over many questions.

    `rerank=False` skips the reranker whatever the profile says. `batch` is
    accepted for the eval scripts' signature and ignored.
    """
    from app.rag import remote_retrieval, trace

    if not questions:
        return []
    k = k or config.FINAL_K
    dense_k = dense_k or config.DENSE_K
    name, spec = config.retrieval_profile(profile or _profile.get())
    embedder = spec["embedder"]
    reranker = spec["reranker"] if rerank else None

    try:
        qemb = remote_retrieval.embed(questions, embedder, "query")
    except remote_retrieval.Unavailable as exc:
        log.warning("SEARCH UNAVAILABLE  %s could not embed the question: %s",
                    embedder, str(exc)[:160])
        raise SearchUnavailable(f"{embedder} could not embed the question "
                                f"({str(exc)[:120]})") from exc
    hits = _search(qemb, dense_k * OVERFETCH, embedder)
    trace.step("retrieve", f"profile {name}")

    results = []
    for i in range(len(questions)):
        chunks, scores, ids = hits[i]
        cands = _collapse(chunks, scores, ids, dense_k)
        for c in cands:
            c["score"] = c["dense"]
        if reranker == "nemotron" and cands:
            try:
                got = remote_retrieval.rerank(questions[i],
                                              [c["chunk"]["text"] for c in cands])
            except remote_retrieval.Unavailable as exc:
                # The search order is already a measured answer (E21); the
                # reranker is an extra, so losing it costs nothing but itself.
                log.warning("FALLBACK  retrieval: nemotron rerank failed (%s); "
                            "using the search order", str(exc)[:160])
                reranker = None
            else:
                for c, s in zip(cands, got):
                    c["score"] = s
                cands.sort(key=lambda c: -c["score"])
        results.append(_to_results(cands, k))
        if progress and (i + 1) % 100 == 0:
            print(f"  {i+1}/{len(questions)}", flush=True)
    return results


def retrieve(question: str, k: int | None = None, *,
             dense_k: int | None = None, rerank: bool = True,
             profile: str | None = None) -> list[Retrieved]:
    """Top-k sections for a question, best first. Raises `SearchUnavailable`."""
    if not question.strip():
        return []
    return retrieve_many([question], k, dense_k=dense_k, rerank=rerank,
                         profile=profile)[0]


def warmup() -> None:
    """Build the in-memory catalog up front, so the first question does not pay
    for it. No model to load: retrieval runs on OpenRouter."""
    from app.rag import catalog

    catalog.connect()
