"""[Auth 2] Google SSO + session management.

Three concerns:
    1. Build the Google OAuth redirect URL + handle the callback (token exchange)
    2. Mint a session JWT and store it in an httpOnly cookie
    3. Provide a FastAPI dependency: `get_current_user(request) -> User | None`

Config required (env vars; missing → SSO disabled, anonymous-only mode):
    GOOGLE_OAUTH_CLIENT_ID
    GOOGLE_OAUTH_CLIENT_SECRET
    GOOGLE_OAUTH_REDIRECT_URI    (e.g. https://advisor.colaberry.ai/auth/callback)
    LIBRARY_SESSION_SECRET       (JWT signing key — 32+ random bytes)

Domain → company mapping lives in config/library_tenant_domains.json
(see [Auth 1] tenancy module for the User schema this populates).

When SSO env is missing, `is_enabled()` returns False; routers can
short-circuit to anonymous mode without crashing — useful for local
dev and lets the existing prod box keep running until Ali registers the
OAuth app.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import tenancy

LAYER = "platform_core"
PRODUCT = "library"

ROOT = Path(__file__).resolve().parents[3]
TENANT_DOMAINS_PATH = ROOT / "config" / "library_tenant_domains.json"


# ── Config + readiness ────────────────────────────────────────────


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def is_enabled() -> bool:
    """SSO is enabled only when the four config values are present."""
    return all(_env(k) for k in (
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "GOOGLE_OAUTH_REDIRECT_URI",
        "LIBRARY_SESSION_SECRET",
    ))


def disabled_reason() -> str:
    missing = [k for k in (
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "GOOGLE_OAUTH_REDIRECT_URI",
        "LIBRARY_SESSION_SECRET",
    ) if not _env(k)]
    if not missing:
        return "enabled"
    return f"disabled — missing env: {', '.join(missing)}"


def _load_tenant_domains() -> dict[str, Any]:
    if not TENANT_DOMAINS_PATH.exists():
        return {"mappings": [], "unmatched_policy": "queue_for_admin_review",
                  "anonymous_paths": ["/", "/static/", "/docs"],
                  "login_required_paths": ["/library/"]}
    return json.loads(TENANT_DOMAINS_PATH.read_text(encoding="utf-8"))


# ── Path-policy helpers (used by middleware) ─────────────────────


def path_requires_login(path: str) -> bool:
    cfg = _load_tenant_domains()
    for p in cfg.get("login_required_paths", []):
        if path.startswith(p):
            return True
    return False


def path_is_anonymous(path: str) -> bool:
    cfg = _load_tenant_domains()
    for p in cfg.get("anonymous_paths", []):
        if path.startswith(p):
            return True
    return False


def resolve_company_for_email(email: str) -> tuple[str | None, dict | None]:
    """Returns (company_id, mapping_entry) or (None, None) when no match."""
    if "@" not in email:
        return (None, None)
    domain = email.split("@", 1)[1].lower()
    for m in _load_tenant_domains().get("mappings", []):
        if m.get("domain", "").lower() == domain:
            return (m.get("company_id"), m)
    return (None, None)


# ── OAuth URL building + token exchange ──────────────────────────


_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
_DEFAULT_SCOPES = "openid email profile"


def build_login_url(state: str | None = None,
                          extra_scopes: str = "") -> str:
    """Returns the Google consent URL to redirect the user to."""
    if not is_enabled():
        raise RuntimeError(f"Auth disabled: {disabled_reason()}")
    state = state or secrets.token_urlsafe(24)
    scopes = (_DEFAULT_SCOPES + " " + extra_scopes).strip()
    qs = urllib.parse.urlencode({
        "client_id": _env("GOOGLE_OAUTH_CLIENT_ID"),
        "redirect_uri": _env("GOOGLE_OAUTH_REDIRECT_URI"),
        "response_type": "code",
        "scope": scopes,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    })
    return f"{_AUTH_URL}?{qs}"


def exchange_code_for_userinfo(code: str) -> dict[str, Any]:
    """Trade the OAuth code for a userinfo dict.

    Returns: {sub, email, email_verified, name, picture, locale, ...}
    Raises RuntimeError on any HTTP/parse failure.
    """
    if not is_enabled():
        raise RuntimeError(f"Auth disabled: {disabled_reason()}")

    # Step 1: code → tokens
    body = urllib.parse.urlencode({
        "code": code,
        "client_id": _env("GOOGLE_OAUTH_CLIENT_ID"),
        "client_secret": _env("GOOGLE_OAUTH_CLIENT_SECRET"),
        "redirect_uri": _env("GOOGLE_OAUTH_REDIRECT_URI"),
        "grant_type": "authorization_code",
    }).encode()
    req = urllib.request.Request(_TOKEN_URL, data=body, method="POST",
                                              headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            tokens = json.loads(r.read())
    except Exception as e:
        raise RuntimeError(f"token exchange failed: {e}")

    access_token = tokens.get("access_token")
    if not access_token:
        raise RuntimeError(f"no access_token in token response: {tokens}")

    # Step 2: access_token → userinfo
    ui_req = urllib.request.Request(_USERINFO_URL, headers={
        "Authorization": f"Bearer {access_token}",
    })
    try:
        with urllib.request.urlopen(ui_req, timeout=15) as r:
            return json.loads(r.read())
    except Exception as e:
        raise RuntimeError(f"userinfo fetch failed: {e}")


# ── User provisioning on first login ─────────────────────────────


def provision_or_lookup_user(google_userinfo: dict[str, Any]) -> tuple[tenancy.User | None, str]:
    """Look up by email, or auto-provision if the domain maps to a tenant.

    Returns (user, status). status is one of:
      "ok"                    — existing user, recorded a login
      "provisioned"           — new user created
      "queued_for_review"     — domain unmatched, no User created
      "rejected_unverified"   — email not verified by Google
    """
    email = (google_userinfo.get("email") or "").strip().lower()
    if not email:
        return (None, "rejected_unverified")
    if not google_userinfo.get("email_verified", False):
        return (None, "rejected_unverified")

    existing = tenancy.get_user(email)
    if existing:
        if not existing.google_subject and google_userinfo.get("sub"):
            existing.google_subject = google_userinfo["sub"]
            tenancy.upsert_user(existing)
        tenancy.record_login(existing.user_id)
        return (existing, "ok")

    company_id, mapping = resolve_company_for_email(email)
    if not company_id or not (mapping and mapping.get("auto_provision", False)):
        return (None, "queued_for_review")

    user = tenancy.User(
        user_id=tenancy._new_id("usr"),
        email=email,
        company_id=company_id,
        display_name=google_userinfo.get("name", email.split("@")[0]),
        roles=mapping.get("default_roles", ["consumer"]),
        google_subject=google_userinfo.get("sub"),
    )
    tenancy.upsert_user(user)
    tenancy.record_login(user.user_id)
    return (user, "provisioned")


# ── Session JWT — minimal HS256 hand-rolled (no extra dep) ───────


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * ((4 - len(s) % 4) % 4)
    return base64.urlsafe_b64decode(s + pad)


def issue_session_token(user: tenancy.User, ttl_seconds: int = 86400) -> str:
    """Issue a compact JWT-like signed token. Stored in an httpOnly cookie."""
    secret = _env("LIBRARY_SESSION_SECRET")
    if not secret:
        raise RuntimeError("LIBRARY_SESSION_SECRET not set")
    header = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    payload = {
        "sub": user.user_id,
        "email": user.email,
        "company": user.company_id,
        "roles": user.roles,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    h = _b64url(json.dumps(header, separators=(",", ":")).encode())
    p = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{h}.{p}".encode()
    sig = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    return f"{h}.{p}.{_b64url(sig)}"


def verify_session_token(token: str) -> dict[str, Any] | None:
    """Returns the payload dict if valid, else None."""
    secret = _env("LIBRARY_SESSION_SECRET")
    if not secret or not token or token.count(".") != 2:
        return None
    h, p, s = token.split(".")
    signing_input = f"{h}.{p}".encode()
    expected = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    try:
        actual = _b64url_decode(s)
    except Exception:
        return None
    if not hmac.compare_digest(expected, actual):
        return None
    try:
        payload = json.loads(_b64url_decode(p))
    except Exception:
        return None
    if int(time.time()) > int(payload.get("exp", 0)):
        return None
    return payload


# ── FastAPI dependency-style helpers (decoupled from FastAPI imports) ──


SESSION_COOKIE_NAME = "library_session"


def current_user_from_cookie(cookie_value: str | None) -> tenancy.User | None:
    """Resolve a logged-in user from a cookie. Returns None if unauth / bad."""
    if not cookie_value:
        return None
    payload = verify_session_token(cookie_value)
    if not payload:
        return None
    return tenancy.get_user(payload.get("sub", ""))
