"""Adding and removing sources from the frontend — admins only.

    GET    /api/sources/admin              am I allowed to? (drives the UI)
    POST   /api/sources                    upload a PDF; starts its review
    GET    /api/sources/jobs               documents still being added, or failed
    GET    /api/sources/jobs/{id}          progress, and the review when ready
    POST   /api/sources/jobs/{id}/choose   {"parser": "pymupdf" | "docling"}
    POST   /api/sources/jobs/{id}/discard  abandon an add before it is indexed
    DELETE /api/sources/{slug}             remove a source (never a core Act)

Every write needs a signed-in user whose email is in ADMIN_EMAILS, checked on
the verified token here — hiding the buttons in the UI is not the protection.
The core Acts are refused by `app.ingest.sources` whoever asks.
"""

from __future__ import annotations

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel

router = APIRouter(prefix="/api/sources", tags=["sources"])


def _token(authorization: str | None) -> str:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1]
    return ""


def _admin(authorization: str | None) -> None:
    from app.db import supabase

    token = _token(authorization)
    if not supabase.user_id(token):
        raise HTTPException(401, "sign in to manage sources")
    if not supabase.is_admin(token):
        raise HTTPException(403, "only an admin can add or remove sources")


def _manageable() -> None:
    from app.ingest import sources

    if why := sources.manageable():
        raise HTTPException(503, f"{why} — add or remove sources where the "
                                 "pipeline runs, then load them to Supabase")


def _refused(exc: Exception) -> HTTPException:
    return HTTPException(409, str(exc))


@router.get("/admin")
def am_i_admin(authorization: str | None = Header(None)) -> dict:
    from app.db import supabase
    from app.ingest import sources

    # `manage` false: an admin, but this deployment cannot run the pipeline
    # (the container) — the UI hides the controls rather than offer a failure.
    why = sources.manageable()
    return {"admin": supabase.is_admin(_token(authorization)),
            "manage": why is None, "reason": why}


@router.post("")
def add_source(file: UploadFile = File(...), slug: str = Form(...),
               name: str = Form(...), act_no: str = Form(""),
               authorization: str | None = Header(None)) -> dict:
    """Save the PDF and start its review. Returns the job to poll."""
    from app.ingest import sources

    _admin(authorization)
    _manageable()
    data = file.file.read(sources.MAX_UPLOAD_BYTES + 1)
    try:
        job = sources.start_add(data, file.filename or f"{slug}.pdf", slug, name, act_no)
    except sources.Refused as exc:
        raise _refused(exc) from exc
    return job.public()


@router.get("/jobs")
def unfinished_jobs(authorization: str | None = Header(None)) -> list[dict]:
    """Documents still being added (or failed), for the sources list."""
    from app.ingest import sources

    _admin(authorization)
    _manageable()
    return [j.public(with_review=False) for j in sources.unfinished_adds()]


@router.get("/jobs/{job_id}")
def job_status(job_id: str, authorization: str | None = Header(None)) -> dict:
    from app.ingest import sources

    _admin(authorization)
    _manageable()
    job = sources.get(job_id)
    if job is None:
        raise HTTPException(404, "no such job — it may have been lost to a restart")
    return job.public()


class Choice(BaseModel):
    parser: str


@router.post("/jobs/{job_id}/choose")
def choose_parser(job_id: str, body: Choice,
                  authorization: str | None = Header(None)) -> dict:
    from app.ingest import sources

    _admin(authorization)
    _manageable()
    try:
        return sources.choose(job_id, body.parser).public(with_review=False)
    except sources.Refused as exc:
        raise _refused(exc) from exc


@router.post("/jobs/{job_id}/discard")
def discard(job_id: str, authorization: str | None = Header(None)) -> dict:
    from app.ingest import sources

    _admin(authorization)
    _manageable()
    try:
        return sources.discard(job_id).public(with_review=False)
    except sources.Refused as exc:
        raise _refused(exc) from exc


@router.delete("/{slug}")
def remove_source(slug: str, authorization: str | None = Header(None)) -> dict:
    from app.ingest import sources

    _admin(authorization)
    _manageable()
    try:
        return sources.start_remove(slug).public(with_review=False)
    except sources.Refused as exc:
        raise _refused(exc) from exc
