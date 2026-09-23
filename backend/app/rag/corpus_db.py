"""The statute corpus, read from Supabase — the app's only primary store.

Two jobs, and they are read differently on purpose.

**Exact lookups** — the catalog. The planner's validation asks the catalog
several small questions per request (does this Act exist, which Acts have a
section 25, what is Chapter XVI called). Each one a network round trip would add
up to half a second a question for data that only changes when an Act is
reloaded. So the `law_*` structural tables are pulled once into an in-memory
SQLite database with exactly the schema `catalog.sqlite` has
(`catalog_schema.SCHEMA`), and `catalog.py` runs its unchanged queries against
it. Supabase is the store; memory is a cache of it.

The cache follows the database rather than the clock alone: every
`REFRESH_SECONDS` it compares `law_loads` — one digest per Act — with what it
built from, and rebuilds when an Act was loaded, reloaded or removed. That is
what lets a new Act go live without a restart.

**Meaning search** — the vectors. Not cached. Each question's embedding goes to
`law_match_chunks`, an exact inner-product scan in Postgres, so a newly loaded
Act is searchable on the very next question and there is no index file to ship.

Only Acts with a commit marker in `law_loads` are used. The loader writes that
row last; an Act without it was interrupted mid-load and is not trusted.

**When Supabase fails**, both functions raise `Unavailable` and the caller falls
back to the local files (`catalog.connect`, `pipeline._search`), logging it via
`fallback.py`. A failure also opens a breaker for `DOWN_SECONDS`: during an
outage every catalog lookup would otherwise wait out a network timeout before
falling back, several times per question. With the breaker open they go straight
to the fallback, and Supabase is tried again when it closes.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time

import numpy as np

from app.rag.catalog_schema import SCHEMA

REFRESH_SECONDS = 60
DOWN_SECONDS = 60
PAGE = 1000

log = logging.getLogger("ainshonglap")

_lock = threading.Lock()
_state: dict = {"con": None, "version": None, "checked": 0.0,
                "down_until": 0.0, "down_reason": ""}


class Unavailable(RuntimeError):
    """Supabase could not answer; the caller should use the local fallback."""


def _trip(exc: Exception) -> str:
    """Open the breaker after a failure. Returns the reason, for the log."""
    reason = f"{type(exc).__name__}: {exc}"[:160]
    _state["down_until"] = time.time() + DOWN_SECONDS
    _state["down_reason"] = reason
    return reason


def _open() -> str | None:
    """Why Supabase is being skipped right now, or None if it may be tried."""
    if time.time() < _state["down_until"]:
        return _state["down_reason"] or "recently unreachable"
    return None


def _client():
    from app.db import supabase

    return supabase.client()


def _fetch(table: str, columns: str, order: str) -> list[dict]:
    """Every row of a table, paged — the API caps a response at ~1000 rows."""
    out, start, c = [], 0, _client()
    while True:
        page = (c.table(table).select(columns).order(order)
                .range(start, start + PAGE - 1).execute().data)
        out.extend(page)
        if len(page) < PAGE:
            return out
        start += PAGE


def version() -> tuple:
    """What is loaded, as (slug, digest) pairs. Changes whenever an Act does."""
    rows = _client().table("law_loads").select("act_slug,digest").order("act_slug").execute().data
    return tuple((r["act_slug"], r["digest"]) for r in rows)


def _hydrate(loaded: tuple) -> sqlite3.Connection:
    """Rebuild catalog.sqlite's contents in memory from the law_* tables."""
    from app.rag import trace

    t0 = time.time()
    trusted = {slug for slug, _ in loaded}
    con = sqlite3.connect(":memory:", check_same_thread=False)
    con.executescript(SCHEMA)

    # The database keys rows by natural text ids; the catalog schema uses
    # integers. These maps translate, in a fixed order so ids are stable.
    act_id, chapter_id, section_id, sub_id = {}, {}, {}, {}

    for i, a in enumerate(r for r in _fetch("law_acts", "*", "slug")
                          if r["slug"] in trusted):
        act_id[a["slug"]] = i + 1
        con.execute("INSERT INTO acts VALUES (?,?,?,?,?,?,?)",
                    (i + 1, a["slug"], a["name"], a["act_no"], a["source_pdf"],
                     a["n_chapters"], a["n_sections"]))

    # Ordered as build_catalog.py numbers them — by Act, then position — so
    # the integer ids match catalog.sqlite exactly and anything that returns
    # or sorts by id behaves identically on both stores. Ordering by the text
    # id instead would put 'ch10' before 'ch2'.
    chapters = sorted((r for r in _fetch("law_chapters", "*", "id")
                       if r["act_slug"] in trusted),
                      key=lambda r: (act_id[r["act_slug"]], r["seq"]))
    for i, ch in enumerate(chapters):
        chapter_id[ch["id"]] = i + 1
        con.execute("INSERT INTO chapters VALUES (?,?,?,?,?,?,?,?,?)",
                    (i + 1, act_id[ch["act_slug"]], ch["number"], ch["number_int"],
                     ch["title"], ch["part"], ch["part_title"], ch["seq"],
                     ch["n_sections"]))

    sections = sorted((r for r in _fetch(
        "law_sections",
        "key,act_slug,chapter_id,number,number_to,num_int,num_suffix,title,text,"
        "page_start,page_end,seq,dup_seq,continuation", "key")
        if r["act_slug"] in trusted), key=lambda r: (act_id[r["act_slug"]], r["seq"]))
    for i, s in enumerate(sections):
        section_id[s["key"]] = i + 1
        con.execute("INSERT INTO sections VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (i + 1, act_id[s["act_slug"]], chapter_id.get(s["chapter_id"]),
                     s["number"], s["number_to"], s["num_int"], s["num_suffix"],
                     s["title"], s["text"], s["page_start"], s["page_end"],
                     s["seq"], s["key"], s["dup_seq"], int(s["continuation"])))

    subs = sorted((r for r in _fetch("law_subsections", "*", "id")
                   if r["section_key"] in section_id),
                  key=lambda r: (section_id[r["section_key"]], r["seq"]))
    for i, ss in enumerate(subs):
        sub_id[ss["id"]] = i + 1
    for ss in subs:
        con.execute("INSERT INTO subsections VALUES (?,?,?,?,?,?,?,?)",
                    (sub_id[ss["id"]], section_id[ss["section_key"]],
                     sub_id.get(ss["parent_id"]), ss["number"], ss["type"],
                     ss["text"], ss["depth"], ss["seq"]))

    # 'crpc-s155:r10' sorts before ':r2' as text; the loader numbered them in
    # catalog id order, so sort on that number.
    xrefs = sorted((r for r in _fetch("law_cross_references", "*", "id")
                    if r["section_key"] in section_id),
                   key=lambda r: (section_id[r["section_key"]],
                                  int(r["id"].rsplit(":r", 1)[1])))
    for i, x in enumerate(xrefs):
        con.execute("INSERT INTO cross_references VALUES (?,?,?,?,?,?,?)",
                    (i + 1, section_id[x["section_key"]],
                     act_id.get(x["target_act_slug"]), x["target_number"],
                     x["kind"], x["target_to"], x["raw"]))

    # The same statement build_catalog.py uses, so the full-text index is built
    # identically whichever store it came from.
    con.execute("INSERT INTO sections_fts(rowid, title, text)"
                " SELECT id, COALESCE(title,''), text FROM sections")
    con.commit()
    con.row_factory = sqlite3.Row
    trace.step("corpus", f"catalog from Supabase: {len(act_id)} acts, "
                         f"{len(section_id)} sections", time.time() - t0)
    return con


def connection() -> sqlite3.Connection:
    """The in-memory catalog, rebuilt when `law_loads` says the corpus changed.

    Raises `Unavailable` only when there is no copy at all — the first build
    failed, or the breaker is open before one existed. Once a copy exists, a
    failed refresh keeps serving it: that is still Supabase's data, one minute
    old at most, which beats the local files' data of unknown age.
    """
    with _lock:
        now = time.time()
        con = _state["con"]
        if con is not None and now - _state["checked"] < REFRESH_SECONDS:
            return con
        if con is None and (why := _open()):
            raise Unavailable(why)
        if con is not None and _open():
            return con
        try:
            current = version()
            if con is None or current != _state["version"]:
                con = _hydrate(current)
                _state["con"], _state["version"] = con, current
                _on_change()
        except Exception as exc:
            reason = _trip(exc)
            _state["checked"] = now
            if _state["con"] is None:
                raise Unavailable(reason) from exc
            log.warning("Supabase refresh failed — serving the catalog loaded "
                        "from it earlier, not the local files: %s", reason)
            return _state["con"]
        _state["checked"] = now
        return con


def _on_change() -> None:
    """Drop everything derived from the old catalog, so nothing serves stale."""
    from app.rag import catalog, library

    catalog.act_slugs.cache_clear()
    library._library.cache_clear()


def match(query: np.ndarray, k: int) -> list[dict]:
    """Exact nearest chunks for one query vector, best first.

    Rows carry `text`, `metadata` and `score` — the shape the pipeline groups
    into sections. `score` is the inner product, the same number FAISS returns.
    Raises `Unavailable` on any failure, or at once while the breaker is open.
    """
    if why := _open():
        raise Unavailable(why)
    literal = "[" + ",".join(np.format_float_positional(x, unique=True, trim="-")
                             for x in np.asarray(query, dtype=np.float32)) + "]"
    try:
        return _client().rpc("law_match_chunks",
                             {"query_embedding": literal, "match_count": k}).execute().data
    except Exception as exc:
        raise Unavailable(_trip(exc)) from exc


def status() -> dict:
    """For /health: what the catalog was built from, and whether Supabase is up."""
    return {"catalog_loaded": _state["con"] is not None,
            "acts": [slug for slug, _ in (_state["version"] or ())],
            "unavailable": _open()}
