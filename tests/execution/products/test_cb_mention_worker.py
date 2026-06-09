"""Unit tests for execution/products/ops/cb_mention_worker.py.

Covers the three changes that unblock CB after the 2026-06-09 silent-miss
incident on Basecamp todo 9946499598:
  1. Heartbeat is written every run so /admin/cb-mentions.json can surface
     liveness without grepping container logs.
  2. _post_comment sends a User-Agent that contains contact info (BC's API
     spec requires it; the old "Advisor CB System (auto-response)" string
     could 403).
  3. When the rich response fails, a sentinel comment is attempted on the
     ticket so a human asker sees CB *tried* — currently the seen-set
     swallows the mention and there's no footprint at all.

These are unit tests; the BC HTTP layer is mocked so they don't touch the
network or any prod credentials.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest

from execution.products.ops import cb_mention_worker as cb


@pytest.fixture(autouse=True)
def _isolate_heartbeat_and_seen(tmp_path, monkeypatch):
    """Redirect HEARTBEAT_PATH and SEEN_PATH to a tmp dir so tests don't
    clobber the real prod files in output/ops/_cb_mentions/."""
    monkeypatch.setattr(cb, "HEARTBEAT_PATH", tmp_path / "heartbeat.json")
    monkeypatch.setattr(cb, "SEEN_PATH", tmp_path / "seen.json")
    yield


# ── heartbeat ───────────────────────────────────────────────────────


def test_write_heartbeat_persists_summary_as_json():
    summary = {"started_at": "2026-06-09T06:30:00Z", "users_with_token": 2}
    cb._write_heartbeat(summary)
    assert cb.HEARTBEAT_PATH.exists()
    loaded = json.loads(cb.HEARTBEAT_PATH.read_text(encoding="utf-8"))
    assert loaded["users_with_token"] == 2
    assert loaded["started_at"] == "2026-06-09T06:30:00Z"


def test_write_heartbeat_swallows_oserror(tmp_path, monkeypatch):
    """Heartbeat write must not break the cron — disk full / permission
    errors get logged but the run is still considered successful.
    Simulate by pointing HEARTBEAT_PATH at a file whose 'parent' is
    actually a regular file (mkdir will fail with NotADirectoryError).
    """
    blocking_file = tmp_path / "blocker"
    blocking_file.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(cb, "HEARTBEAT_PATH", blocking_file / "heartbeat.json")
    # Should not raise
    cb._write_heartbeat({"x": 1})


# ── _post_comment User-Agent (BC API spec compliance) ──────────────


def test_post_comment_user_agent_contains_contact_info():
    """BC's API readme: 'identify your application... include an application
    name and version, or a contact email address (or both)'. Without contact
    info BC can return 403. The old UA 'Advisor CB System (auto-response)'
    had no email — this regression test pins the fix.
    """
    captured = {}
    class _FakeResp:
        status = 201
        def __enter__(self): return self
        def __exit__(self, *a): pass

    def _urlopen(req, timeout=None):
        captured["headers"] = dict(req.headers)
        captured["url"] = req.full_url
        return _FakeResp()

    with patch.object(cb.urllib.request, "urlopen", _urlopen):
        ok, detail = cb._post_comment(123, 456, "<p>hi</p>", "tok")

    assert ok is True
    assert detail == "ok"
    ua = captured["headers"].get("User-agent") or captured["headers"].get("User-Agent")
    assert ua, "User-Agent header must be present"
    assert "@" in ua, f"User-Agent must contain contact email, got: {ua!r}"


def test_post_comment_returns_http_code_on_failure():
    import urllib.error
    def _urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)
    with patch.object(cb.urllib.request, "urlopen", _urlopen):
        ok, detail = cb._post_comment(1, 2, "<p>x</p>", "tok")
    assert ok is False
    assert detail == "http_403"


# ── sentinel fallback on post failure ──────────────────────────────


def test_scan_for_user_posts_sentinel_when_rich_post_fails(monkeypatch):
    """When the rich response POST fails, a sentinel must be attempted so
    a human reading the BC thread sees that CB tried — eliminates the
    'silent skip with no footprint' failure class.
    """
    monkeypatch.setattr(cb.tokens, "get_user_token",
                        lambda uid: ("fake-tok", "vault-oauth"))
    # One bucket, one fresh mention
    monkeypatch.setattr(
        "execution.products.ops.sync.discover_projects",
        lambda token: [{"id": 47502609, "name": "Test bucket"}],
    )
    fresh = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime(
        "%Y-%m-%dT%H:%M:%S+00:00"
    )
    monkeypatch.setattr(
        cb, "_scan_bucket_for_mentions",
        lambda bucket, token, cutoff: [{
            "comment_id": 99999, "comment_url": "u",
            "comment_body": "@CB please help",
            "created_at": fresh, "creator_name": "Ali",
            "parent_url": "https://3.basecamp.com/x/buckets/1/todos/2",
            "parent_id": 2, "bucket": bucket,
        }],
    )
    monkeypatch.setattr(cb, "_parent_is_closed", lambda *a, **kw: False)
    monkeypatch.setattr(cb.context_collector, "collect", lambda *a, **kw: {})
    monkeypatch.setattr(cb.plan_inference, "infer",
                        lambda **kw: {"anticipated_goal": "help"})

    posts: list[str] = []
    def _post(bucket, recording_id, html, token):
        posts.append(html)
        # First call (rich) fails; second call (sentinel) succeeds
        return (len(posts) > 1, "http_500" if len(posts) == 1 else "ok")
    monkeypatch.setattr(cb, "_post_comment", _post)

    result = cb.scan_for_user("ali@colaberry.com")

    assert len(posts) == 2, "expected rich post + sentinel fallback"
    assert posts[1] == cb.SENTINEL_HTML
    assert result["failed"] == 1
    assert result["responded"] == 0
    assert any(e.get("stage") == "post_comment" for e in result["errors"])


def test_scan_for_user_no_sentinel_when_rich_post_succeeds(monkeypatch):
    """Happy path: only one POST (the rich response), no sentinel spam."""
    monkeypatch.setattr(cb.tokens, "get_user_token",
                        lambda uid: ("fake-tok", "vault-oauth"))
    monkeypatch.setattr(
        "execution.products.ops.sync.discover_projects",
        lambda token: [{"id": 1, "name": "B"}],
    )
    fresh = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime(
        "%Y-%m-%dT%H:%M:%S+00:00"
    )
    monkeypatch.setattr(
        cb, "_scan_bucket_for_mentions",
        lambda bucket, token, cutoff: [{
            "comment_id": 1, "comment_url": "u", "comment_body": "@CB",
            "created_at": fresh, "creator_name": "Ali",
            "parent_url": "https://x", "parent_id": 2, "bucket": bucket,
        }],
    )
    monkeypatch.setattr(cb, "_parent_is_closed", lambda *a, **kw: False)
    monkeypatch.setattr(cb.context_collector, "collect", lambda *a, **kw: {})
    monkeypatch.setattr(cb.plan_inference, "infer", lambda **kw: {})

    posts: list = []
    def _post(*args):
        posts.append(args)
        return True, "ok"
    monkeypatch.setattr(cb, "_post_comment", _post)

    result = cb.scan_for_user("ali@colaberry.com")
    assert len(posts) == 1
    assert result["responded"] == 1
    assert result["failed"] == 0


# ── no_token + bucket truncation produce WARNING-visible state ─────


def test_scan_for_user_no_token_returns_status(monkeypatch, caplog):
    monkeypatch.setattr(cb.tokens, "get_user_token", lambda uid: (None, "missing"))
    with caplog.at_level("WARNING"):
        r = cb.scan_for_user("ali@colaberry.com")
    assert r["status"] == "no_token"
    assert any("no BC token" in rec.message for rec in caplog.records)


def test_scan_for_user_logs_bucket_truncation(monkeypatch, caplog):
    monkeypatch.setattr(cb.tokens, "get_user_token", lambda uid: ("tok", "vault-oauth"))
    # 75 buckets > MAX_BUCKETS (50)
    monkeypatch.setattr(
        "execution.products.ops.sync.discover_projects",
        lambda token: [{"id": i} for i in range(75)],
    )
    monkeypatch.setattr(cb, "_scan_bucket_for_mentions", lambda *a, **kw: [])

    with caplog.at_level("WARNING"):
        r = cb.scan_for_user("ali@colaberry.com", max_buckets=50)

    assert r["buckets_truncated"] == 25
    assert any("buckets_truncated" not in rec.message and "truncated=25" in rec.message
               for rec in caplog.records)


# ── scan_all_users writes a heartbeat ──────────────────────────────


def test_scan_all_users_writes_heartbeat(monkeypatch):
    """The whole point of the change: every cron tick leaves a file we can
    point /admin/cb-mentions.json at."""
    fake_user = MagicMock(user_id="usr-1", email="ali@colaberry.com")

    monkeypatch.setattr(
        "execution.products.library.tenancy.list_users",
        lambda active_only=True: [fake_user],
    )
    monkeypatch.setattr(
        "execution.products.library.vault.list_for_user",
        lambda user_id, caller_id: [MagicMock(tool_name="basecamp_ai_clone")],
    )
    monkeypatch.setattr(
        cb, "scan_for_user",
        lambda email: {
            "status": "ok", "checked_buckets": 3, "mentions_found": 2,
            "responded": 2, "failed": 0, "skipped_already_seen": 0,
            "skipped_closed_parent": 0, "token_source": "vault-oauth",
            "errors": [], "buckets_truncated": 0,
        },
    )

    summary = cb.scan_all_users()
    assert cb.HEARTBEAT_PATH.exists()
    loaded = json.loads(cb.HEARTBEAT_PATH.read_text(encoding="utf-8"))
    assert loaded["users_with_token"] == 1
    assert loaded["total_mentions_found"] == 2
    assert loaded["total_responded"] == 2
    assert loaded["fatal_error"] is None
    assert summary == loaded


def test_scan_all_users_records_fatal_when_tenancy_breaks(monkeypatch):
    monkeypatch.setattr(
        "execution.products.library.tenancy.list_users",
        MagicMock(side_effect=RuntimeError("db down")),
    )
    summary = cb.scan_all_users()
    assert summary["fatal_error"] == "list_users_failed:RuntimeError"
    assert summary["users_with_token"] == 0


# ── trigger regex (regression: don't drop @-mention chips) ─────────


@pytest.mark.parametrize("body", [
    "@CB please help",
    "@CB System turn this into a PPT",
    "Hey @CB_System, thoughts?",
    "@cb system thoughts?",            # case-insensitive
    "@CBSystem direct",
])
def test_trigger_regex_matches_expected_forms(body):
    assert cb.TRIGGER_RE.search(body) is not None, f"should match: {body!r}"


@pytest.mark.parametrize("body", [
    "team CB had a sync",       # no leading @
    "FYI: scb pipeline broke",  # 'scb' is not '@CB'
    "",
])
def test_trigger_regex_rejects_non_mentions(body):
    assert cb.TRIGGER_RE.search(body) is None, f"should NOT match: {body!r}"
