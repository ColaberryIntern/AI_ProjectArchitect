"""Tests for the inbound BC webhook HTTP endpoint.

Auth model: shared-secret URL segment. The endpoint MUST:
  - 503 when OPS_CB_WEBHOOK_SECRET is unset (operator disabled webhooks
    but BC may still be POSTing)
  - 401 when the URL segment doesn't match
  - 200 with a per-event summary when the secret matches, regardless of
    whether handle_event found anything to act on (BC retries on 5xx;
    we don't want spurious retries when the comment simply didn't match)
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("OPS_CB_WEBHOOK_SECRET", raising=False)
    yield


def test_endpoint_503_when_secret_unset(client):
    r = client.post("/webhooks/basecamp/anything", json={})
    assert r.status_code == 503
    assert "OPS_CB_WEBHOOK_SECRET" in r.json()["detail"]


def test_endpoint_401_when_secret_mismatch(client, monkeypatch):
    monkeypatch.setenv("OPS_CB_WEBHOOK_SECRET", "real-secret")
    r = client.post("/webhooks/basecamp/wrong-secret", json={})
    assert r.status_code == 401


def test_endpoint_200_when_secret_matches(client, monkeypatch):
    monkeypatch.setenv("OPS_CB_WEBHOOK_SECRET", "real-secret")
    # handle_event will return its skipped-non_comment summary; we just
    # want a 200 response with ok=True and a summary present.
    r = client.post("/webhooks/basecamp/real-secret",
                    json={"recording": {"type": "Todo"}})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "summary" in body


def test_endpoint_acks_malformed_json_without_retry(client, monkeypatch):
    """BC retries 5xx — if BC sends garbage we still return 200 so they
    don't keep retrying."""
    monkeypatch.setenv("OPS_CB_WEBHOOK_SECRET", "real-secret")
    r = client.post("/webhooks/basecamp/real-secret",
                    data="not-json",
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert r.json()["skipped"] == "malformed_json"
