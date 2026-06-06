"""Onboarding gate middleware.

Once a Google-SSO'd user is identified, if any of the three setup steps
(MCP installed + Google grant + Basecamp grant) is incomplete, redirect
to /profile/welcome. The welcome page, the three setup pages, the JSON
status endpoints, auth, static assets, and the MCP RPC endpoint are
skipped.

Truth for "is setup complete" lives in app/routers/welcome.py
(needs_setup) so the gate and the page always agree.
"""
from __future__ import annotations

from fastapi.responses import RedirectResponse


SKIP_PREFIXES = (
    "/profile/welcome",
    "/profile/mcp-setup", "/profile/mcp-token", "/profile/mcp-revoke",
    "/profile/mcp-status.json",
    "/profile/connect-google", "/profile/google-status.json",
    "/profile/connect-basecamp", "/profile/basecamp-status.json",
    "/auth/", "/static/", "/advisory/static/",
    "/mcp", "/api/", "/favicon",
    "/openapi.json", "/docs", "/redoc",
)


async def onboarding_gate_middleware(request, call_next):
    # Late imports so the test client can monkeypatch these symbols.
    from app.routers import welcome as _welcome
    from execution.products.library import auth_google as _ag

    path = request.url.path
    if any(path.startswith(p) for p in SKIP_PREFIXES):
        return await call_next(request)
    cookie = request.cookies.get(_ag.SESSION_COOKIE_NAME)
    user = _ag.current_user_from_cookie(cookie) if cookie else None
    if user is not None and _welcome.needs_setup(user):
        accept = (request.headers.get("accept") or "").lower()
        if "text/html" in accept or accept == "" or "*/*" in accept:
            return RedirectResponse("/profile/welcome", status_code=303)
    return await call_next(request)
