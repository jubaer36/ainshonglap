"""E21: OpenRouter's free embedders and reranker vs BGE-M3 + bge-reranker.
Caches every result so a run stopped by the daily cap resumes.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import time

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env.local")

from app.rag import config  # noqa: E402

CACHE = config.ROOT / "Laws" / "experiments" / "openrouter"
API = "https://openrouter.ai/api/v1"

EMBEDDERS = {
    "nemotron-3-embed": {"model": "nvidia/nemotron-3-embed-1b:free",
                         "query": {"input_type": "query"}, "doc": {"input_type": "passage"}},
    "nemotron-embed-vl": {"model": "nvidia/llama-nemotron-embed-vl-1b-v2:free",
                          "query": {"input_type": "query"}, "doc": {"input_type": "passage"}},
    "lfm-embed": {"model": "liquid/lfm-2.5-embedding-350m:free",
                  "query": {"prefix": "query: "}, "doc": {"prefix": "document: "},
                  "max_tokens": 512},
}
RERANKER = "nvidia/llama-nemotron-rerank-vl-1b-v2:free"
DENSE_K = 20
OVERFETCH = 3


def _headers():
    return {"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
            "Content-Type": "application/json"}


class Capped(RuntimeError):
    """The free-model daily cap: stop, keep the cache, resume tomorrow."""


def _post(path: str, body: dict, tries: int = 6) -> dict:
    import requests

    wait = 5.0
    for attempt in range(tries):
        try:
            r = requests.post(f"{API}/{path}", headers=_headers(), json=body, timeout=120)
        except requests.RequestException as exc:
            err = str(exc)
        else:
            if r.status_code == 200:
                d = r.json()
                if "error" not in d:
                    return d
                err = json.dumps(d["error"])[:300]
            else:
                err = f"HTTP {r.status_code}: {r.text[:300]}"
            if r.status_code == 400:
                raise ValueError(err)
            if r.status_code == 429 and "per-day" in r.text.lower():
                raise Capped(err)
        if attempt == tries - 1:
            raise RuntimeError(err)
        print(f"    retry in {wait:.0f}s — {err[:160]}", flush=True)
        time.sleep(wait)
        wait = min(wait * 2, 120)
    raise RuntimeError("unreachable")


def load_corpus():
    chunks = json.loads((config.INDEX_DIR / "chunks.json").read_text(encoding="utf-8"))
    meta = json.loads((config.INDEX_DIR / "meta.json").read_text(encoding="utf-8"))
    return chunks, meta


def load_questions():
    from eval_retrieval import load_eval

    return load_eval(None)


# ------------------------------------------------------------------ embedding

def _fit(text: str, limit: int) -> str:
    """Cut a text to roughly `limit` tokens, by BGE-M3's tokenizer as a proxy."""
    from app.rag.encoder import load_encoder

    tok = load_encoder()[0]
    ids = tok(text, add_special_tokens=False)["input_ids"]
    if len(ids) <= limit:
        return text
    return tok.decode(ids[:limit], skip_special_tokens=True)


def _embed_batch(spec: dict, texts: list[str], side: str) -> list[list[float]]:
    how = spec[side]
    body = {"model": spec["model"],
            "input": [how.get("prefix", "") + t for t in texts]}
    if "input_type" in how:
        body["input_type"] = how["input_type"]
    d = _post("embeddings", body)
    got = sorted(d["data"], key=lambda x: x["index"])
    return [x["embedding"] for x in got]


def embed_all(name: str, texts: list[str], side: str, batch: int) -> np.ndarray:
    """Vectors for `texts`, cached; resumable batch by batch."""
    spec = EMBEDDERS[name]
    out_path = CACHE / f"{name}.{side}.npy"
    if out_path.exists():
        return np.load(out_path)
    part_path = CACHE / f"{name}.{side}.partial.json"
    done: dict[str, list[float]] = (json.loads(part_path.read_text())
                                    if part_path.exists() else {})
    uniq = list(dict.fromkeys(texts))
    todo = [t for t in uniq if t not in done]
    limit = spec.get("max_tokens")
    cut = 0
    print(f"  {name} {side}: {len(uniq)} unique texts, {len(todo)} to fetch", flush=True)
    t0, calls = time.time(), 0
    for i in range(0, len(todo), batch):
        group = todo[i:i + batch]
        send = group
        if limit:
            # Leave room for the prefix and the tokenizer difference.
            send = [_fit(t, limit - 40) for t in group]
            cut += sum(1 for a, b in zip(group, send) if a != b)
        try:
            vecs = _embed_batch(spec, send, side)
            calls += 1
        except ValueError:
            # One over-long input fails the whole batch; retry one at a time,
            # cutting harder on the ones that still do not fit.
            vecs = []
            for t in send:
                for lim in ([limit - 80, limit - 160, limit - 240] if limit else [None]):
                    try:
                        vecs.extend(_embed_batch(spec, [_fit(t, lim) if lim else t], side))
                        calls += 1
                        break
                    except ValueError:
                        continue
                else:
                    raise
        for t, v in zip(group, vecs):
            done[t] = v
        part_path.write_text(json.dumps(done))
        if (i // batch) % 10 == 0:
            print(f"    {min(i + batch, len(todo))}/{len(todo)}  "
                  f"{calls} calls, {time.time() - t0:.0f}s", flush=True)
    emb = np.array([done[t] for t in texts], dtype="float32")
    emb /= np.linalg.norm(emb, axis=1, keepdims=True)
    np.save(out_path, emb)
    part_path.unlink(missing_ok=True)
    if cut:
        print(f"  {name} {side}: {cut} texts cut to fit {limit} tokens")
        (CACHE / f"{name}.{side}.cut").write_text(str(cut))
    print(f"  {name} {side}: done, {calls} calls, {time.time() - t0:.0f}s", flush=True)
    return emb


def bge_vectors(questions: list[str]):
    docs = np.load(config.INDEX_DIR / "embeddings.npy").astype("float32")
    q_path = CACHE / "bge-m3.query.npy"
    if q_path.exists():
        return docs, np.load(q_path)
    from app.rag.encoder import embed

    q = embed(questions, batch=32)
    np.save(q_path, q)
    return docs, q


# ------------------------------------------------------------------ candidates

def candidates(name: str, chunks, questions) -> list[list[dict]]:
    """20 distinct-section candidates per question, from one embedder."""
    from app.rag.pipeline import _collapse

    if name == "bge-m3":
        docs, q = bge_vectors(questions)
    else:
        docs = np.load(CACHE / f"{name}.doc.npy")
        q = np.load(CACHE / f"{name}.query.npy")
    sims = q @ docs.T
    n = DENSE_K * OVERFETCH
    top = np.argsort(-sims, axis=1)[:, :n]
    out = []
    for i in range(len(questions)):
        ids = list(top[i])
        cands = _collapse(chunks, [float(sims[i, j]) for j in ids], ids, DENSE_K)
        out.append(cands)
    return out


def rerank_bge(questions, cands, name) -> list[list[float]]:
    path = CACHE / f"rerank.bge.on-{name}.json"
    if path.exists():
        return json.loads(path.read_text())
    from app.rag.reranker import rerank_scores

    t0 = time.time()
    out = []
    for q, cs in zip(questions, cands):
        out.append(rerank_scores([(q, c["chunk"]["text"]) for c in cs]))
    path.write_text(json.dumps(out))
    print(f"  bge-reranker on {name}: {time.time() - t0:.0f}s local", flush=True)
    return out


def rerank_nemotron(questions, cands, name) -> list[list[float]] | None:
    """Scores per candidate; resumable. None while incomplete."""
    path = CACHE / f"rerank.nemotron.on-{name}.json"
    done: dict[str, list[float]] = json.loads(path.read_text()) if path.exists() else {}
    todo = [i for i in range(len(questions)) if str(i) not in done]
    if todo:
        print(f"  nemotron-rerank on {name}: {len(todo)} questions to fetch", flush=True)
    t0 = time.time()
    try:
        for n, i in enumerate(todo, 1):
            docs = [c["chunk"]["text"] for c in cands[i]]
            d = _post("rerank", {"model": RERANKER, "query": questions[i],
                                 "documents": docs})
            scores = [0.0] * len(docs)
            for r in d["results"]:
                scores[r["index"]] = float(r["relevance_score"])
            done[str(i)] = scores
            if n % 25 == 0 or n == len(todo):
                path.write_text(json.dumps(done))
                print(f"    {n}/{len(todo)}  {time.time() - t0:.0f}s", flush=True)
    except Capped as exc:
        path.write_text(json.dumps(done))
        print(f"  STOPPED at the daily cap after {len(done)} questions: {exc}")
    path.write_text(json.dumps(done))
    if len(done) < len(questions):
        return None
    return [done[str(i)] for i in range(len(questions))]


# ------------------------------------------------------------------ scoring

def ranked(cands, scores=None, k: int = 20):
    from app.rag.pipeline import _to_results

    out = []
    for i, cs in enumerate(cands):
        cs = [dict(c) for c in cs]
        for j, c in enumerate(cs):
            c["score"] = scores[i][j] if scores is not None else c["dense"]
        cs.sort(key=lambda c: -c["score"])
        out.append(_to_results(cs, k))
    return out


def evaluate(results, rows) -> dict:
    from eval_retrieval import score

    s = score(results, rows, [1, 5, 20])
    # Per-question R@1 hits, for a paired comparison against the baseline.
    s["hits1"] = [bool(got) and got[0].act.strip().lower() == row["act"].lower()
                  and got[0].section == row["section"] for row, got in zip(rows, results)]
    return s


def paired(a: list[bool], b: list[bool]) -> str:
    """Exact McNemar on R@1: only the questions the two disagree on count."""
    from math import comb

    win = sum(1 for x, y in zip(a, b) if y and not x)
    loss = sum(1 for x, y in zip(a, b) if x and not y)
    n = win + loss
    if n == 0:
        return "identical"
    k = min(win, loss)
    p = min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2 ** n)
    return f"+{win}/-{loss}, p={p:.3f}"


def report(chunks, rows, questions):
    lines = []
    names = ["bge-m3"] + [n for n in EMBEDDERS if (CACHE / f"{n}.query.npy").exists()]
    base = None
    for name in names:
        cands = candidates(name, chunks, questions)
        variants = [("dense only", None), ("+ bge-reranker", rerank_bge(questions, cands, name))]
        # Read from the cache only — the report never spends API calls.
        path = CACHE / f"rerank.nemotron.on-{name}.json"
        got = json.loads(path.read_text()) if path.exists() else {}
        nem = [got[str(i)] for i in range(len(questions))] \
            if len(got) == len(questions) else None
        if nem is not None:
            variants.append(("+ nemotron-rerank", nem))
        for label, sc in variants:
            s = evaluate(ranked(cands, sc), rows)
            if base is None and label == "+ bge-reranker":
                base = s
            vs = paired(base["hits1"], s["hits1"]) if base and s is not base else "—"
            lines.append((f"{name} {label}", s, vs))
    print(f"\n{'pipeline':<36} {'R@1':>6} {'R@5':>6} {'R@20':>6} {'MRR':>6} {'Act':>6}   R@1 vs current (McNemar)")
    for label, s, vs in lines:
        print(f"{label:<36} {s['R@1']:>5.1f}% {s['R@5']:>5.1f}% {s['R@20']:>5.1f}% "
              f"{s['MRR']:>6.3f} {s['act']:>5.1f}%   {vs}")
    for n in EMBEDDERS:
        f = CACHE / f"{n}.doc.cut"
        if f.exists():
            print(f"note: {n} — {f.read_text()} chunks cut to fit its input limit")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["embed", "rerank", "report"], required=True)
    ap.add_argument("--models", nargs="*", default=list(EMBEDDERS))
    ap.add_argument("--on", default="bge-m3", help="candidate set for --stage rerank")
    ap.add_argument("--batch", type=int, default=32)
    args = ap.parse_args()

    CACHE.mkdir(parents=True, exist_ok=True)
    chunks, meta = load_corpus()
    rows = load_questions()
    questions = [r["question"] for r in rows]
    print(f"corpus: {len(chunks)} chunks ({meta['model']})   eval: {len(rows)} questions")

    if args.stage == "embed":
        texts = [c["text"] for c in chunks]
        for name in args.models:
            try:
                embed_all(name, questions, "query", args.batch)
                embed_all(name, texts, "doc", args.batch)
            except Capped as exc:
                print(f"  STOPPED at the daily cap during {name}: {exc}")
                return
    elif args.stage == "rerank":
        cands = candidates(args.on, chunks, questions)
        rerank_nemotron(questions, cands, args.on)
    else:
        report(chunks, rows, questions)


if __name__ == "__main__":
    main()
