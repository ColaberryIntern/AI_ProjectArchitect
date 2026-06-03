"""[Auth 2] Tests for the auth gate middleware.

Verifies the four decision branches:
    1. SSO disabled -> middleware is a no-op (preserves dev / unregistered prod)
    2. Anonymous path + SSO enabled -> passes through
    3. Login-required path + no valid session -> 302 to /auth/login
    4. Login-required path + valid session -> passes through, user on state

The tests build an isolated FastAPI app rather than importing app.main so
they exercise ONLY the middleware (faster, no startup-side-effects).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.middleware import auth_gate_middleware
from execution.products.library import auth_google, tenancy


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def gated_app():
    """Minimal FastAPI app with the middleware + a few representative routes."""
    app = FastAPI()
    app.middleware("http")(auth_gate_middleware)

    @app.get("/")
    def root():
        return {"path": "/"}

    @app.get("/library/")
    def library_home():
        return {"path": "/library/"}

    @app.get("/library/skills/")
    def library_skills():
        return {"path": "/library/skills/"}

    @app.get("/auth/login")
    def auth_login_page():
        return {"path": "/auth/login"}

    @app.get("/library/whoami")
    def library_whoami(request: Request):
        u = getattr(request.state, "user", None)
        return {"user_email": u.email if u else None}

    return app


@pytest.fixture
def enabled_sso(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("GOOGLE_OAUTH_REDIRECT_URI", "http://localhost/auth/callback")
    monkeypatch.setenv("LIBRARY_SESSION_SECRET", "test-session-secret-32-char-long-x")


@pytest.fixture
def disabled_sso(monkeypatch):
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_REDIRECT_URI", raising=False)
    monkeypatch.delenv("LIBRARY_SESSION_SECRET", raising=False)


@pytest.fixture
def fake_user():
    return tenancy.User(
        user_id="usr_test_alice",
        email="alice@colaberry.com",
        company_id="colaberry",
        display_name="Alice Test",
        roles=["consumer"],
    )


# ── Branch 1: SSO disabled is a no-op ────────────────────────────


def test_sso_disabled_lets_library_through_anonymously(gated_app, disabled_sso):
    client = TestClient(gated_app)
    response = client.get("/library/", follow_redirects=False)
    assert response.status_code == 200
    assert response.json() == {"path": "/library/"}


def test_sso_disabled_lets_root_through(gated_app, disabled_sso):
    client = TestClient(gated_app)
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 200


# ── Branch 2: anonymous paths pass through even when SSO is enabled ──


def test_sso_enabled_root_is_anonymous(gated_app, enabled_sso):
    client = TestClient(gated_app)
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 200


def test_sso_enabled_auth_login_is_anonymous(gated_app, enabled_sso):
    """/auth/* must stay anonymous or login is unreachable."""
    client = TestClient(gated_app)
    response = client.get("/auth/login", follow_redirects=False)
    assert response.status_code == 200


# ── Branch 3: login-required + no session -> redirect ────────────


def test_sso_enabled_library_no_cookie_redirects_to_login(gated_app, enabled_sso):
    client = TestClient(gated_app)
    response = client.get("/library/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].startswith("/auth/login?next=")
    # Original path is URL-encoded in the next= param
    assert "%2Flibrary%2F" in response.headers["location"]


def test_sso_enabled_library_query_string_preserved_in_next(gated_app, enabled_sso):
    client = TestClient(gated_app)
    response = client.get("/library/?view=skills&tier=ai", follow_redirects=False)
    assert response.status_code == 302
    loc = response.headers["location"]
    assert loc.startswith("/auth/login?next=")
    # The full path+query should be encoded in next=
    assert "view%3Dskills" in loc
    assert "tier%3Dai" in loc


def test_sso_enabled_library_invalid_cookie_redirects(gated_app, enabled_sso, monkeypatch):
    monkeypatch.setattr(
        auth_google, "current_user_from_cookie", lambda c: None
    )
    client = TestClient(gated_app)
    response = client.get(
        "/library/",
        cookies={auth_google.SESSION_COOKIE_NAME: "garbage-or-expired"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"].startswith("/auth/login?next=")


def test_sso_enabled_subpath_under_library_is_gated(gated_app, enabled_sso):
    """/library/skills/ should also redirect — gate is by prefix."""
    client = TestClient(gated_app)
    response = client.get("/library/skills/", follow_redirects=False)
    assert response.status_code == 302
    assert "%2Flibrary%2Fskills%2F" in response.headers["location"]


# ── Branch 4: login-required + valid session -> passes through ───


def test_sso_enabled_library_valid_cookie_passes_through(
    gated_app, enabled_sso, monkeypatch, fake_user
):
    monkeypatch.setattr(
        auth_google, "current_user_from_cookie",
        lambda c: fake_user if c == "valid-cookie" else None,
    )
    client = TestClient(gated_app)
    response = client.get(
        "/library/",
        cookies={auth_google.SESSION_COOKIE_NAME: "valid-cookie"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert response.json() == {"path": "/library/"}


def test_valid_session_attaches_user_to_request_state(
    gated_app, enabled_sso, monkeypatch, fake_user
):
    """Downstream routers should be able to read request.state.user."""
    monkeypatch.setattr(
        auth_google, "current_user_from_cookie",
        lambda c: fake_user if c == "valid-cookie" else None,
    )
    client = TestClient(gated_app)
    response = client.get(
        "/library/whoami",
        cookies={auth_google.SESSION_COOKIE_NAME: "valid-cookie"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert response.json() == {"user_email": "alice@colaberry.com"}
