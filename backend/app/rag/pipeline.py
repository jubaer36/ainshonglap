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
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.rag import config
from app.rag.encoder import embed
from app.rag.reranker import rerank_scores
from app.rag.store import load_store

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


def _search(qemb, n: int) -> list[tuple[list[dict], list[float], list[int]]]:
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

    try:
        out = []
        for q in qemb:
            rows = corpus_db.match(q, n)
            out.append(([{"text": r["text"], "metadata": r["metadata"]} for r in rows],
                        [r["score"] for r in rows], list(range(len(rows)))))
        fallback.recovered("vector search")
        return out
    except corpus_db.Unavailable as exc:
        fallback.used("vector search", str(exc))

    index, chunks, _ = load_store()
    scores, ids = index.search(qemb, min(n, index.ntotal))
    return [(chunks, list(scores[i]), list(ids[i])) for i in range(len(qemb))]


def retrieve_many(questions: list[str], k: int | None = None, *,
                  dense_k: int | None = None, rerank: bool = True,
                  batch: int | None = None,
                  progress: bool = False) -> list[list[Retrieved]]:
    """Same pipeline as `retrieve`, batched over many questions."""
    if not questions:
        return []
    k = k or config.FINAL_K
    dense_k = dense_k or config.DENSE_K
    qemb = embed(questions, batch=batch)
    hits = _search(qemb, dense_k * OVERFETCH)

    results = []
    for i in range(len(questions)):
        chunks, scores, ids = hits[i]
        cands = _collapse(chunks, scores, ids, dense_k)
        if rerank and cands:
            pairs = [(questions[i], c["chunk"]["text"]) for c in cands]
            for c, s in zip(cands, rerank_scores(pairs)):
                c["score"] = s
            cands.sort(key=lambda c: -c["score"])
        else:
            for c in cands:
                c["score"] = c["dense"]
        results.append(_to_results(cands, k))
        if progress and (i + 1) % 100 == 0:
            print(f"  {i+1}/{len(questions)}", flush=True)
    return results


def retrieve(question: str, k: int | None = None, *,
             dense_k: int | None = None, rerank: bool = True) -> list[Retrieved]:
    """Top-k sections for a question, best first.

    `rerank=False` gives the dense-only ordering — kept so the two can be
    compared on one code path rather than across two scripts, which is how the
    eval numbers and the served numbers diverged in the first place.
    """
    if not question.strip():
        return []
    return retrieve_many([question], k, dense_k=dense_k, rerank=rerank)[0]


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
