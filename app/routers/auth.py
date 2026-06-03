"""[Auth 2] HTTP routes for Google SSO + session.

Routes:
    GET  /auth/login        — kick off Google OAuth
    GET  /auth/callback     — Google redirects here with ?code= + ?state=
    POST /auth/logout       — clear the session cookie
    GET  /auth/whoami       — JSON snapshot of the current session
    GET  /auth/status       — JSON, whether SSO is enabled/disabled + why

When SSO is not configured (env vars missing), /auth/login returns a
helpful 503 explaining what's needed. This keeps the existing
anonymous-only library working until Ali registers the OAuth app.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from execution.products.library import auth_google, tenancy


router = APIRouter(prefix="/auth")


@router.get("/status")
async def auth_status():
    return {
        "enabled": auth_google.is_enabled(),
        "reason": auth_google.disabled_reason(),
        "anonymous_paths": auth_google._load_tenant_domains().get("anonymous_paths", []),
        "login_required_paths": auth_google._load_tenant_domains().get("login_required_paths", []),
    }


@router.get("/login")
async def auth_login(request: Request, next: str = "/library/"):
    if not auth_google.is_enabled():
        return JSONResponse(
            status_code=503,
            content={
                "error": "Google SSO not configured on this server.",
                "reason": auth_google.disabled_reason(),
                "fix": "Set GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET, "
                          "GOOGLE_OAUTH_REDIRECT_URI, and LIBRARY_SESSION_SECRET in .env.prod, "
                          "then restart the container.",
            },
        )
    # CSRF: random state, stored in a short-lived cookie + included in URL
    state = secrets.token_urlsafe(24)
    # `next` is a returnTo URL; we encode it into the state for simplicity
    # (in real prod, use a signed payload — for v1 the state is opaque
    # and the next URL is kept in a separate cookie)
    url = auth_google.build_login_url(state=state)
    response = RedirectResponse(url=url, status_code=302)
    response.set_cookie("oauth_state", state, httponly=True, max_age=600,
                              samesite="lax", secure=True)
    response.set_cookie("oauth_next", next, httponly=True, max_age=600,
                              samesite="lax", secure=True)
    return response


@router.get("/callback")
async def auth_callback(request: Request, code: str = "", state: str = ""):
    if not auth_google.is_enabled():
        raise HTTPException(503, "Auth not configured")
    expected_state = request.cookies.get("oauth_state", "")
    if not state or state != expected_state:
        raise HTTPException(400, "state mismatch")
    next_url = request.cookies.get("oauth_next", "/library/")
    try:
        userinfo = auth_google.exchange_code_for_userinfo(code)
    except Exception as e:
        raise HTTPException(502, f"oauth callback failed: {e}")

    user, status = auth_google.provision_or_lookup_user(userinfo)
    if user is None:
        if status == "queued_for_review":
            return JSONResponse(
                status_code=403,
                content={
                    "error": "Your email domain isn't mapped to a Colaberry tenant.",
                    "email": userinfo.get("email"),
                    "status": status,
                    "next_step": "An admin will review your access request.",
                },
            )
        raise HTTPException(403, f"login rejected: {status}")

    token = auth_google.issue_session_token(user)
    response = RedirectResponse(url=next_url, status_code=302)
    response.set_cookie(
        auth_google.SESSION_COOKIE_NAME, token,
        httponly=True, max_age=86400, samesite="lax", secure=True,
    )
    response.delete_cookie("oauth_state")
    response.delete_cookie("oauth_next")
    return response


@router.post("/logout")
async def auth_logout(response: Response):
    response = JSONResponse({"ok": True, "logged_out": True})
    response.delete_cookie(auth_google.SESSION_COOKIE_NAME)
    return response


@router.get("/whoami")
async def auth_whoami(request: Request):
    cookie = request.cookies.get(auth_google.SESSION_COOKIE_NAME)
    user = auth_google.current_user_from_cookie(cookie)
    if not user:
        return {"authenticated": False}
    company = tenancy.get_company(user.company_id)
    return {
        "authenticated": True,
        "user_id": user.user_id,
        "email": user.email,
        "display_name": user.display_name,
        "company_id": user.company_id,
        "company_name": company.display_name if company else user.company_id,
        "roles": user.roles,
        "last_login_at": user.last_login_at,
    }
