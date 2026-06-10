"""Tests for the /admin/cb-webhooks.* endpoints.

`/admin/cb-webhooks.json` exposes subscription state and config status.
`POST /admin/cb-webhooks/subscribe` idempotently subscribes BC webhooks
for a user — the operator action that turns PR #10 from "shipped but
SSH-required" into "subscribable from the admin console".

This file also covers /admin/cb-mentions.json's polling-disabled surface
(test_cb_mentions_polling_disabled_*), since the auth-bypass fixture
above is reusable.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from execution.products.ops import cb_mention_worker, cb_webhooks


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(cb_webhooks, "SUBS_PATH", tmp_path / "webhook_subs.json")
    for k in ("OPS_CB_WEBHOOK_SECRET", "OPS_CB_WEBHOOK_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    # _require_admin reads `_current_user(request)`. In test env there's
    # no Google session cookie + no tenancy data, so it'd 401. Stub it
    # to return a synthetic super-admin so we can exercise the route.
    from app.routers import admin as admin_router

    class _FakeAdmin:
        user_id = "usr-test-admin"
        email = "ali@colaberry.com"
        roles = ["admin"]
        company_id = "colaberry"
        display_name = "Ali"

    monkeypatch.setattr(admin_router, "_current_user", lambda req: _FakeAdmin())
    yield


# ── status endpoint ──────────────────────────────────────────────


def test_status_reports_secret_unset(client):
    r = client.get("/admin/cb-webhooks.json")
    assert r.status_code == 200
    body = r.json()
    assert body["secret_configured"] is False
    assert body["payload_url"] is None
    assert body["subscription_counts"] == {}


def test_status_reports_secret_set_and_existing_subs(client, monkeypatch):
    monkeypatch.setenv("OPS_CB_WEBHOOK_SECRET", "s3cret")
    monkeypatch.setenv("OPS_CB_WEBHOOK_BASE_URL", "https://advisor.example.com")
    cb_webhooks._save_subs({
        "ali@colaberry.com": {"100": 1001, "200": 1002},
        "kes@colaberry.com": {"300": 1003},
    })
    r = client.get("/admin/cb-webhooks.json")
    assert r.status_code == 200
    body = r.json()
    assert body["secret_configured"] is True
    assert body["payload_url"] == \
        "https://advisor.example.com/webhooks/basecamp/s3cret"
    assert body["subscription_counts"] == {
        "ali@colaberry.com": 2, "kes@colaberry.com": 1,
    }


# ── subscribe endpoint ───────────────────────────────────────────


def test_subscribe_rejects_empty_email(client):
    r = client.post("/admin/cb-webhooks/subscribe", data={"user_email": "   "})
    assert r.status_code == 400


def test_subscribe_invokes_helper_and_returns_summary(client, monkeypatch):
    monkeypatch.setenv("OPS_CB_WEBHOOK_SECRET", "s3cret")

    captured: dict = {}
    def _stub(user_email, max_buckets=None):
        captured["user_email"] = user_email
        captured["max_buckets"] = max_buckets
        return {
            "status": "ok", "user_email": user_email,
            "buckets_added": 5, "buckets_existing": 2,
            "failed": 0, "errors": [],
        }
    monkeypatch.setattr(cb_webhooks, "subscribe_user_buckets", _stub)

    r = client.post("/admin/cb-webhooks/subscribe",
                    data={"user_email": "Ali@Colaberry.com"})
    assert r.status_code == 200
    body = r.json()
    assert body["buckets_added"] == 5
    assert body["buckets_existing"] == 2
    # Email normalized to lowercase
    assert captured["user_email"] == "ali@colaberry.com"


def test_subscribe_passes_max_buckets_when_provided(client, monkeypatch):
    captured: dict = {}
    def _stub(user_email, max_buckets=None):
        captured["max_buckets"] = max_buckets
        return {"status": "ok", "buckets_added": 0, "buckets_existing": 0,
                "failed": 0, "errors": []}
    monkeypatch.setattr(cb_webhooks, "subscribe_user_buckets", _stub)

    r = client.post("/admin/cb-webhooks/subscribe",
                    data={"user_email": "ali@colaberry.com",
                          "max_buckets": "10"})
    assert r.status_code == 200
    assert captured["max_buckets"] == 10


def test_subscribe_returns_helper_status_when_secret_missing(client, monkeypatch):
    """When OPS_CB_WEBHOOK_SECRET is unset, the helper returns
    {status: 'no_secret'} — the admin route surfaces that as 200 with
    the helper's body, NOT a 5xx (the operator needs to know it's a
    config gap, not a code bug)."""
    def _stub(user_email, max_buckets=None):
        return {"status": "no_secret", "buckets_added": 0,
                "buckets_existing": 0, "failed": 0, "errors": []}
    monkeypatch.setattr(cb_webhooks, "subscribe_user_buckets", _stub)

    r = client.post("/admin/cb-webhooks/subscribe",
                    data={"user_email": "ali@colaberry.com"})
    assert r.status_code == 200
    assert r.json()["status"] == "no_secret"


# ── /admin/cb-mentions.json: polling-disabled surface ───────────────


def test_cb_mentions_polling_disabled_reports_intentional_not_stale(
        client, tmp_path, monkeypatch):
    """When the heartbeat carries skipped+reason=polling_disabled, the
    admin response must NOT report stale=True (the operator chose this).
    """
    hb_path = tmp_path / "heartbeat.json"
    hb_path.write_text(json.dumps({
        "started_at": "2020-01-01T00:00:00Z",
        "finished_at": "2020-01-01T00:00:00Z",
        "skipped": True,
        "reason": "polling_disabled",
        "users_with_token": 0,
        "total_mentions_found": 0,
        "total_responded": 0,
        "total_failed": 0,
        "fatal_error": None,
        "per_user": [],
    }), encoding="utf-8")
    monkeypatch.setattr(cb_mention_worker, "HEARTBEAT_PATH", hb_path)

    r = client.get("/admin/cb-mentions.json")
    assert r.status_code == 200
    body = r.json()
    assert body["polling_disabled"] is True
    assert body["ok"] is True
    assert body["stale"] is False
    assert body["heartbeat"]["reason"] == "polling_disabled"


def test_cb_mentions_normal_heartbeat_still_unaffected(
        client, tmp_path, monkeypatch):
    """A normal (non-skipped) heartbeat should keep its existing behavior:
    polling_disabled=False; staleness computed from finished_at."""
    hb_path = tmp_path / "heartbeat.json"
    # finished_at far in the past => stale=True under normal rules
    hb_path.write_text(json.dumps({
        "started_at": "2020-01-01T00:00:00Z",
        "finished_at": "2020-01-01T00:00:00Z",
        "users_with_token": 1,
        "total_mentions_found": 0,
        "total_responded": 0,
        "total_failed": 0,
        "fatal_error": None,
        "per_user": [],
    }), encoding="utf-8")
    monkeypatch.setattr(cb_mention_worker, "HEARTBEAT_PATH", hb_path)

    r = client.get("/admin/cb-mentions.json")
    assert r.status_code == 200
    body = r.json()
    assert body["polling_disabled"] is False
    assert body["stale"] is True
    assert body["ok"] is False
