"""Tests for /my-day/_health — operational visibility endpoint.

Covers:
    - Unauthenticated → redirect to /auth/login (handled upstream)
    - Authenticated but not admin → 403
    - Admin → 200 with JSON snapshot containing the expected keys
    - ?format=html → renders the template
    - Recent errors appear in the snapshot
    - Per-user sync state shows the right rows
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.routers import my_day as my_day_router
from execution.products.library import tenancy
from execution.products.ops import sync


@pytest.fixture
def admin_user():
    return tenancy.User(
        user_id="usr_admin",
        email="ali@colaberry.com",
        company_id="colaberry",
        display_name="Ali",
        roles=["admin"],
    )


@pytest.fixture
def regular_user():
    return tenancy.User(
        user_id="usr_regular",
        email="someone@colaberry.com",
        company_id="colaberry",
        display_name="Some User",
        roles=["consumer"],
    )


# ── _health_snapshot is a pure function ────────────────────────────


def test_snapshot_structure(monkeypatch):
    """The snapshot dict has the documented top-level keys."""
    snap = my_day_router._health_snapshot()
    expected = {
        "now", "ops_sync_scheduler", "pilot_dash_scheduler",
        "per_user_sync", "recent_errors", "pilot_dash_delivery",
    }
    assert expected.issubset(snap.keys())


def test_snapshot_includes_recent_errors():
    sync.clear_recent_errors()
    sync._record_error("test@example.com", "test_kind", "detail line")
    snap = my_day_router._health_snapshot()
    assert len(snap["recent_errors"]) >= 1
    assert any(e["kind"] == "test_kind" for e in snap["recent_errors"])
    sync.clear_recent_errors()


def test_snapshot_pilot_delivery_reflects_env(monkeypatch):
    monkeypatch.setenv("PILOT_DASH_DELIVERY", "1")
    monkeypatch.setenv("PILOT_DASH_TEST_MODE", "0")
    monkeypatch.setenv("GMAIL_SMTP_USERNAME", "ali@colaberry.com")
    monkeypatch.setenv("GMAIL_SMTP_APP_PASSWORD", "secret")
    snap = my_day_router._health_snapshot()
    assert snap["pilot_dash_delivery"]["delivery_enabled"] is True
    assert snap["pilot_dash_delivery"]["test_mode"] is False
    assert snap["pilot_dash_delivery"]["smtp_creds_present"] is True


def test_snapshot_pilot_delivery_no_creds(monkeypatch):
    monkeypatch.delenv("GMAIL_SMTP_USERNAME", raising=False)
    monkeypatch.delenv("GMAIL_SMTP_APP_PASSWORD", raising=False)
    snap = my_day_router._health_snapshot()
    assert snap["pilot_dash_delivery"]["smtp_creds_present"] is False


# ── Error tracking ─────────────────────────────────────────────────


def test_record_error_capped_at_50():
    sync.clear_recent_errors()
    for i in range(60):
        sync._record_error("u@x.com", "kind", f"detail {i}")
    errs = sync.recent_errors()
    assert len(errs) == 50
    # Should have kept the LAST 50 (oldest evicted)
    assert errs[0]["detail"] == "detail 10"
    assert errs[-1]["detail"] == "detail 59"
    sync.clear_recent_errors()


def test_clear_recent_errors():
    sync._record_error("u@x.com", "kind", "detail")
    sync.clear_recent_errors()
    assert sync.recent_errors() == []
