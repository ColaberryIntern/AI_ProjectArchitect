"""Guided welcome dashboard for new employees.

`/profile/welcome` shows three numbered step cards:

  1. Install Claude Code MCP   (token minted AND a device has phoned home)
  2. Connect Google             (Gmail + Drive OAuth grant on file)
  3. Connect Basecamp           (Basecamp OAuth grant on file, not legacy)

`Continue to My Day` is disabled until all three are complete.

The page READS the existing connect pages' state; it does NOT re-implement
OAuth or token minting. Each card's "Open" button links to the existing
setup page with `?return=welcome` so the user round-trips back here.

The completeness helpers (`has_mcp_installed`, `has_google_grant`,
`has_bc_grant`, `needs_setup`) are also imported by the auth middleware
in app/main.py so the page and the gate share the same truth source.
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from execution.products.library import (
    auth_google, basecamp_oauth_token, google_oauth_token, tenancy,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Completeness helpers (shared with the gate) ───────────────────────


def has_mcp_installed(user) -> bool:
    """True if the operator has minted a token AND at least one device has
    actually called the MCP server.

    Checks both storage models:
      - Multi-device (Phase 8.3+): mcp_tokens list. At least one entry
        with last_used_at and not revoked.
      - Legacy single-token (pre-8.3): mcp_token_issued_at +
        mcp_token_last_used_at on the User itself.

    Critical: the previous version bailed early when mcp_token_issued_at
    was None, but modern users only have entries in mcp_tokens (the
    legacy field is never populated). That made Kes's page show 'Pending'
    even though his Claude Code had successfully phoned home 3 minutes
    after install. Now we check mcp_tokens FIRST.
    """
    # New multi-device model: any active token that has phoned home.
    tokens = getattr(user, "mcp_tokens", None) or []
    for entry in tokens:
        if entry.get("last_used_at") and not entry.get("revoked_at"):
            return True
    # Legacy single-token model: both fields must be set.
    if (getattr(user, "mcp_token_issued_at", None)
                  and getattr(user, "mcp_token_last_used_at", None)):
        return True
    return False


def has_google_grant(user) -> bool:
    """True if the operator has a Gmail+Drive refresh token in the vault."""
    try:
        return bool(google_oauth_token.get_refresh_token_for_operator(user))
    except Exception:
        return False


def has_bc_grant(user) -> bool:
    """True if the operator has a non-legacy Basecamp OAuth grant in the
    vault. Legacy paste-only tokens count as `not connected` so the user
    is prompted to upgrade to the auto-refreshing OAuth grant.
    """
    try:
        meta = basecamp_oauth_token.get_grant_metadata(user)
    except Exception:
        return False
    if not meta:
        return False
    if meta.get("legacy"):
        return False
    return True


def needs_setup(user) -> bool:
    """Composite gate used by the auth middleware."""
    return not (
        has_mcp_installed(user)
        and has_google_grant(user)
        and has_bc_grant(user)
    )


def next_step_path(user) -> str:
    """Return the URL path of the next undone setup step, or
    `/my-day/?welcome=1` if all are done. Used by the existing connect-
    page footers to render a deterministic "Next: ..." link.
    """
    if not has_mcp_installed(user):
        return "/profile/mcp-setup?return=welcome"
    if not has_google_grant(user):
        return "/profile/connect-google?return=welcome"
    if not has_bc_grant(user):
        return "/profile/connect-basecamp?return=welcome"
    return "/my-day/?welcome=1"


def next_step_label(user) -> str:
    if not has_mcp_installed(user):
        return "Install MCP"
    if not has_google_grant(user):
        return "Connect Google"
    if not has_bc_grant(user):
        return "Connect Basecamp"
    return "Go to My Day"


# ── Auth helper (matches google_connect / basecamp_connect) ────────────


def _session_user(request: Request):
    cookie = request.cookies.get(auth_google.SESSION_COOKIE_NAME)
    user = auth_google.current_user_from_cookie(cookie)
    if user:
        return user
    if not auth_google.is_enabled():
        return tenancy.get_user("ali@colaberry.com")
    return None


def _require_web_user(request: Request):
    user = _session_user(request)
    if not user:
        from urllib.parse import quote
        full = request.url.path + (("?" + request.url.query) if request.url.query else "")
        raise HTTPException(303, headers={"Location": f"/auth/login?next={quote(full, safe='')}"})
    return user


# ── Route ──────────────────────────────────────────────────────────────


def _video_block() -> str:
    """Render an <iframe>/<video> at the top of the welcome page if
    ONBOARDING_VIDEO_URL is set. Empty string when unset (so the page
    doesn't show a "coming soon" placeholder)."""
    url = (os.environ.get("ONBOARDING_VIDEO_URL") or "").strip()
    if not url:
        return ""
    if "youtube.com" in url or "youtu.be" in url or "vimeo.com" in url:
        return (
            '<div style="aspect-ratio:16/9;width:100%;margin-bottom:24px;'
            'border-radius:8px;overflow:hidden;background:#000;">'
            f'<iframe src="{url}" width="100%" height="100%" frameborder="0" '
            'allow="autoplay; encrypted-media; picture-in-picture" '
            'allowfullscreen></iframe></div>'
        )
    return (
        '<video controls style="width:100%;border-radius:8px;'
        'margin-bottom:24px;background:#000;">'
        f'<source src="{url}"></video>'
    )


def _step_card(*, n: int, done: bool, title: str, why: str,
               open_path: str, open_label: str) -> str:
    if done:
        chip = ('<span style="background:#dafbe1;color:#137333;border:1px solid #aceebb;'
                'padding:3px 10px;border-radius:999px;font-size:12px;font-weight:600;">'
                "✓ Complete</span>")
        button = (
            f'<a href="{open_path}" '
            'style="display:inline-block;background:#fff;color:#1f2328;'
            'border:1px solid #d0d7de;padding:8px 18px;border-radius:6px;'
            'font-weight:500;text-decoration:none;font-size:13px;">'
            "Revisit</a>"
        )
        border = "#aceebb"
    else:
        chip = ('<span style="background:#fff4d5;color:#8a5a00;border:1px solid #f0d678;'
                'padding:3px 10px;border-radius:999px;font-size:12px;font-weight:600;">'
                "Pending</span>")
        button = (
            f'<a href="{open_path}" '
            'style="display:inline-block;background:#1a1a1a;color:#fff;'
            'padding:10px 22px;border-radius:6px;font-weight:600;'
            'text-decoration:none;">'
            f"{open_label}</a>"
        )
        border = "#d0d7de"
    return (
        f'<div id="step-{n}" style="border:1px solid {border};border-radius:10px;'
        'padding:20px 24px;margin-bottom:14px;background:#fff;">'
        '<div style="display:flex;align-items:center;justify-content:space-between;'
        'margin-bottom:8px;">'
        f'<div style="font-size:18px;font-weight:600;">Step {n}: {title}</div>'
        f"{chip}"
        "</div>"
        f'<div style="color:#57606a;font-size:13px;line-height:1.5;margin-bottom:14px;">'
        f"{why}</div>"
        f"{button}"
        "</div>"
    )


@router.get("/profile/welcome")
async def welcome_page(request: Request, step: int | None = None):
    user = _require_web_user(request)

    mcp_done = has_mcp_installed(user)
    google_done = has_google_grant(user)
    bc_done = has_bc_grant(user)
    all_done = mcp_done and google_done and bc_done

    video = _video_block()

    cards = (
        _step_card(
            n=1, done=mcp_done,
            title="Install Claude Code MCP",
            why=("Lets your Claude Code talk to Colaberry's private tools "
                 "(Basecamp, Gmail, project memory). Requires a quick "
                 "terminal command per device."),
            open_path="/profile/mcp-setup?return=welcome",
            open_label="Set up MCP",
        )
        + _step_card(
            n=2, done=google_done,
            title="Connect your Google account",
            why=("Lets Claude fetch attachments from your Gmail and stage "
                 "them in your Drive on your behalf. Per-operator -- "
                 "only your own mail is touched."),
            open_path="/profile/connect-google?return=welcome",
            open_label="Connect Google",
        )
        + _step_card(
            n=3, done=bc_done,
            title="Connect Basecamp",
            why=("Lets Claude create todos, post progress, and close "
                 "tickets in Basecamp on your behalf with a "
                 "<code>via {Your Name}'s Claude Code</code> attribution "
                 "in the body."),
            open_path="/profile/connect-basecamp?return=welcome",
            open_label="Connect Basecamp",
        )
    )

    if all_done:
        cta = (
            '<a href="/my-day/?welcome=1" '
            'style="display:inline-block;background:#137333;color:#fff;'
            'padding:14px 32px;border-radius:8px;font-weight:600;'
            'text-decoration:none;font-size:15px;margin-top:18px;">'
            "Continue to My Day →</a>"
        )
        cta_caption = ('<div style="color:#137333;font-size:13px;margin-top:10px;">'
                       "All set. Your Claude Code is ready to use Colaberry's tools."
                       "</div>")
    else:
        remaining = sum(1 for x in (mcp_done, google_done, bc_done) if not x)
        cta = (
            '<button disabled '
            'style="background:#eaeef2;color:#8c959f;'
            'padding:14px 32px;border-radius:8px;font-weight:600;'
            'border:none;font-size:15px;margin-top:18px;cursor:not-allowed;">'
            "Continue to My Day →</button>"
        )
        cta_caption = (
            '<div style="color:#57606a;font-size:13px;margin-top:10px;">'
            f"{remaining} step{'s' if remaining != 1 else ''} remaining."
            "</div>"
        )

    scroll_js = ""
    if step:
        scroll_js = (
            "<script>"
            "window.addEventListener('load', function() {"
            f"  var el = document.getElementById('step-{int(step)}');"
            "  if (el) el.scrollIntoView({behavior:'smooth', block:'center'});"
            "});"
            "</script>"
        )

    name = (getattr(user, "display_name", "") or user.email.split("@")[0])

    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Welcome · Colaberry</title>"
        "<style>"
        "body{font-family:-apple-system,Segoe UI,sans-serif;"
        "max-width:760px;margin:48px auto;padding:0 24px;color:#1f2328;"
        "background:#f6f8fa;}"
        "code{background:#eaeef2;padding:1px 5px;border-radius:3px;font-size:12px;}"
        "h1{font-size:26px;margin:0 0 6px;}"
        ".lede{color:#57606a;font-size:14px;margin:0 0 24px;}"
        "a:focus,button:focus{outline:2px solid #0969da;outline-offset:2px;}"
        "</style></head><body>"
        f"<h1>Welcome to Colaberry MCP, {name}.</h1>"
        '<p class="lede">Three quick setups to wire your Claude Code into '
        "Colaberry's tools. About 2 minutes total. You can do them in any "
        "order; we recommend top to bottom.</p>"
        f"{video}"
        f"{cards}"
        f"{cta}"
        f"{cta_caption}"
        '<p style="margin-top:40px;font-size:12px;color:#57606a;">'
        f"Signed in as <code>{user.email}</code>. "
        "Need help? Ping ali@colaberry.com."
        "</p>"
        f"{scroll_js}"
        "</body></html>"
    )
    return HTMLResponse(html)
