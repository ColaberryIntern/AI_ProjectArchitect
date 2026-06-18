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
from execution.products.ops import sync_coordinator


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
    # The viewed-project targeted walk (focus_project_id path) — stub too so
    # the bg thread never reaches BC when a project filter is present.
    monkeypatch.setattr(
        my_day_router.sync, "pull_todos_for_project",
        lambda *a, **kw: {"status": "ok", "project_id": a[1] if len(a) > 1 else None},
    )
    monkeypatch.setattr(
        my_day_router.scorer, "score_all_todos",
        lambda *a, **kw: None,
    )
    # Reset the SyncCoordinator so each test starts with no in-flight
    # slot from a prior test. Phase 2 retired the function-attribute
    # `_locks` dict in favor of the coordinator.
    sync_coordinator.reset_coordinator_for_tests()

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


def test_sync_with_project_walks_that_project_targeted(monkeypatch, stub_user):
    """Manual Sync while viewing one project must walk THAT project via the
    budget-exempt pull_todos_for_project, so the viewed project always
    matches BC — the 2026-06-18 'sync still doesn't match' fix. The full
    sweep alone defers it behind the round-robin SYNC_BUDGET_SECONDS cursor
    (CB System sees 50+ projects)."""
    import time as _t
    monkeypatch.setattr(my_day_router, "_require_user", lambda r: stub_user)
    monkeypatch.setattr(my_day_router.sync, "pull_todos_for_user",
                        lambda *a, **kw: {"status": "ok"})
    monkeypatch.setattr(my_day_router.scorer, "score_all_todos", lambda *a, **kw: None)
    sync_coordinator.reset_coordinator_for_tests()
    focused: list[int] = []
    monkeypatch.setattr(
        my_day_router.sync, "pull_todos_for_project",
        lambda email, project_id, *a, **kw: focused.append(project_id) or {"status": "ok"},
    )
    app = FastAPI()
    app.include_router(my_day_router.router)
    client = TestClient(app)

    r = client.post("/my-day/sync",
                    data={"view": "briefing", "tier": "all", "project": "47126345"},
                    follow_redirects=False)
    assert r.status_code == 303
    # Daemon thread — wait for the targeted walk to register.
    for _ in range(100):
        if focused: break
        _t.sleep(0.01)
    assert focused == [47126345]


def test_sync_without_project_skips_targeted_walk(monkeypatch, stub_user):
    """No project filter → only the full sweep runs; the targeted walk must
    NOT fire (nothing to focus)."""
    import time as _t
    monkeypatch.setattr(my_day_router, "_require_user", lambda r: stub_user)
    full_done: list = []
    monkeypatch.setattr(my_day_router.sync, "pull_todos_for_user",
                        lambda *a, **kw: full_done.append(True) or {"status": "ok"})
    monkeypatch.setattr(my_day_router.scorer, "score_all_todos", lambda *a, **kw: None)
    sync_coordinator.reset_coordinator_for_tests()
    focused: list[int] = []
    monkeypatch.setattr(
        my_day_router.sync, "pull_todos_for_project",
        lambda email, project_id, *a, **kw: focused.append(project_id) or {"status": "ok"},
    )
    app = FastAPI()
    app.include_router(my_day_router.router)
    client = TestClient(app)

    r = client.post("/my-day/sync", data={"view": "briefing", "tier": "all"},
                    follow_redirects=False)
    assert r.status_code == 303
    for _ in range(100):
        if full_done: break
        _t.sleep(0.01)
    assert full_done == [True]
    assert focused == []
