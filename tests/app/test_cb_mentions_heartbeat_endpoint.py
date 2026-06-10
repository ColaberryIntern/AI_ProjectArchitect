"""Tests for /admin/cb-mentions.json — heartbeat + webhook event summary.

The endpoint stitches polling health (heartbeat.json) with webhook event
counts (webhook_events.jsonl) so operators answer "is CB healthy?" from
one URL.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app
from execution.products.ops import cb_mention_worker, cb_webhooks


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(cb_mention_worker, "HEARTBEAT_PATH",
                        tmp_path / "heartbeat.json")
    monkeypatch.setattr(cb_webhooks, "EVENT_LOG_PATH",
                        tmp_path / "webhook_events.jsonl")
    from app.routers import admin as admin_router

    class _FakeAdmin:
        user_id = "usr-test-admin"
        email = "ali@colaberry.com"
        roles = ["admin"]
        company_id = "colaberry"
        display_name = "Ali"

    monkeypatch.setattr(admin_router, "_current_user", lambda req: _FakeAdmin())
    yield


def _write_fresh_heartbeat() -> None:
    cb_mention_worker.HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
    hb = {
        "finished_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "users_scanned": 1,
        "mentions_handled": 0,
        "fatal_error": None,
    }
    cb_mention_worker.HEARTBEAT_PATH.write_text(json.dumps(hb), encoding="utf-8")


def test_endpoint_includes_webhook_summary_block(client):
    _write_fresh_heartbeat()
    cb_webhooks.EVENT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with cb_webhooks.EVENT_LOG_PATH.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"received_at": ts, "responded": True}) + "\n")
        f.write(json.dumps({"received_at": ts, "skipped": "no_trigger"}) + "\n")

    r = client.get("/admin/cb-mentions.json")
    assert r.status_code == 200
    body = r.json()
    # Existing polling keys preserved
    assert "heartbeat" in body
    assert "fresh_window_minutes" in body
    assert "interval_minutes" in body
    # New webhook block has the expected shape
    wh = body["webhooks"]
    assert wh["window_minutes"] == body["fresh_window_minutes"]
    assert wh["events_total"] == 2
    assert wh["responded"] == 1
    assert wh["skipped_no_trigger"] == 1
    assert wh["last_event_at"] is not None


def test_endpoint_still_200s_when_summary_throws(client, monkeypatch):
    """A corrupt event log mustn't take down the polling-health view."""
    _write_fresh_heartbeat()

    def _boom(window_minutes=60):
        raise RuntimeError("simulated corrupt log")
    monkeypatch.setattr(cb_webhooks, "recent_event_summary", _boom)

    r = client.get("/admin/cb-mentions.json")
    assert r.status_code == 200
    body = r.json()
    assert body["webhooks"] == {"error": "RuntimeError"}
    # Polling block still intact
    assert "heartbeat" in body
    assert body["heartbeat"]["users_scanned"] == 1
