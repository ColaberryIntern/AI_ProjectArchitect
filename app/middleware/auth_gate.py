"""[Auth 2] HTTP middleware that gates login_required_paths.

Decision tree (in order):
    1. SSO disabled (env vars not set) -> pass through.
       Preserves anonymous browsing in dev and on prod boxes where Ali
       has not yet registered the OAuth app and populated .env.prod.
    2. Path matches login_required_paths -> check session.
       Missing/invalid -> 302 redirect to /auth/login?next=<original>.
       Valid -> attach user to request.state.user and pass through.
    3. Otherwise pass through.

Login-required is checked BEFORE anonymous because the current
config has "/" in anonymous_paths (matches every path via startswith),
and the intent is that login-required paths win when both match.

When a valid session is found, request.state.user is set so downstream
routers can read the resolved User without re-parsing the cookie.

Configured via config/library_tenant_domains.json. See auth_google for
the path-matching helpers.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import Request
from fastapi.responses import RedirectResponse

from execution.products.library import auth_google


async def auth_gate_middleware(request: Request, call_next):
    path = request.url.path

    if not auth_google.is_enabled():
        return await call_next(request)

    if auth_google.path_requires_login(path):
        cookie = request.cookies.get(auth_google.SESSION_COOKIE_NAME)
        user = auth_google.current_user_from_cookie(cookie)
        if user is None:
            # Redirect browser navigations to login; let API/JSON requests fall
            # through so the route returns its own 401 (or validates a secret).
            # Keeps secret-authed endpoints like /admin/cb-mentions.json working.
            accept = request.headers.get("accept", "")
            wants_html = "text/html" in accept and not path.endswith(".json")
            if not wants_html:
                return await call_next(request)
            qs = request.url.query
            full = path + ("?" + qs if qs else "")
            return RedirectResponse(
                url=f"/auth/login?next={quote(full, safe='')}",
                status_code=302,
            )
        request.state.user = user

    return await call_next(request)
