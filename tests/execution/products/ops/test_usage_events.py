"""Tests for My Day first-party usage telemetry (record + aggregate)."""

import datetime as dt

import execution.products.ops.usage_events as ue


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(ue, "EVENTS_DIR", tmp_path / "usage_events")


def test_record_and_aggregate(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    now = dt.datetime(2026, 7, 8, 12, 0, tzinfo=dt.timezone.utc)
    n = ue.record_events("ali@colaberry.com", [
        {"type": "view", "label": "myday.view.kanban", "view": "kanban", "tier": "human", "project": "123"},
        {"type": "click", "label": "myday.sync"},
        {"type": "bogus", "label": "x"},            # dropped: bad type
        "not-a-dict",                                # dropped
    ], now=now)
    assert n == 2

    agg = ue.aggregate(since_days=7, now=now)
    assert agg["total_events"] == 2
    assert agg["unique_users"] == 1
    assert agg["views"]["kanban"] == 1
    assert agg["tiers"]["human"] == 1
    assert agg["top_controls"]["myday.sync"] == 1
    assert agg["filter_use"]["project"] == 1
    assert agg["filter_use"]["list"] == 0


def test_batch_capped_and_labels_sanitized(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    now = dt.datetime(2026, 7, 8, tzinfo=dt.timezone.utc)
    assert ue.record_events("u", [{"type": "click", "label": f"c{i}"} for i in range(60)], now=now) == 50
    ue.record_events("u", [{"type": "click", "label": "<script>alert(1)</script>"}], now=now)
    agg = ue.aggregate(now=now)
    assert not any("<" in k for k in agg["top_controls"])          # angle brackets stripped


def test_window_excludes_old_events(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    old = dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc)
    new = dt.datetime(2026, 7, 8, tzinfo=dt.timezone.utc)
    ue.record_events("u", [{"type": "view", "view": "briefing"}], now=old)
    ue.record_events("u", [{"type": "view", "view": "kanban"}], now=new)
    agg = ue.aggregate(since_days=14, now=new)
    assert "briefing" not in agg["views"]                          # 37 days old → outside window
    assert agg["views"]["kanban"] == 1


def test_bad_input_never_raises(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    assert ue.record_events("u", "not a list") == 0
    assert ue.record_events("u", None) == 0
    assert ue.record_events("u", [1, 2, "x", {"type": "click", "label": "ok"}]) == 1


def test_empty_aggregate_when_no_events(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    agg = ue.aggregate()
    assert agg["total_events"] == 0 and agg["unique_users"] == 0
    assert agg["views"] == {} and agg["top_controls"] == {}
