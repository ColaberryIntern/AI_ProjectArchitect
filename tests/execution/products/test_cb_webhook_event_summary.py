"""Unit tests for cb_webhooks.recent_event_summary.

Reads the append-only webhook_events.jsonl and returns aggregated
counts. Stays usable when the file is missing, when lines are malformed,
and when the log is huge (last-5000-lines cap).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from execution.products.ops import cb_webhooks


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(cb_webhooks, "EVENT_LOG_PATH",
                        tmp_path / "webhook_events.jsonl")
    yield


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _write(records: list[dict]) -> None:
    cb_webhooks.EVENT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with cb_webhooks.EVENT_LOG_PATH.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── empty / missing ───────────────────────────────────────────────


def test_missing_file_returns_zero_summary():
    s = cb_webhooks.recent_event_summary(window_minutes=60)
    assert s["events_total"] == 0
    assert s["responded"] == 0
    assert s["failures"] == 0
    assert s["last_event_at"] is None
    assert s["window_minutes"] == 60


def test_empty_file_returns_zero_summary():
    cb_webhooks.EVENT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    cb_webhooks.EVENT_LOG_PATH.write_text("", encoding="utf-8")
    s = cb_webhooks.recent_event_summary(window_minutes=60)
    assert s["events_total"] == 0
    assert s["last_event_at"] is None


# ── window filter ─────────────────────────────────────────────────


def test_events_outside_window_excluded():
    now = _now()
    inside = _iso(now - timedelta(minutes=5))
    outside = _iso(now - timedelta(minutes=120))
    _write([
        {"received_at": inside, "responded": True},
        {"received_at": outside, "responded": True},
    ])
    s = cb_webhooks.recent_event_summary(window_minutes=60)
    assert s["events_total"] == 1
    assert s["responded"] == 1


# ── skip-tag buckets ──────────────────────────────────────────────


def test_named_skip_tags_each_count_in_own_bucket():
    now = _now()
    ts = _iso(now - timedelta(minutes=1))
    _write([
        {"received_at": ts, "skipped": "no_trigger"},
        {"received_at": ts, "skipped": "no_trigger"},
        {"received_at": ts, "skipped": "already_seen"},
        {"received_at": ts, "skipped": "parent_closed"},
        {"received_at": ts, "skipped": "no_token:missing"},
    ])
    s = cb_webhooks.recent_event_summary(window_minutes=60)
    assert s["skipped_no_trigger"] == 2
    assert s["skipped_already_seen"] == 1
    assert s["skipped_parent_closed"] == 1
    assert s["skipped_no_token"] == 1
    assert s["skipped_other"] == 0
    assert s["events_total"] == 5


def test_unknown_skip_lands_in_skipped_other():
    now = _now()
    ts = _iso(now - timedelta(minutes=1))
    _write([
        {"received_at": ts, "skipped": "non_comment"},
        {"received_at": ts, "skipped": "missing_parent_or_bucket"},
        {"received_at": ts, "skipped": "something_brand_new"},
    ])
    s = cb_webhooks.recent_event_summary(window_minutes=60)
    assert s["skipped_other"] == 3
    assert s["skipped_no_trigger"] == 0


# ── failures vs responded ─────────────────────────────────────────


def test_responded_false_without_skipped_counts_as_failure():
    now = _now()
    ts = _iso(now - timedelta(minutes=1))
    _write([
        {"received_at": ts, "responded": False, "post_detail": "http_500"},
        {"received_at": ts, "responded": True},
    ])
    s = cb_webhooks.recent_event_summary(window_minutes=60)
    assert s["responded"] == 1
    assert s["failures"] == 1


# ── resilience ────────────────────────────────────────────────────


def test_malformed_json_lines_are_skipped():
    now = _now()
    ts = _iso(now - timedelta(minutes=1))
    cb_webhooks.EVENT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with cb_webhooks.EVENT_LOG_PATH.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"received_at": ts, "responded": True}) + "\n")
        f.write("{this is not valid json\n")
        f.write(json.dumps({"received_at": ts, "skipped": "no_trigger"}) + "\n")
    s = cb_webhooks.recent_event_summary(window_minutes=60)
    assert s["events_total"] == 2
    assert s["responded"] == 1
    assert s["skipped_no_trigger"] == 1


def test_only_last_5000_lines_are_considered():
    """Write 5050 in-window lines; the 50 oldest must drop out so the
    admin endpoint stays O(5000) regardless of log growth."""
    now = _now()
    ts = _iso(now - timedelta(minutes=1))
    cb_webhooks.EVENT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with cb_webhooks.EVENT_LOG_PATH.open("w", encoding="utf-8") as f:
        for _ in range(5050):
            f.write(json.dumps({"received_at": ts, "responded": True}) + "\n")
    s = cb_webhooks.recent_event_summary(window_minutes=60)
    assert s["events_total"] == 5000
    assert s["responded"] == 5000


# ── last_event_at ─────────────────────────────────────────────────


def test_last_event_at_reflects_newest_event_in_window():
    now = _now()
    older = now - timedelta(minutes=10)
    newer = now - timedelta(minutes=2)
    _write([
        {"received_at": _iso(older), "responded": True},
        {"received_at": _iso(newer), "responded": True},
    ])
    s = cb_webhooks.recent_event_summary(window_minutes=60)
    assert s["last_event_at"] == _iso(newer)
