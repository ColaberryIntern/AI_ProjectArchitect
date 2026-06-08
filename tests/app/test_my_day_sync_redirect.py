"""Tests for POST /my-day/sync — filter-preserving redirect.

The Sync button used to hard-redirect to bare /my-day/, silently dropping
every URL filter (view, tier, project, list, person) the user had active.
That felt like a state reset.

These tests verify that the handler now rebuilds the redirect URL from
the form fields (set by hidden inputs in the Sync forms) and falls back
to the Referer query string when the form is empty.

Strategy: mount ONLY the my-day router on a fresh FastAPI app so we don't
boot the full app + scheduler + middleware. Stub _require_user and the
background sync so the request is fast and deterministic.
"""
from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import my_day as my_day_router


@pytest.fixture
def stub_user():
    return SimpleNamespace(
        user_id="usr_test",
        email="someone@colaberry.com",
        display_name="Tester",
    )


@pytest.fixture
def client(monkeypatch, stub_user):
    # Skip real auth — the redirect logic is what we're testing.
    monkeypatch.setattr(my_day_router, "_require_user", lambda r: stub_user)
    # Skip the real background sync — we don't want BC calls in tests.
    monkeypatch.setattr(
        my_day_router.sync, "pull_todos_for_user",
        lambda *a, **kw: {"status": "ok", "todos": 0, "projects": 0},
    )
    monkeypatch.setattr(
        my_day_router.scorer, "score_all_todos",
        lambda *a, **kw: None,
    )
    # Reset the per-user sync lock so each test starts clean.
    if getattr(my_day_router._maybe_async_sync, "_locks", None) is not None:
        my_day_router._maybe_async_sync._locks.clear()

    app = FastAPI()
    app.include_router(my_day_router.router)
    return TestClient(app)


def _redirect_params(response) -> dict:
    """Parse the redirect Location into a {key: value} dict (first value wins)."""
    assert response.status_code == 303, f"expected 303 redirect, got {response.status_code}"
    loc = response.headers["location"]
    parsed = urlparse(loc)
    assert parsed.path == "/my-day/", f"unexpected redirect path: {parsed.path}"
    qs = parse_qs(parsed.query)
    return {k: v[0] for k, v in qs.items()}


def test_sync_preserves_all_filter_fields(client):
    """Form fields → redirect query string, plus sync_started=1."""
    r = client.post(
        "/my-day/sync",
        data={
            "view": "kanban",
            "tier": "assigned",
            "project": "47126345",
            "list": "9953889092",
            "person": "Ali",
        },
        follow_redirects=False,
    )
    params = _redirect_params(r)
    assert params["view"] == "kanban"
    assert params["tier"] == "assigned"
    assert params["project"] == "47126345"
    assert params["list"] == "9953889092"
    assert params["person"] == "Ali"
    assert params["sync_started"] == "1"


def test_sync_preserves_partial_filters(client):
    """Only some filters set — the redirect carries exactly those, no empties."""
    r = client.post(
        "/my-day/sync",
        data={"view": "briefing", "tier": "due", "project": "47126345"},
        follow_redirects=False,
    )
    params = _redirect_params(r)
    assert params == {
        "view": "briefing",
        "tier": "due",
        "project": "47126345",
        "sync_started": "1",
    }


def test_sync_empty_form_falls_back_to_referer(client):
    """No form fields → handler parses the Referer query and uses those."""
    r = client.post(
        "/my-day/sync",
        data={},
        headers={"Referer": "http://localhost/my-day/?view=heatmap&tier=human&project=99"},
        follow_redirects=False,
    )
    params = _redirect_params(r)
    assert params["view"] == "heatmap"
    assert params["tier"] == "human"
    assert params["project"] == "99"
    assert params["sync_started"] == "1"


def test_sync_no_form_no_referer_redirects_to_bare(client):
    """No filters anywhere → bare /my-day/?sync_started=1 (matches old behavior)."""
    r = client.post("/my-day/sync", data={}, follow_redirects=False)
    params = _redirect_params(r)
    assert params == {"sync_started": "1"}


def test_sync_form_wins_over_referer(client):
    """When the form provides filters, Referer is ignored (form is authoritative)."""
    r = client.post(
        "/my-day/sync",
        data={"view": "briefing", "tier": "assigned"},
        headers={"Referer": "http://localhost/my-day/?view=kanban&tier=watching"},
        follow_redirects=False,
    )
    params = _redirect_params(r)
    assert params["view"] == "briefing"
    assert params["tier"] == "assigned"
