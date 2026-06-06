"""Tests for the onboarding gate middleware.

Builds an isolated FastAPI app so we exercise ONLY the middleware (no
app startup side effects, no scheduler boot).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware import onboarding_gate_middleware
from app.routers import welcome


SKIP_PATHS = (
    "/profile/welcome",
    "/profile/mcp-setup",
    "/profile/connect-google",
    "/profile/connect-basecamp",
    "/profile/mcp-status.json",
    "/profile/google-status.json",
    "/profile/basecamp-status.json",
    "/auth/login",
    "/static/x.css",
    "/mcp/v1",
    "/api/whatever",
    "/favicon.ico",
)


@pytest.fixture
def gated_app():
    app = FastAPI()
    app.middleware("http")(onboarding_gate_middleware)

    @app.get("/")
    def root():
        return {"path": "/"}

    @app.get("/my-day/")
    def my_day():
        return {"path": "/my-day/"}

    @app.get("/library/")
    def library():
        return {"path": "/library/"}

    for p in SKIP_PATHS:
        async def skip(_p=p):
            return {"path": _p}
        app.add_api_route(p, skip, methods=["GET"])

    return app


@pytest.fixture
def client(gated_app):
    return TestClient(gated_app)


def _user_complete():
    return SimpleNamespace(
        user_id="usr-complete", email="ali@colaberry.com",
        display_name="Ali",
    )


def _user_incomplete():
    return SimpleNamespace(
        user_id="usr-incomplete", email="new@colaberry.com",
        display_name="New",
    )


# ── Anonymous traffic passes through ──────────────────────────────────


def test_anonymous_user_passes_through(client, monkeypatch):
    """No session cookie -> no user -> nothing to gate on. Gate is for
    authenticated users only; the auth_gate middleware handles anon."""
    from execution.products.library import auth_google
    monkeypatch.setattr(auth_google, "current_user_from_cookie",
                        MagicMock(return_value=None))
    r = client.get("/my-day/", follow_redirects=False)
    assert r.status_code == 200
    assert r.json() == {"path": "/my-day/"}


# ── Skip prefixes always pass ─────────────────────────────────────────


@pytest.mark.parametrize("path", SKIP_PATHS)
def test_skip_prefixes_pass_even_with_incomplete_user(client, monkeypatch, path):
    """Even when needs_setup returns True, the setup pages themselves
    and the JSON status endpoints must NOT be gated, or the user would
    be redirect-looped forever."""
    from execution.products.library import auth_google
    monkeypatch.setattr(auth_google, "current_user_from_cookie",
                        MagicMock(return_value=_user_incomplete()))
    monkeypatch.setattr(welcome, "needs_setup",
                        MagicMock(return_value=True))
    client.cookies.set("library_session", "fake-cookie")
    r = client.get(path, follow_redirects=False)
    assert r.status_code == 200, f"Path {path} should not have been gated"


# ── Complete user passes through ──────────────────────────────────────


def test_complete_user_passes_through_to_my_day(client, monkeypatch):
    from execution.products.library import auth_google
    monkeypatch.setattr(auth_google, "current_user_from_cookie",
                        MagicMock(return_value=_user_complete()))
    monkeypatch.setattr(welcome, "needs_setup", MagicMock(return_value=False))
    client.cookies.set("library_session", "fake-cookie")
    r = client.get("/my-day/", follow_redirects=False)
    assert r.status_code == 200
    assert r.json() == {"path": "/my-day/"}


# ── Incomplete user is redirected to /profile/welcome ─────────────────


def test_incomplete_user_redirected_to_welcome(client, monkeypatch):
    from execution.products.library import auth_google
    monkeypatch.setattr(auth_google, "current_user_from_cookie",
                        MagicMock(return_value=_user_incomplete()))
    monkeypatch.setattr(welcome, "needs_setup", MagicMock(return_value=True))
    client.cookies.set("library_session", "fake-cookie")
    r = client.get("/my-day/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/profile/welcome"


def test_incomplete_user_redirected_from_library(client, monkeypatch):
    """Gate applies to all non-skip paths, not just /my-day/."""
    from execution.products.library import auth_google
    monkeypatch.setattr(auth_google, "current_user_from_cookie",
                        MagicMock(return_value=_user_incomplete()))
    monkeypatch.setattr(welcome, "needs_setup", MagicMock(return_value=True))
    client.cookies.set("library_session", "fake-cookie")
    r = client.get("/library/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/profile/welcome"


def test_incomplete_user_redirected_from_root(client, monkeypatch):
    from execution.products.library import auth_google
    monkeypatch.setattr(auth_google, "current_user_from_cookie",
                        MagicMock(return_value=_user_incomplete()))
    monkeypatch.setattr(welcome, "needs_setup", MagicMock(return_value=True))
    client.cookies.set("library_session", "fake-cookie")
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303


# ── Accept header gating ──────────────────────────────────────────────


def test_json_request_from_incomplete_user_is_not_redirected(client, monkeypatch):
    """A fetch('/my-day/foo.json', {headers:{Accept:'application/json'}})
    from the browser should NOT be redirected (it'd 200 the HTML body of
    /profile/welcome into the JSON parser). Let it through and let the
    endpoint return whatever it returns."""
    from execution.products.library import auth_google
    monkeypatch.setattr(auth_google, "current_user_from_cookie",
                        MagicMock(return_value=_user_incomplete()))
    monkeypatch.setattr(welcome, "needs_setup", MagicMock(return_value=True))
    client.cookies.set("library_session", "fake-cookie")
    r = client.get("/my-day/", headers={"Accept": "application/json"},
                   follow_redirects=False)
    assert r.status_code == 200
    assert r.json() == {"path": "/my-day/"}
