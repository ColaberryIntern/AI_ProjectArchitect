"""Tests for the per-(user, bucket) cursor in cb_mention_worker.

The wall-clock window is fragile: if the scheduler is down for 80 min and
the next scan only looks back MENTION_WINDOW_MINUTES (default 60),
mentions created during the gap are silently lost. Cursors fix this by
remembering the last successful scan timestamp per bucket — lookback
extends to the gap, capped at MAX_LOOKBACK_MINUTES.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from execution.products.ops import cb_mention_worker as cb


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    monkeypatch.setattr(cb, "CURSOR_PATH", tmp_path / "cursor.json")
    monkeypatch.setattr(cb, "SEEN_PATH", tmp_path / "seen.json")
    monkeypatch.setattr(cb, "HEARTBEAT_PATH", tmp_path / "heartbeat.json")
    yield


# ── load / save cursor ────────────────────────────────────────────


def test_load_cursors_empty_when_file_missing():
    assert cb._load_cursors() == {}


def test_load_cursors_returns_per_user_dict_only():
    """The file is `{"per_user": {...}}`; callers want the inner dict."""
    cb.CURSOR_PATH.parent.mkdir(parents=True, exist_ok=True)
    cb.CURSOR_PATH.write_text(json.dumps({
        "per_user": {"ali@colaberry.com": {"100": "2026-06-09T03:00:00Z"}}
    }), encoding="utf-8")
    assert cb._load_cursors() == {
        "ali@colaberry.com": {"100": "2026-06-09T03:00:00Z"}
    }


def test_load_cursors_tolerates_corrupt_json():
    cb.CURSOR_PATH.parent.mkdir(parents=True, exist_ok=True)
    cb.CURSOR_PATH.write_text("not json", encoding="utf-8")
    assert cb._load_cursors() == {}


def test_save_then_load_roundtrip():
    payload = {"ali@colaberry.com": {"100": "2026-06-09T03:00:00Z",
                                      "200": "2026-06-09T04:00:00Z"}}
    cb._save_cursors(payload)
    assert cb._load_cursors() == payload


# ── _cutoff_for_bucket ────────────────────────────────────────────


def test_cutoff_first_scan_uses_window(monkeypatch):
    """No cursor for this bucket → wall-clock MENTION_WINDOW_MINUTES."""
    monkeypatch.setattr(cb, "MENTION_WINDOW_MINUTES", 60)
    now = datetime(2026, 6, 9, 6, 30, tzinfo=timezone.utc)
    cutoff, source = cb._cutoff_for_bucket({}, 47502609, now)
    assert source == "first_scan"
    assert cutoff == now - timedelta(minutes=60)


def test_cutoff_uses_cursor_when_within_max_lookback(monkeypatch):
    monkeypatch.setattr(cb, "MAX_LOOKBACK_MINUTES", 10080)  # 7 days
    now = datetime(2026, 6, 9, 6, 30, tzinfo=timezone.utc)
    # 2 hours ago — well within 7 days
    cursor_iso = (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cutoff, source = cb._cutoff_for_bucket(
        {"47502609": cursor_iso}, 47502609, now,
    )
    assert source == "cursor"
    assert cutoff == datetime.fromisoformat(cursor_iso.replace("Z", "+00:00"))


def test_cutoff_clamps_to_max_lookback_when_cursor_is_ancient(monkeypatch):
    """Scheduler down for 14 days; we should look back 7 days, not 14."""
    monkeypatch.setattr(cb, "MAX_LOOKBACK_MINUTES", 10080)  # 7 days
    now = datetime(2026, 6, 9, 6, 30, tzinfo=timezone.utc)
    ancient = (now - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cutoff, source = cb._cutoff_for_bucket(
        {"47502609": ancient}, 47502609, now,
    )
    assert source == "cursor_capped"
    assert cutoff == now - timedelta(minutes=10080)


def test_cutoff_falls_back_when_cursor_unparseable(monkeypatch):
    monkeypatch.setattr(cb, "MENTION_WINDOW_MINUTES", 60)
    now = datetime(2026, 6, 9, 6, 30, tzinfo=timezone.utc)
    cutoff, source = cb._cutoff_for_bucket(
        {"47502609": "garbage"}, 47502609, now,
    )
    assert source == "first_scan"
    assert cutoff == now - timedelta(minutes=60)


# ── scan_for_user integration with cursors ────────────────────────


def _stub_scan_environment(monkeypatch, bucket_ids):
    monkeypatch.setattr(cb.tokens, "get_user_token",
                        lambda uid: ("tok", "vault-oauth"))
    monkeypatch.setattr(
        "execution.products.ops.sync.discover_projects",
        lambda token: [{"id": b, "name": f"B{b}"} for b in bucket_ids],
    )
    monkeypatch.setattr(cb, "_scan_bucket_for_mentions",
                        lambda bucket, token, cutoff: [])


def test_scan_for_user_advances_cursor_for_every_scanned_bucket(monkeypatch):
    _stub_scan_environment(monkeypatch, [100, 200, 300])
    result = cb.scan_for_user("ali@colaberry.com")
    assert result["status"] == "ok"
    assert result["cutoff_sources"]["first_scan"] == 3
    persisted = cb._load_cursors()["ali@colaberry.com"]
    assert set(persisted.keys()) == {"100", "200", "300"}


def test_second_scan_uses_cursor_not_window(monkeypatch):
    """Run twice; the second scan should report 'cursor' as the source."""
    _stub_scan_environment(monkeypatch, [100])
    cb.scan_for_user("ali@colaberry.com")
    result2 = cb.scan_for_user("ali@colaberry.com")
    assert result2["cutoff_sources"]["cursor"] == 1
    assert result2["cutoff_sources"]["first_scan"] == 0


def test_scan_passes_cursor_as_cutoff_to_bucket_scan(monkeypatch):
    """The cutoff handed to _scan_bucket_for_mentions on the SECOND scan
    must equal the cursor stored after the FIRST scan — *not* now-60min.
    This is the bug fix in one assertion.
    """
    _stub_scan_environment(monkeypatch, [100])
    cb.scan_for_user("ali@colaberry.com")

    captured = {}
    def _capture(bucket, token, cutoff):
        captured["cutoff"] = cutoff
        return []
    monkeypatch.setattr(cb, "_scan_bucket_for_mentions", _capture)

    cb.scan_for_user("ali@colaberry.com")
    cursor_iso = cb._load_cursors()["ali@colaberry.com"]["100"]
    # captured cutoff (the one used on scan 2) is the cursor from scan 1.
    # Allow 1s of drift — _now_iso truncates to seconds.
    expected = datetime.fromisoformat(cursor_iso.replace("Z", "+00:00"))
    assert abs((captured["cutoff"] - expected).total_seconds()) < 2


def test_scan_does_not_clobber_other_users_cursors(monkeypatch):
    """Concurrent users: ali's scan must NOT overwrite kes's cursor map."""
    cb._save_cursors({
        "kes@colaberry.com": {"999": "2026-06-01T00:00:00Z"},
    })
    _stub_scan_environment(monkeypatch, [100])
    cb.scan_for_user("ali@colaberry.com")
    final = cb._load_cursors()
    assert "kes@colaberry.com" in final
    assert final["kes@colaberry.com"]["999"] == "2026-06-01T00:00:00Z"
    assert "ali@colaberry.com" in final
    assert "100" in final["ali@colaberry.com"]


def test_long_outage_uses_capped_cursor(monkeypatch):
    """Cursor older than MAX_LOOKBACK → lookback is clamped."""
    monkeypatch.setattr(cb, "MAX_LOOKBACK_MINUTES", 60)  # 1h cap for test
    monkeypatch.setattr(cb, "MENTION_WINDOW_MINUTES", 10)
    ancient = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    cb._save_cursors({"ali@colaberry.com": {"100": ancient}})

    _stub_scan_environment(monkeypatch, [100])
    result = cb.scan_for_user("ali@colaberry.com")
    assert result["cutoff_sources"]["cursor_capped"] == 1
    assert result["cutoff_sources"]["cursor"] == 0
