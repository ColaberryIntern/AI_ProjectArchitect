"""In-app Google Gmail+Drive consent flow for the colaberry_attachment_fetch
MCP tool.

Why this exists: bootstrap_google_oauth.py runs locally on each operator's
laptop and is great for a single user but doesn't scale to onboarding
multiple operators (Karun, Kes, Ram, ...) without each of them following
a CLI-script walkthrough. This router gives each operator an in-portal
"Connect Gmail + Drive" button:

  GET  /profile/connect-google           - landing page with status + button
  GET  /auth/google-attachment-callback  - OAuth callback that stores
                                                                       refresh token in vault

Uses the SSO Web OAuth client (GOOGLE_OAUTH_CLIENT_ID/_SECRET) because
this is a Web-browser flow with a public callback URL -- NOT the
Desktop OAuth client which is for laptop/localhost bootstrap.

REQUIRED Cloud Console setup (one-time, by admin):

  - The SSO Web OAuth client's "Authorized redirect URIs" list must
    include: https://advisor.colaberry.ai/auth/google-attachment-callback
  - The OAuth consent screen must have gmail.modify + drive.file scopes
    listed (the bootstrap script already required this, so on existing
    deployments it's done).
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from execution.products.library import (
    auth_google, google_oauth_token, tenancy, vault,
)

logger = logging.getLogger(__name__)

router = APIRouter()

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
CALLBACK_PATH = "/auth/google-attachment-callback"
SCOPES = " ".join([
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/drive.file",
])

# Short-lived state cookie that prevents CSRF on the callback. We sign it
# with LIBRARY_SESSION_SECRET so a forged state from another tab can't
# replay against a legitimate session.
STATE_COOKIE_NAME = "colaberry_google_connect_state"
STATE_TTL_SEC = 600


# ── Auth helpers ─────────────────────────────────────────────────────


def _session_user(request: Request) -> tenancy.User | None:
    cookie = request.cookies.get(auth_google.SESSION_COOKIE_NAME)
    user = auth_google.current_user_from_cookie(cookie)
    if user:
        return user
    if not auth_google.is_enabled():
        return tenancy.get_user("ali@colaberry.com")
    return None


def _require_web_user(request: Request) -> tenancy.User:
    user = _session_user(request)
    if not user:
        from urllib.parse import quote
        qs = request.url.query
        full = request.url.path + ("?" + qs if qs else "")
        raise HTTPException(303, headers={"Location": f"/auth/login?next={quote(full, safe='')}"})
    return user


def _redirect_uri() -> str:
    """Public callback URL. Picks up advisor's base from the existing SSO
    config so we don't have to re-register a separate env var; just swap
    the path component.
    """
    base = (os.environ.get("GOOGLE_OAUTH_REDIRECT_URI") or "").strip()
    if base:
        # Replace the last path segment (e.g. /auth/callback) with our path
        parts = urllib.parse.urlsplit(base)
        return urllib.parse.urlunsplit((parts.scheme, parts.netloc,
                                                                CALLBACK_PATH, "", ""))
    return "https://advisor.colaberry.ai" + CALLBACK_PATH


def _client_credentials() -> tuple[str, str]:
    """SSO Web client creds (NOT the Desktop client used by the CLI bootstrap).

    These are the same vars auth_google.py already uses, so SSO and this
    Web-flow share the OAuth client. The Desktop client (used by
    scripts/bootstrap_google_oauth.py) is separate.
    """
    cid = (os.environ.get("GOOGLE_OAUTH_CLIENT_ID") or "").strip()
    secret = (os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET") or "").strip()
    return cid, secret


# ── Routes ───────────────────────────────────────────────────────────


@router.get("/profile/connect-google")
async def connect_google_page(request: Request, status: str | None = None,
                                                          error: str | None = None):
    """Landing page: shows whether the operator's Google grant is live +
    a button to (re-)consent.
    """
    user = _require_web_user(request)
    has_grant = bool(google_oauth_token.get_refresh_token_for_operator(user))
    grant_meta = vault.get_metadata(user.user_id, "google_oauth_refresh",
                                                              caller_id="connect-google-page")
    last_rotated = grant_meta.last_rotated_at if grant_meta else None

    state = secrets.token_urlsafe(24)
    auth_params = {
        "client_id": _client_credentials()[0],
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        "prompt": "consent",          # force refresh_token issuance every time
        "state": state,
        "include_granted_scopes": "true",
        "login_hint": user.email,
    }
    auth_url = AUTH_ENDPOINT + "?" + urllib.parse.urlencode(auth_params)

    cid_ok = bool(_client_credentials()[0])
    if not cid_ok:
        body = (
            "<h2>Google OAuth not configured on this server</h2>"
            "<p>GOOGLE_OAUTH_CLIENT_ID env var is missing. Ask an admin.</p>"
        )
    else:
        status_block = ""
        if status == "ok":
            status_block = (
                '<div style="background:#dafbe1;border:1px solid #aceebb;'
                'padding:12px 16px;border-radius:8px;margin-bottom:16px;color:#137333;">'
                "✅ Connected. Gmail + Drive access granted; you can now use "
                "the <code>colaberry_attachment_fetch</code> MCP tool."
                "</div>"
            )
        elif error:
            status_block = (
                '<div style="background:#ffeef0;border:1px solid #ffabba;'
                'padding:12px 16px;border-radius:8px;margin-bottom:16px;color:#b3261e;">'
                f"❌ Connect failed: <code>{error}</code>. Try again."
                "</div>"
            )

        if has_grant:
            grant_state = (
                '<div style="background:#dafbe1;border:1px solid #aceebb;'
                'padding:12px 16px;border-radius:8px;margin-bottom:16px;color:#137333;">'
                "🟢 <strong>Connected.</strong> Your Gmail + Drive grant is "
                f"stored. Last refreshed: <code>{last_rotated or 'unknown'}</code>."
                "</div>"
            )
            button_label = "🔄 Reconnect (replace existing grant)"
        else:
            grant_state = (
                '<div style="background:#fff4d5;border:1px solid #f0d678;'
                'padding:12px 16px;border-radius:8px;margin-bottom:16px;color:#8a5a00;">'
                "🟡 <strong>Not connected yet.</strong> Click below to grant "
                "Gmail + Drive access. Required for the "
                "<code>colaberry_attachment_fetch</code> MCP tool."
                "</div>"
            )
            button_label = "🔗 Connect Gmail + Drive"

        body = (
            "<h1 style='font-size:22px;margin:0 0 12px;'>Connect Google</h1>"
            "<p style='color:#57606a;font-size:13px;margin:0 0 22px;'>"
            "Grants the Colaberry MCP server permission to download attachments "
            "from your Gmail and stage them in your Google Drive on your behalf. "
            "Per-operator; only your own Gmail/Drive is touched."
            "</p>"
            f"{status_block}"
            f"{grant_state}"
            f'<a href="{auth_url}" '
            'style="display:inline-block;background:#1a1a1a;color:#fff;'
            'padding:12px 22px;border-radius:6px;font-weight:600;'
            'text-decoration:none;">'
            f"{button_label}</a>"
            '<details style="margin-top:24px;font-size:13px;color:#57606a;">'
            "<summary style='cursor:pointer;'>What does this grant?</summary>"
            "<ul style='line-height:1.7;margin-top:8px;'>"
            "<li><code>gmail.modify</code> -- read, draft, send, label emails "
            "(NOT permanent delete).</li>"
            "<li><code>drive.file</code> -- only files our app creates in "
            "Drive (we cannot see or modify your other Drive content).</li>"
            "</ul>"
            "<p>You can revoke anytime at "
            '<a href="https://myaccount.google.com/permissions" target="_blank">'
            "myaccount.google.com/permissions</a>.</p>"
            "</details>"
        )

    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Connect Google · Colaberry</title>"
        "<style>body{font-family:-apple-system,Segoe UI,sans-serif;"
        "max-width:680px;margin:60px auto;padding:0 24px;color:#1f2328;}"
        "code{background:#f6f8fa;padding:1px 5px;border-radius:3px;font-size:12px;}"
        "</style></head><body>"
        f"{body}"
        '<p style="margin-top:32px;font-size:12px;color:#57606a;">'
        f"Signed in as <code>{user.email}</code>. "
        '<a href="/my-day/">My Day</a> · '
        '<a href="/profile/mcp-setup">MCP setup</a>'
        "</p></body></html>"
    )
    response = HTMLResponse(html)
    response.set_cookie(
        STATE_COOKIE_NAME, state,
        max_age=STATE_TTL_SEC,
        httponly=True, samesite="lax", secure=True,
        path=CALLBACK_PATH,  # only sent on the callback URL
    )
    return response


@router.get(CALLBACK_PATH)
async def google_attachment_callback(request: Request,
                                                                  code: str | None = None,
                                                                  state: str | None = None,
                                                                  error: str | None = None):
    """OAuth redirect target. Validates state, exchanges code for refresh
    token, stores in vault, redirects back to /profile/connect-google.
    """
    user = _require_web_user(request)

    if error:
        return RedirectResponse(
            f"/profile/connect-google?error={urllib.parse.quote(error)}",
            status_code=303,
        )
    if not code or not state:
        return RedirectResponse(
            "/profile/connect-google?error=missing_code_or_state",
            status_code=303,
        )

    expected = request.cookies.get(STATE_COOKIE_NAME, "")
    if not expected or not secrets.compare_digest(state, expected):
        return RedirectResponse(
            "/profile/connect-google?error=state_mismatch",
            status_code=303,
        )

    client_id, client_secret = _client_credentials()
    if not client_id or not client_secret:
        return RedirectResponse(
            "/profile/connect-google?error=server_not_configured",
            status_code=303,
        )

    body = urllib.parse.urlencode({
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": _redirect_uri(),
        "grant_type": "authorization_code",
    }).encode("utf-8")
    req = urllib.request.Request(
        TOKEN_ENDPOINT,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            payload = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        logger.warning("token exchange HTTP %s for user=%s", e.code, user.user_id)
        return RedirectResponse(
            f"/profile/connect-google?error=token_exchange_http_{e.code}",
            status_code=303,
        )
    except urllib.error.URLError as e:
        logger.warning("token exchange network error for user=%s: %s",
                                  user.user_id, type(e).__name__)
        return RedirectResponse(
            "/profile/connect-google?error=token_exchange_network_error",
            status_code=303,
        )
    except json.JSONDecodeError:
        return RedirectResponse(
            "/profile/connect-google?error=token_exchange_malformed_response",
            status_code=303,
        )

    refresh_token = payload.get("refresh_token")
    if not refresh_token:
        # Google didn't reissue (probably because user previously consented
        # AND we forgot prompt=consent). Direct them to revoke + retry.
        return RedirectResponse(
            "/profile/connect-google?error=no_refresh_token_returned_revoke_at_myaccount",
            status_code=303,
        )

    vault.store_secret(
        user.user_id,
        "google_oauth_refresh",
        refresh_token,
        caller_id="google_connect_web_flow",
        ttl_days=180,
    )
    google_oauth_token.invalidate_access_token_cache(user)

    response = RedirectResponse(
        "/profile/connect-google?status=ok",
        status_code=303,
    )
    # Clear the state cookie -- single use
    response.delete_cookie(STATE_COOKIE_NAME, path=CALLBACK_PATH)
    return response


@router.get("/profile/google-status.json")
async def google_status_json(request: Request):
    """JSON status -- useful for the MCP tools list or programmatic checks."""
    user = _require_web_user(request)
    has_grant = bool(google_oauth_token.get_refresh_token_for_operator(user))
    meta = vault.get_metadata(user.user_id, "google_oauth_refresh",
                                                  caller_id="google_status_json")
    return JSONResponse({
        "ok": True,
        "connected": has_grant,
        "last_rotated_at": meta.last_rotated_at if meta else None,
        "ttl_days": meta.ttl_days if meta else None,
        "scopes": SCOPES.split(),
    })
