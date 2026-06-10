"""Tests for the inbound BC webhook HTTP endpoint.

Auth model: shared-secret URL segment + per-user token segment. The
endpoint MUST:
  - 503 when OPS_CB_WEBHOOK_SECRET is unset (operator disabled webhooks
    but BC may still be POSTing)
  - 401 when the URL secret doesn't match
  - 401 when the user_token doesn't resolve to any active user
  - 200 with a per-event summary when both segments match, regardless of
    whether handle_event found anything to act on (BC retries on 5xx;
    we don't want spurious retries when the comment simply didn't match)

The legacy single-segment route stays alive during the migration window
and routes events to OPS_CB_WEBHOOK_LEGACY_DEFAULT_USER (or
OPS_CB_WEBHOOK_DEFAULT_USER) so existing BC subscriptions don't drop
events while operators re-run subscribe to get the new URL.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from execution.products.ops import cb_webhooks


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("OPS_CB_WEBHOOK_SECRET", "OPS_CB_WEBHOOK_DEFAULT_USER",
              "OPS_CB_WEBHOOK_LEGACY_DEFAULT_USER"):
        monkeypatch.delenv(k, raising=False)
    yield


def _patch_users(monkeypatch, *emails):
    """Stub tenancy.list_users so resolve_user_email_from_token has
    something to walk."""
    monkeypatch.setattr(
        "execution.products.library.tenancy.list_users",
        lambda active_only=True: [SimpleNamespace(email=e) for e in emails],
    )


# ── new multi-tenant route ────────────────────────────────────────


def test_endpoint_503_when_secret_unset(client):
    r = client.post("/webhooks/basecamp/anything/sometoken", json={})
    assert r.status_code == 503
    assert "OPS_CB_WEBHOOK_SECRET" in r.json()["detail"]


def test_endpoint_401_when_secret_mismatch(client, monkeypatch):
    monkeypatch.setenv("OPS_CB_WEBHOOK_SECRET", "real-secret")
    _patch_users(monkeypatch, "ali@colaberry.com")
    r = client.post("/webhooks/basecamp/wrong-secret/sometoken", json={})
    assert r.status_code == 401


def test_endpoint_401_when_user_token_does_not_resolve(client, monkeypatch):
    monkeypatch.setenv("OPS_CB_WEBHOOK_SECRET", "real-secret")
    _patch_users(monkeypatch, "ali@colaberry.com")
    r = client.post("/webhooks/basecamp/real-secret/" + "0" * 24, json={})
    assert r.status_code == 401
    assert "user token" in r.json()["detail"]


def test_endpoint_200_routes_to_resolved_user(client, monkeypatch):
    """When the user_token resolves, handle_event runs with that user's
    email — NOT a hardcoded default."""
    monkeypatch.setenv("OPS_CB_WEBHOOK_SECRET", "real-secret")
    _patch_users(monkeypatch, "ali@colaberry.com", "kes@colaberry.com")

    captured: dict = {}

    def _stub(payload, *, user_email):
        captured["user_email"] = user_email
        return {"received_at": "now", "skipped": "non_comment"}
    monkeypatch.setattr(cb_webhooks, "handle_event", _stub)

    kes_token = cb_webhooks._user_token_for("kes@colaberry.com", "real-secret")
    r = client.post(
        f"/webhooks/basecamp/real-secret/{kes_token}",
        json={"recording": {"type": "Todo"}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "summary" in body
    assert captured["user_email"] == "kes@colaberry.com"


def test_endpoint_acks_malformed_json_without_retry(client, monkeypatch):
    """BC retries 5xx — if BC sends garbage we still return 200 so they
    don't keep retrying."""
    monkeypatch.setenv("OPS_CB_WEBHOOK_SECRET", "real-secret")
    _patch_users(monkeypatch, "ali@colaberry.com")
    ali_token = cb_webhooks._user_token_for("ali@colaberry.com", "real-secret")
    r = client.post(
        f"/webhooks/basecamp/real-secret/{ali_token}",
        data="not-json",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert r.json()["skipped"] == "malformed_json"


# ── legacy single-segment route ───────────────────────────────────


def test_legacy_route_503_when_secret_unset(client):
    r = client.post("/webhooks/basecamp/anything", json={})
    assert r.status_code == 503


def test_legacy_route_401_when_secret_mismatch(client, monkeypatch):
    monkeypatch.setenv("OPS_CB_WEBHOOK_SECRET", "real-secret")
    r = client.post("/webhooks/basecamp/wrong-secret", json={})
    assert r.status_code == 401


def test_legacy_route_routes_to_legacy_default_user(client, monkeypatch):
    """During the migration window the legacy route routes events to the
    operator-configured legacy default so existing subscriptions don't
    drop events while operators re-run subscribe."""
    monkeypatch.setenv("OPS_CB_WEBHOOK_SECRET", "real-secret")
    monkeypatch.setenv("OPS_CB_WEBHOOK_LEGACY_DEFAULT_USER", "legacy@colaberry.com")

    captured: dict = {}

    def _stub(payload, *, user_email):
        captured["user_email"] = user_email
        return {"skipped": "non_comment"}
    monkeypatch.setattr(cb_webhooks, "handle_event", _stub)

    r = client.post("/webhooks/basecamp/real-secret",
                    json={"recording": {"type": "Todo"}})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["legacy"] is True
    assert captured["user_email"] == "legacy@colaberry.com"


def test_legacy_route_falls_back_to_default_user_var(client, monkeypatch):
    """If LEGACY_DEFAULT_USER isn't set, fall back to the original
    OPS_CB_WEBHOOK_DEFAULT_USER var that PR #10 used."""
    monkeypatch.setenv("OPS_CB_WEBHOOK_SECRET", "real-secret")
    monkeypatch.setenv("OPS_CB_WEBHOOK_DEFAULT_USER", "ali@colaberry.com")

    captured: dict = {}

    def _stub(payload, *, user_email):
        captured["user_email"] = user_email
        return {"skipped": "non_comment"}
    monkeypatch.setattr(cb_webhooks, "handle_event", _stub)

    r = client.post("/webhooks/basecamp/real-secret",
                    json={"recording": {"type": "Todo"}})
    assert r.status_code == 200
    assert captured["user_email"] == "ali@colaberry.com"


def test_legacy_route_skips_when_no_default_user_configured(client, monkeypatch):
    """When neither legacy var is set we ack 200 with a skipped marker
    — better than 500ing while the operator decides how to migrate."""
    monkeypatch.setenv("OPS_CB_WEBHOOK_SECRET", "real-secret")
    r = client.post("/webhooks/basecamp/real-secret",
                    json={"recording": {"type": "Todo"}})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["skipped"] == "legacy_no_default_user"
    assert body["legacy"] is True
