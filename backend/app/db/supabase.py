"""Supabase client and access-token verification.

**Everything goes through this API, with the service-role key.** That key
bypasses Row Level Security by design, so the policies in `db/schema.sql` are
not what keeps one person's legal questions away from another — the `user_id`
filter in `app/db/conversations.py` is. Every statement there passes it, and the
policies remain as a second line for anything that ever connects with an anon
key.

Token verification is local. The frontend signs in with Supabase and sends the
access token as a bearer; this reads the token's own `alg` header and picks the
method:

    RS256 / ES256   new projects, asymmetric. The public key comes from the
                    project's JWKS endpoint, cached, so a request never waits on
                    Supabase.
    HS256           older projects with a shared secret in SUPABASE_JWT_SECRET.

Unconfigured is a supported state, not an error. With no keys the API runs
exactly as it did before — anonymous, with conversations living in the browser —
so the assistant works for someone who has not signed in and for a deployment
with no Supabase at all.
"""

from __future__ import annotations

import functools
import os
import pathlib

ENV_FILE = pathlib.Path(__file__).resolve().parents[2] / ".env.local"

# Supabase always issues access tokens with this audience.
AUDIENCE = "authenticated"


def _env(name: str) -> str:
    from dotenv import load_dotenv

    load_dotenv(ENV_FILE, override=False)
    return (os.environ.get(name) or "").strip()


def url() -> str:
    return _env("SUPABASE_URL")


def bucket() -> str:
    return _env("SUPABASE_BUCKET") or "page-images"


def configured() -> bool:
    """Whether storage and auth are available at all."""
    return bool(url() and _env("SUPABASE_SERVICE_ROLE_KEY"))


@functools.lru_cache(maxsize=1)
def client():
    """Service-role client. Cached: constructing one opens a session."""
    from supabase import create_client

    if not configured():
        raise RuntimeError("Supabase is not configured (SUPABASE_URL / "
                           "SUPABASE_SERVICE_ROLE_KEY missing)")
    return create_client(url(), _env("SUPABASE_SERVICE_ROLE_KEY"))


@functools.lru_cache(maxsize=1)
def _jwks_client():
    """Public keys for asymmetric tokens, fetched once and cached by PyJWT.

    PyJWT's client refreshes on an unknown key id, so a signing-key rotation
    recovers by itself rather than needing a restart.
    """
    from jwt import PyJWKClient

    return PyJWKClient(f"{url()}/auth/v1/.well-known/jwks.json",
                       cache_keys=True, lifespan=3600)


def verify(token: str) -> dict | None:
    """Claims from a valid access token, or None.

    Returns None rather than raising: an expired or malformed token means "not
    signed in", which is a supported state here, not a failure. Signature,
    expiry and audience are all checked — decoding without verification would
    let anyone claim any `sub` and read another user's conversations.
    """
    import jwt

    token = (token or "").strip()
    if not token or not url():
        return None
    try:
        algorithm = (jwt.get_unverified_header(token) or {}).get("alg", "")
        if algorithm.startswith(("RS", "ES", "PS")):
            key = _jwks_client().get_signing_key_from_jwt(token).key
        elif algorithm == "HS256":
            secret = _env("SUPABASE_JWT_SECRET")
            if not secret:
                return None
            key = secret
        else:
            return None
        return jwt.decode(token, key, algorithms=[algorithm],
                          audience=AUDIENCE,
                          options={"require": ["exp", "sub"]})
    except Exception:
        return None


def is_admin(token: str | None) -> bool:
    """Is this a signed-in user allowed to add and remove sources?

    `ADMIN_EMAILS` in backend/.env.local — comma-separated. Checked against the
    email in a VERIFIED token, so it cannot be claimed by sending a header; with
    the variable unset, nobody is an admin and the controls do not appear.
    """
    claims = verify(token or "")
    if not claims:
        return False
    allowed = {e.strip().lower() for e in _env("ADMIN_EMAILS").split(",") if e.strip()}
    email = str(claims.get("email") or "").strip().lower()
    return bool(email) and email in allowed and bool(claims.get("sub"))


def user_id(token: str | None) -> str | None:
    """The signed-in user's id, or None for an anonymous request."""
    claims = verify(token or "")
    return claims.get("sub") if claims else None


def state() -> dict:
    """What /health reports about the database."""
    return {"configured": configured(), "bucket": bucket() if configured() else None,
            "jwt": ("shared-secret" if _env("SUPABASE_JWT_SECRET") else "jwks")}
