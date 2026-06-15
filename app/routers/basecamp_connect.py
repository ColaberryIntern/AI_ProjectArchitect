"""In-app Basecamp 3 OAuth grant flow for the per-operator BC token used
by the MCP write tools (colaberry_create_ticket / _post_progress /
_close_ticket).

Model: each operator grants OAuth for their REAL Basecamp account (no
"X AI" persona, no aliasing). The MCP write tools post as them with a
"<p><em>via {Name}'s Claude Code</em></p>" body prefix so readers can
tell the comment came through Claude. BC's authorship header shows the
human's real name, which is honest because they DID author it via their
session.

Routes:
  GET  /profile/connect-basecamp     - landing + Connect button
  GET  /auth/basecamp-callback       - OAuth callback; exchanges + stores

REQUIRED Cloud setup (one-time, by admin):
  - Basecamp 3 Integration at https://integrate.37signals.com/ with
    redirect URI: https://advisor.colaberry.ai/auth/basecamp-callback
  - BASECAMP_OAUTH_CLIENT_ID + BASECAMP_OAUTH_CLIENT_SECRET in env
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

from app.routers import welcome as _welcome_helpers
from execution.products.library import (
    auth_google, basecamp_oauth_token, tenancy,
)

logger = logging.getLogger(__name__)

router = APIRouter()

AUTHORIZE_ENDPOINT = "https://launchpad.37signals.com/authorization/new"
TOKEN_ENDPOINT = "https://launchpad.37signals.com/authorization/token"
PROFILE_ENDPOINT = "https://launchpad.37signals.com/authorization.json"
CALLBACK_PATH = "/auth/basecamp-callback"

STATE_COOKIE_NAME = "colaberry_basecamp_state"
RETURN_COOKIE_NAME = "colaberry_basecamp_return"
STATE_TTL_SEC = 600


def is_ai_account_email(email: str) -> bool:
    """True when the BC account behind this email is the operator's
    'X AI' identity rather than their human account.

    Recognized patterns (case-insensitive):
      - <base>-ai@<domain>      e.g. karun-ai@colaberry.com
      - <base>+ai@<domain>      e.g. karun+ai@colaberry.com (Gmail aliasing)
      - <base>.ai@<domain>      e.g. karun.ai@colaberry.com (dot alias)

    Anything else is treated as the user's human account. We deliberately
    DON'T require the AI account to be on the colaberry.com domain --
    contractors / partners might use a different domain.
    """
    if not email:
        return False
    e = email.strip().lower()
    if "@" not in e:
        return False
    local, _, _ = e.partition("@")
    if not local:
        return False
    return (
        local.endswith("-ai")
        or local.endswith("+ai")
        or local.endswith(".ai")
        or local == "ai"
    )


def _friendly_ai_name(email: str, fallback_suffix: str = "") -> str:
    """Derive 'Karun AI' from 'karun-ai@colaberry.com' for display."""
    if not email or "@" not in email:
        return ""
    local = email.split("@", 1)[0].lower()
    base = local
    for suf in ("-ai", "+ai", ".ai"):
        if local.endswith(suf):
            base = local[: -len(suf)]
            break
    if not base:
        return "AI"
    name = base.replace(".", " ").replace("-", " ").replace("+", " ").title()
    if is_ai_account_email(email):
        return f"{name} AI"
    return f"{name}{fallback_suffix}"


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
    return "https://advisor.colaberry.ai" + CALLBACK_PATH


def _client_credentials() -> tuple[str, str]:
    cid = (os.environ.get("BASECAMP_OAUTH_CLIENT_ID") or "").strip()
    secret = (os.environ.get("BASECAMP_OAUTH_CLIENT_SECRET") or "").strip()
    return cid, secret


def _fetch_authorization_info(access_token: str) -> dict:
    """Call launchpad /authorization.json. Returns identity dict for display
    purposes (e.g. "Connected as Ali Muwwakkil")."""
    req = urllib.request.Request(
        PROFILE_ENDPOINT,
        method="GET",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "User-Agent": "Colaberry MCP per-user BC (ali@colaberry.com)",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


@router.get("/profile/connect-basecamp")
async def connect_basecamp_page(request: Request, status: str | None = None,
                                error: str | None = None):
    """Landing page: shows status + Connect button."""
    user = _require_web_user(request)

    cid, _ = _client_credentials()
    if not cid:
        return HTMLResponse(_wrap(
            "<h2>Basecamp OAuth not configured on this server</h2>"
            "<p><code>BASECAMP_OAUTH_CLIENT_ID</code> env var is missing. "
            "Ask an admin.</p>",
            user,
        ))

    state = secrets.token_urlsafe(24)
    auth_params = {
        "type": "web_server",
        "client_id": cid,
        "redirect_uri": _redirect_uri(),
        "state": state,
    }
    auth_url = AUTHORIZE_ENDPOINT + "?" + urllib.parse.urlencode(auth_params)

    meta = basecamp_oauth_token.get_grant_metadata(user)
    status_block = ""
    if status == "ok":
        status_block = (
            '<div style="background:#dafbe1;border:1px solid #aceebb;'
            'padding:12px 16px;border-radius:8px;margin-bottom:16px;color:#137333;">'
            "✅ Connected. MCP write tools will now post on your behalf."
            "</div>"
        )
    elif error:
        status_block = (
            '<div style="background:#ffeef0;border:1px solid #ffabba;'
            'padding:12px 16px;border-radius:8px;margin-bottom:16px;color:#b3261e;">'
            f"❌ Connect failed: <code>{error}</code>"
            "</div>"
        )

    if meta and not meta.get("legacy"):
        exp = meta.get("access_token_expires_at")
        exp_str = time.strftime("%Y-%m-%d %H:%M UTC",
                                time.gmtime(exp)) if exp else "unknown"
        bc_email = meta.get("bc_user_email") or ""
        # Context-aware check so hardcoded overrides (Ali -> CB System)
        # also light up the AI badge. Falls back to the suffix-only
        # check when no human user is in scope.
        from execution.products.library import basecamp_provisioning as _bp
        is_ai = _bp.is_ai_account_for_user(bc_email, user)
        if is_ai:
            # 2026-06-15 team decision (human accounts only): connecting as
            # the +ai persona is the WRONG state. One human grant drives both
            # My Day reads and MCP posting, and the persona isn't a member of
            # every project (it 404s on gov buckets). This used to be shown as
            # the good state, and its "switch to a separate identity" help
            # text trapped operators (Obi, Ram) into mis-connecting. Flip it
            # to a warning that steers them back to their human account.
            ai_badge = (
                '<span style="display:inline-block;background:#b3261e;'
                'color:#fff;padding:3px 9px;border-radius:4px;font-size:11px;'
                'font-weight:700;margin-left:8px;vertical-align:middle;">'
                "WRONG ACCOUNT</span>"
            )
            posting_as = (
                "⚠ You are connected as your <strong>AI persona</strong> "
                "account, not your own Basecamp login. That account is not a "
                "member of every project, so some of your tasks will not "
                "sync. Sign out of Basecamp, sign back in as your own "
                "colaberry.com account, then click Reconnect below."
            )
        else:
            ai_badge = ""
            posting_as = (
                "Posts from your Claude Code show under your own Basecamp "
                "name with a <code>via {Your Name}'s Claude Code</code> "
                "attribution. This is the correct setup — you are all set."
            )
        grant_state = (
            '<div style="background:#dafbe1;border:1px solid #aceebb;'
            'padding:12px 16px;border-radius:8px;margin-bottom:16px;color:#137333;">'
            "🟢 <strong>Connected.</strong> Authorized as "
            f"<code>{bc_email}</code>{ai_badge} "
            f"(BC user id <code>{meta.get('bc_user_id')}</code>). "
            f"Access token expires <code>{exp_str}</code> — refreshed automatically."
            f"<div style='margin-top:8px;font-size:13px;'>{posting_as}</div>"
            "</div>"
        )
        button_label = "🔄 Reconnect (replace existing grant)"
    elif meta and meta.get("legacy"):
        grant_state = (
            '<div style="background:#fff4d5;border:1px solid #f0d678;'
            'padding:12px 16px;border-radius:8px;margin-bottom:16px;color:#8a5a00;">'
            "🟡 <strong>Legacy paste-only token detected.</strong> No refresh "
            "token on file — it'll stop working after Basecamp's 14-day TTL. "
            "Click Connect to upgrade to the auto-refreshing OAuth grant."
            "</div>"
        )
        button_label = "🔗 Upgrade to OAuth grant"
    else:
        grant_state = (
            '<div style="background:#fff4d5;border:1px solid #f0d678;'
            'padding:12px 16px;border-radius:8px;margin-bottom:16px;color:#8a5a00;">'
            "🟡 <strong>Not connected yet.</strong>"
            "</div>"
        )
        button_label = "🔗 Connect Basecamp"

    body = (
        "<h1 style='font-size:22px;margin:0 0 12px;'>Connect Basecamp</h1>"
        "<p style='color:#57606a;font-size:13px;margin:0 0 22px;'>"
        "Grants the Colaberry MCP server permission to create todos, post "
        "comments, and close tickets in Basecamp <strong>on your behalf</strong>. "
        "Posts and todos will show your name in the authorship header with a "
        "<code>via {Your Name}'s Claude Code</code> attribution line in the body "
        "so readers can distinguish automated posts from manual ones."
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
        "<li>Create todos in projects you have access to in Basecamp 3.</li>"
        "<li>Post comments + close todos on your behalf.</li>"
        "<li>Read project + people lists (read-only) to resolve names.</li>"
        "</ul>"
        "<p>You can revoke anytime at "
        '<a href="https://launchpad.37signals.com/authorizations" target="_blank">'
        "launchpad.37signals.com/authorizations</a>.</p>"
        "</details>"
    )

    response = HTMLResponse(_wrap(body, user))
    response.set_cookie(
        STATE_COOKIE_NAME, state,
        max_age=STATE_TTL_SEC,
        httponly=True, samesite="lax", secure=True,
        path=CALLBACK_PATH,
    )
    return_dest = (request.query_params.get("return") or "").strip()
    if return_dest == "welcome":
        response.set_cookie(
            RETURN_COOKIE_NAME, "welcome",
            max_age=STATE_TTL_SEC,
            httponly=True, samesite="lax", secure=True,
            path=CALLBACK_PATH,
        )
    return response


def _wrap(body_html: str, user) -> str:
    next_path = _welcome_helpers.next_step_path(user)
    next_label = _welcome_helpers.next_step_label(user)
    if next_path.startswith("/profile/connect-basecamp"):
        next_html = ""
    else:
        next_html = (
            f' · <a href="{next_path}" style="font-weight:600;">'
            f"Next: {next_label} →</a>"
        )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Connect Basecamp · Colaberry</title>"
        "<style>body{font-family:-apple-system,Segoe UI,sans-serif;"
        "max-width:680px;margin:60px auto;padding:0 24px;color:#1f2328;}"
        "code{background:#f6f8fa;padding:1px 5px;border-radius:3px;font-size:12px;}"
        "a{color:#0969da;}"
        "</style></head><body>"
        f"{body_html}"
        '<p style="margin-top:32px;font-size:12px;color:#57606a;">'
        f"Signed in as <code>{user.email}</code>. "
        '<a href="/profile/connect-google">Connect Google</a> · '
        '<a href="/my-day/">My Day</a> · '
        '<a href="/profile/mcp-setup">MCP setup</a>'
        f"{next_html}"
        "</p></body></html>"
    )


@router.get(CALLBACK_PATH)
async def basecamp_callback(request: Request,
                            code: str | None = None,
                            state: str | None = None,
                            error: str | None = None):
    """OAuth redirect target. Validates state, exchanges code, captures
    identity for display, stores grant."""
    user = _require_web_user(request)

    if error:
        return RedirectResponse(
            f"/profile/connect-basecamp?error={urllib.parse.quote(error)}",
            status_code=303,
        )
    if not code or not state:
        return RedirectResponse(
            "/profile/connect-basecamp?error=missing_code_or_state",
            status_code=303,
        )

    expected = request.cookies.get(STATE_COOKIE_NAME, "")
    if not expected or not secrets.compare_digest(state, expected):
        return RedirectResponse(
            "/profile/connect-basecamp?error=state_mismatch",
            status_code=303,
        )

    client_id, client_secret = _client_credentials()
    if not client_id or not client_secret:
        return RedirectResponse(
            "/profile/connect-basecamp?error=server_not_configured",
            status_code=303,
        )

    body = urllib.parse.urlencode({
        "type": "web_server",
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": _redirect_uri(),
        "code": code,
    }).encode("utf-8")
    req = urllib.request.Request(
        TOKEN_ENDPOINT,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": "Colaberry MCP per-user BC (ali@colaberry.com)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            payload = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        logger.warning("BC token exchange HTTP %s for user=%s", e.code, user.user_id)
        return RedirectResponse(
            f"/profile/connect-basecamp?error=token_exchange_http_{e.code}",
            status_code=303,
        )
    except urllib.error.URLError as e:
        logger.warning("BC token exchange network error for user=%s: %s",
                       user.user_id, type(e).__name__)
        return RedirectResponse(
            "/profile/connect-basecamp?error=token_exchange_network_error",
            status_code=303,
        )
    except json.JSONDecodeError:
        return RedirectResponse(
            "/profile/connect-basecamp?error=token_exchange_malformed_response",
            status_code=303,
        )

    access_token = payload.get("access_token") or ""
    refresh_token = payload.get("refresh_token") or ""
    expires_in = int(payload.get("expires_in", 1209600))
    expires_at = time.time() + expires_in

    if not access_token or not refresh_token:
        return RedirectResponse(
            "/profile/connect-basecamp?error=token_response_missing_tokens",
            status_code=303,
        )

    granted_email = ""
    granted_id = 0
    try:
        info = _fetch_authorization_info(access_token)
        identity = (info or {}).get("identity") or {}
        granted_email = (identity.get("email_address") or "").strip().lower()
        granted_id = int(identity.get("id") or 0)
    except Exception as e:
        logger.warning("BC authorization.json failed for user=%s: %s",
                       user.user_id, type(e).__name__)

    # ── Defense: refuse grant if BC handed us the user's AI persona token ──
    # Symptom this prevents: the per-user OAuth flow grants the token to
    # whoever is signed in to BC at click time. If the user happens to be
    # signed in as their +ai persona (the BC AI sub-account created by
    # basecamp_provisioning), the grant points at the wrong identity.
    # The AI persona is NOT a member of customer projects (ShipCES,
    # gov-bid-builds/*) so every sync request returns 404 and titles drift.
    # We saw this happen for Ali on 2026-06-10 (ShipCES Pricing Layer
    # ticket renamed in BC, never propagated to /my-day/).
    #
    # Detect the symptom: granted email is <local>+ai@<domain> where the
    # user's own email is <local>@<domain>. Refuse and tell the user to
    # sign out of BC's AI account and reconnect as their human account.
    user_email_lower = (user.email or "").strip().lower()
    is_plus_alias = False
    if "@" in user_email_lower and "@" in granted_email:
        u_local, u_domain = user_email_lower.split("@", 1)
        g_local, g_domain = granted_email.split("@", 1)
        if u_domain == g_domain and g_local in (f"{u_local}+ai", f"{u_local}-ai"):
            is_plus_alias = True
    if is_plus_alias:
        logger.warning(
            "basecamp_connect: refused +ai-persona grant for user=%s "
            "(granted=%s)", user.user_id, granted_email,
        )
        response = HTMLResponse("""
        <html><body style='font-family: -apple-system, Arial, sans-serif;
          max-width: 640px; margin: 60px auto; padding: 20px; line-height: 1.5;'>
          <h2 style='color: #cf222e;'>🛑 Connection refused: wrong Basecamp account</h2>
          <p>You authorized Basecamp as <code>""" + granted_email + """</code>,
          which is your <strong>AI persona</strong> account, not your human
          account.</p>
          <p>The AI persona account is not a member of customer projects
          (e.g. ShipCES, gov-bid builds), so syncing it would hide every
          customer ticket from your <code>/my-day/</code> view. We refuse the
          grant rather than silently break sync.</p>
          <h3>Fix it in 30 seconds</h3>
          <ol>
            <li>Open <a href='https://launchpad.37signals.com/signout' target='_blank'>https://launchpad.37signals.com/signout</a>
              and sign out of Basecamp completely.</li>
            <li>Open <a href='https://launchpad.37signals.com/signin' target='_blank'>https://launchpad.37signals.com/signin</a>
              and sign in as <code>""" + (user.email or "your-human-account@colaberry.com") + """</code>
              (NOT the +ai address).</li>
            <li>Come back to
              <a href='/profile/connect-basecamp'>/profile/connect-basecamp</a>
              and click Connect again.</li>
          </ol>
          <p><a href='/profile/connect-basecamp'>← Back to Connect Basecamp</a></p>
        </body></html>
        """, status_code=400)
        return response

    basecamp_oauth_token.store_oauth_grant(
        user,
        access_token=access_token,
        refresh_token=refresh_token,
        bc_user_id=granted_id,
        bc_user_email=granted_email,
        access_token_expires_at=expires_at,
        actor_id="basecamp_connect_web_flow",
    )

    if granted_id and not user.bc_user_id:
        user.bc_user_id = granted_id
        tenancy.upsert_user(user)

    return_dest = (request.cookies.get(RETURN_COOKIE_NAME) or "").strip()
    if return_dest == "welcome":
        success_url = "/profile/welcome?step=3"
    else:
        success_url = "/profile/connect-basecamp?status=ok"
    response = RedirectResponse(success_url, status_code=303)
    response.delete_cookie(STATE_COOKIE_NAME, path=CALLBACK_PATH)
    response.delete_cookie(RETURN_COOKIE_NAME, path=CALLBACK_PATH)
    return response


@router.get("/profile/basecamp-status.json")
async def basecamp_status_json(request: Request):
    user = _require_web_user(request)
    meta = basecamp_oauth_token.get_grant_metadata(user)
    return JSONResponse({
        "ok": True,
        "connected": bool(meta and not meta.get("legacy")),
        "legacy": bool(meta and meta.get("legacy")),
        "bc_user_email": meta.get("bc_user_email") if meta else None,
        "bc_user_id": meta.get("bc_user_id") if meta else None,
        "access_token_expires_at": meta.get("access_token_expires_at") if meta else None,
    })
