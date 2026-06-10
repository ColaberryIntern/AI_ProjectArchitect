"""Unit tests for execution/products/ops/cb_smoke.py.

The smoke test exists to catch the failure class PR #2's heartbeat
*can't*: bugs we haven't yet thought of (BC API change, trigger regex
regression, OAuth refresh silently rotating). These tests cover the
correctness of the smoke check itself — the integration value comes from
the scheduled run hitting the real BC fixture.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest

from execution.products.ops import cb_smoke


@pytest.fixture(autouse=True)
def _isolate_state_path(tmp_path, monkeypatch):
    monkeypatch.setattr(cb_smoke, "STATE_PATH", tmp_path / "smoke_history.jsonl")
    # Clear all smoke env vars so each test starts unconfigured
    for k in (
        "OPS_CB_SMOKE_BUCKET_ID", "OPS_CB_SMOKE_TODO_ID",
        "OPS_CB_SMOKE_USER_EMAIL", "OPS_CB_SMOKE_TIMEOUT_MINUTES",
        "OPS_CB_SMOKE_ALERT_CHANNEL",
    ):
        monkeypatch.delenv(k, raising=False)
    yield


# ── is_configured gate ────────────────────────────────────────────


def test_is_configured_false_when_env_unset():
    assert cb_smoke.is_configured() is False


def test_is_configured_true_when_bucket_and_todo_set(monkeypatch):
    monkeypatch.setenv("OPS_CB_SMOKE_BUCKET_ID", "47502609")
    monkeypatch.setenv("OPS_CB_SMOKE_TODO_ID", "9946499598")
    assert cb_smoke.is_configured() is True


def test_is_configured_false_when_bucket_unparseable(monkeypatch):
    monkeypatch.setenv("OPS_CB_SMOKE_BUCKET_ID", "not-an-int")
    monkeypatch.setenv("OPS_CB_SMOKE_TODO_ID", "9946499598")
    assert cb_smoke.is_configured() is False


# ── ping posts via _post_comment with the marker in the body ──────


def test_ping_includes_marker_and_returns_ok(monkeypatch):
    monkeypatch.setenv("OPS_CB_SMOKE_BUCKET_ID", "100")
    monkeypatch.setenv("OPS_CB_SMOKE_TODO_ID", "200")
    monkeypatch.setattr(cb_smoke.tokens, "get_user_token",
                        lambda email: ("tok-x", "vault-oauth"))

    captured: dict = {}
    def _fake_post(bucket, rec, html, token):
        captured.update(bucket=bucket, rec=rec, html=html, token=token)
        return True, "ok"
    monkeypatch.setattr(cb_smoke.cb_mention_worker, "_post_comment", _fake_post)

    result = cb_smoke.ping(marker="smoke-abc123")

    assert result["ok"] is True
    assert result["marker"] == "smoke-abc123"
    assert captured["bucket"] == 100
    assert captured["rec"] == 200
    assert "smoke-abc123" in captured["html"]
    assert "@CB" in captured["html"], "ping body must trigger CB regex"


def test_ping_returns_no_token_when_vault_empty(monkeypatch):
    monkeypatch.setenv("OPS_CB_SMOKE_BUCKET_ID", "1")
    monkeypatch.setenv("OPS_CB_SMOKE_TODO_ID", "2")
    monkeypatch.setattr(cb_smoke.tokens, "get_user_token",
                        lambda email: (None, "missing"))

    result = cb_smoke.ping()
    assert result["ok"] is False
    assert result["detail"] == "no_token:missing"


def test_ping_returns_not_configured_when_env_unset():
    result = cb_smoke.ping()
    assert result["ok"] is False
    assert result["detail"] == "not_configured"


# ── verify ────────────────────────────────────────────────────────


def _fake_comment(*, created_at: str, body: str, email: str, name: str = "X") -> dict:
    return {
        "id": 1, "created_at": created_at,
        "content": f"<p>{body}</p>",
        "creator": {"email_address": email, "name": name},
    }


def test_verify_finds_reply_after_ping(monkeypatch):
    monkeypatch.setenv("OPS_CB_SMOKE_BUCKET_ID", "100")
    monkeypatch.setenv("OPS_CB_SMOKE_TODO_ID", "200")
    monkeypatch.setattr(cb_smoke.tokens, "get_user_token",
                        lambda email: ("tok", "vault-oauth"))

    ping_at = "2026-06-09T03:00:00+00:00"
    reply_at = "2026-06-09T03:02:30+00:00"
    payload = [
        # Older comment from someone else — must be ignored (created before ping).
        _fake_comment(created_at="2026-06-09T02:50:00+00:00",
                      body="old", email="other@colaberry.com"),
        # The ping itself (same email as the pinger).
        _fake_comment(created_at=ping_at, body="smoke-xyz",
                      email="ali@colaberry.com"),
        # The reply we want to find.
        _fake_comment(created_at=reply_at,
                      body="CB System · automated response",
                      email="cb-system@colaberry.com", name="CB System"),
    ]
    class _FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return b""
        def __iter__(self): return iter([])
    def _urlopen(req, timeout=None):
        r = _FakeResp()
        r.read = lambda: json.dumps(payload).encode("utf-8")
        # json.load reads from the response object via `.read()`
        return r
    # json.load needs a file-like; patch json.load instead for simplicity
    monkeypatch.setattr(cb_smoke.urllib.request, "urlopen",
                        lambda req, timeout: _FakeResp())
    monkeypatch.setattr(cb_smoke.json, "load", lambda r: payload)

    v = cb_smoke.verify(after_iso=ping_at, marker="smoke-xyz",
                        ping_user_email="ali@colaberry.com")
    assert v["found_reply"] is True
    assert v["replier"] == "CB System"
    assert v["latency_seconds"] == 150.0  # 2m30s


def test_verify_times_out_when_no_reply(monkeypatch):
    monkeypatch.setenv("OPS_CB_SMOKE_BUCKET_ID", "1")
    monkeypatch.setenv("OPS_CB_SMOKE_TODO_ID", "2")
    monkeypatch.setattr(cb_smoke.tokens, "get_user_token",
                        lambda email: ("tok", "vault-oauth"))

    ping_at = "2026-06-09T03:00:00+00:00"
    payload = [
        # Only the ping itself, no reply.
        _fake_comment(created_at=ping_at, body="smoke-xyz",
                      email="ali@colaberry.com"),
    ]
    class _FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
    monkeypatch.setattr(cb_smoke.urllib.request, "urlopen",
                        lambda req, timeout: _FakeResp())
    monkeypatch.setattr(cb_smoke.json, "load", lambda r: payload)

    v = cb_smoke.verify(after_iso=ping_at, marker="smoke-xyz",
                        ping_user_email="ali@colaberry.com")
    assert v["found_reply"] is False
    assert v["detail"] == "timeout"


def test_verify_ignores_replies_from_ping_author(monkeypatch):
    """A second comment from the SAME author isn't a 'reply' — could be the
    pinger commenting again on the smoke todo for unrelated reasons."""
    monkeypatch.setenv("OPS_CB_SMOKE_BUCKET_ID", "1")
    monkeypatch.setenv("OPS_CB_SMOKE_TODO_ID", "2")
    monkeypatch.setattr(cb_smoke.tokens, "get_user_token",
                        lambda email: ("tok", "vault-oauth"))

    ping_at = "2026-06-09T03:00:00+00:00"
    payload = [
        _fake_comment(created_at=ping_at, body="smoke-xyz",
                      email="ali@colaberry.com"),
        _fake_comment(created_at="2026-06-09T03:05:00+00:00",
                      body="oh wait I meant something else",
                      email="ali@colaberry.com"),
    ]
    class _FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
    monkeypatch.setattr(cb_smoke.urllib.request, "urlopen",
                        lambda req, timeout: _FakeResp())
    monkeypatch.setattr(cb_smoke.json, "load", lambda r: payload)

    v = cb_smoke.verify(after_iso=ping_at, marker="smoke-xyz",
                        ping_user_email="ali@colaberry.com")
    assert v["found_reply"] is False


# ── run() orchestration (mocked sleep + alert) ────────────────────


def test_run_skips_when_not_configured(monkeypatch):
    """No env vars = no-op, no alert, no sleep."""
    sleep_calls = []
    monkeypatch.setattr(cb_smoke.time, "sleep", lambda s: sleep_calls.append(s))
    alert_calls = []
    monkeypatch.setattr(cb_smoke, "_alert",
                        lambda title, body: alert_calls.append((title, body)))

    summary = cb_smoke.run()
    assert summary["skipped"] is True
    assert summary["reason"] == "not_configured"
    assert sleep_calls == []
    assert alert_calls == []


def test_run_alerts_on_ping_failure(monkeypatch):
    monkeypatch.setenv("OPS_CB_SMOKE_BUCKET_ID", "1")
    monkeypatch.setenv("OPS_CB_SMOKE_TODO_ID", "2")
    monkeypatch.setattr(cb_smoke, "ping", lambda marker=None: {
        "ok": False, "detail": "http_403", "marker": None, "posted_at": None,
        "bucket_id": 1, "todo_id": 2,
    })
    sleep_calls: list = []
    monkeypatch.setattr(cb_smoke.time, "sleep", lambda s: sleep_calls.append(s))
    alerts: list = []
    monkeypatch.setattr(cb_smoke, "_alert",
                        lambda title, body: alerts.append((title, body)))

    summary = cb_smoke.run()
    assert summary["ok"] is False
    assert summary["stage"] == "ping"
    assert len(alerts) == 1
    assert "PING FAILED" in alerts[0][0]
    assert sleep_calls == [], "must not sleep when ping fails"


def test_run_alerts_on_no_reply(monkeypatch):
    monkeypatch.setenv("OPS_CB_SMOKE_BUCKET_ID", "1")
    monkeypatch.setenv("OPS_CB_SMOKE_TODO_ID", "2")
    monkeypatch.setenv("OPS_CB_SMOKE_TIMEOUT_MINUTES", "15")
    monkeypatch.setattr(cb_smoke, "ping", lambda marker=None: {
        "ok": True, "detail": "ok", "marker": "smoke-abc",
        "posted_at": "2026-06-09T03:00:00Z", "bucket_id": 1, "todo_id": 2,
        "user_email": "ali@colaberry.com",
    })
    monkeypatch.setattr(cb_smoke, "verify",
                        lambda *a, **kw: {"ok": False, "found_reply": False,
                                          "detail": "timeout"})
    sleep_seconds: list = []
    monkeypatch.setattr(cb_smoke.time, "sleep", lambda s: sleep_seconds.append(s))
    alerts: list = []
    monkeypatch.setattr(cb_smoke, "_alert",
                        lambda title, body: alerts.append((title, body)))

    summary = cb_smoke.run()
    assert summary["ok"] is False
    assert sleep_seconds == [15 * 60]
    assert len(alerts) == 1
    assert "NO REPLY" in alerts[0][0]
    # History line written
    assert cb_smoke.STATE_PATH.exists()
    line = cb_smoke.STATE_PATH.read_text(encoding="utf-8").strip().splitlines()[-1]
    assert json.loads(line)["ok"] is False


def test_run_records_success_and_does_not_alert(monkeypatch):
    monkeypatch.setenv("OPS_CB_SMOKE_BUCKET_ID", "1")
    monkeypatch.setenv("OPS_CB_SMOKE_TODO_ID", "2")
    monkeypatch.setenv("OPS_CB_SMOKE_TIMEOUT_MINUTES", "1")
    monkeypatch.setattr(cb_smoke, "ping", lambda marker=None: {
        "ok": True, "detail": "ok", "marker": "smoke-abc",
        "posted_at": "2026-06-09T03:00:00Z", "bucket_id": 1, "todo_id": 2,
        "user_email": "ali@colaberry.com",
    })
    monkeypatch.setattr(cb_smoke, "verify",
                        lambda *a, **kw: {"ok": True, "found_reply": True,
                                          "latency_seconds": 90.0,
                                          "replier": "CB System",
                                          "replier_email": "cb@x",
                                          "detail": "ok"})
    monkeypatch.setattr(cb_smoke.time, "sleep", lambda s: None)
    alerts: list = []
    monkeypatch.setattr(cb_smoke, "_alert",
                        lambda title, body: alerts.append((title, body)))

    summary = cb_smoke.run()
    assert summary["ok"] is True
    assert alerts == []
    line = json.loads(cb_smoke.STATE_PATH.read_text(encoding="utf-8")
                      .strip().splitlines()[-1])
    assert line["ok"] is True
    assert line["verify"]["replier"] == "CB System"


# ── _alert fallback (no channel = WARNING log) ────────────────────


def test_alert_logs_warning_when_no_channel_configured(caplog):
    with caplog.at_level("WARNING"):
        cb_smoke._alert("title", "body")
    assert any("cb_smoke ALERT" in rec.message for rec in caplog.records)
