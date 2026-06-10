"""Tests for GET /my-day/sync-status.json — Phase 3 C7 / audit M3.

The polling endpoint that replaces the fixed 25s JS countdown. JS calls
this every ~2.5s while the ?sync_started=1 banner is up; reload fires
when in_flight transitions to False AND state has advanced.

Strategy: same as test_my_day_sync_redirect — mount only the router on
a fresh FastAPI app + stub _require_user and tenancy so the endpoint
serves deterministically without booting the full app.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import my_day as my_day_router
from execution.products.ops import store, sync_coordinator


@pytest.fixture
def stub_user():
    return SimpleNamespace(
        user_id="usr_test",
        email="poll@colaberry.com",
        display_name="Poller",
        roles=["consumer"],
    )


@pytest.fixture
def client(monkeypatch, stub_user, tmp_path):
    """Mini-app with my_day router mounted + isolated store + fresh
    coordinator so each test starts clean."""
    monkeypatch.setattr(my_day_router, "_require_user", lambda r: stub_user)
    monkeypatch.setattr(store, "OPS_ROOT", tmp_path / "ops")
    sync_coordinator.reset_coordinator_for_tests()
    app = FastAPI()
    app.include_router(my_day_router.router)
    return TestClient(app)


def test_returns_200_and_documented_shape(client):
    """Shape contract — JS depends on these keys existing."""
    r = client.get("/my-day/sync-status.json")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {
        "in_flight", "in_flight_age_seconds", "last_sync_at", "last_sync_status",
    }
    assert isinstance(body["in_flight"], bool)


def test_idle_state_returns_in_flight_false(client):
    """Brand-new user, no sync ever ran -> in_flight False, empty state."""
    r = client.get("/my-day/sync-status.json")
    body = r.json()
    assert body["in_flight"] is False
    assert body["in_flight_age_seconds"] is None
    assert body["last_sync_at"] == ""
    assert body["last_sync_status"] == ""


def test_in_flight_true_when_coordinator_holds_slot(client, stub_user):
    """When a sync is in flight, the endpoint must surface it so the JS
    keeps polling. Tests the integration with SyncCoordinator directly."""
    coord = sync_coordinator.get_coordinator()
    assert coord.try_start_sync(stub_user.email) is True
    try:
        body = client.get("/my-day/sync-status.json").json()
        assert body["in_flight"] is True
        assert body["in_flight_age_seconds"] is not None
        assert body["in_flight_age_seconds"] >= 0
    finally:
        coord.finish_sync(stub_user.email)


def test_returns_last_sync_metadata_from_store(client, stub_user):
    """When state.json has prior sync metadata, the endpoint surfaces it."""
    state = store.OpsState(
        user_id=stub_user.email,
        last_sync_at="2026-06-09T12:00:00+00:00",
        last_sync_status="partial",
    )
    store.save_state(state)
    body = client.get("/my-day/sync-status.json").json()
    assert body["last_sync_at"] == "2026-06-09T12:00:00+00:00"
    assert body["last_sync_status"] == "partial"


def test_cache_control_no_store(client):
    """Polled at 2.5s — must NOT be cached (by browser, by CDN, by nginx).
    Without no-store, a stale 200 could keep the banner alive past the
    real sync completion. Verify the header is set explicitly."""
    r = client.get("/my-day/sync-status.json")
    cc = r.headers.get("cache-control", "").lower()
    assert "no-store" in cc, f"missing no-store, got {cc!r}"


def test_status_returned_for_failed_sync(client, stub_user):
    """Sanity: failed status round-trips. JS uses this to know that the
    sync isn't going to come back successful, so it can stop polling
    optimistically."""
    state = store.OpsState(
        user_id=stub_user.email,
        last_sync_at="2026-06-09T13:00:00+00:00",
        last_sync_status="failed",
        last_sync_error="token_missing",
    )
    store.save_state(state)
    body = client.get("/my-day/sync-status.json").json()
    assert body["last_sync_status"] == "failed"
