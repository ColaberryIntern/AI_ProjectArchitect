"""Phase 7D tests: tracing + alerts + notifications + prometheus_exporter."""

import json
import time

import pytest

from execution.ops_platform import (
    alerts, audit_log, cache_bus, notifications, prometheus_exporter,
    realtime_bus, secrets, tracing,
)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(tracing, "_TRACING_DIR", tmp_path / "tracing")
    monkeypatch.setattr(alerts, "_ALERTS_DIR", tmp_path / "alerts")
    monkeypatch.setattr(alerts, "_RULES_DIR", tmp_path / "alerts" / "rules")
    monkeypatch.setattr(alerts, "_ACTIVE_DIR", tmp_path / "alerts" / "active")
    monkeypatch.setattr(alerts, "_HISTORY_DIR", tmp_path / "alerts" / "history")
    monkeypatch.setattr(notifications, "_NOTIF_DIR", tmp_path / "notifications")
    monkeypatch.setattr(notifications, "_CHANNELS_DIR", tmp_path / "notifications" / "channels")
    monkeypatch.setattr(realtime_bus, "_EVENTS_DIR", tmp_path / "events")
    monkeypatch.setattr(realtime_bus, "_SEQUENCE_PATH", tmp_path / "sequence.json")
    monkeypatch.setattr(audit_log, "_AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(cache_bus, "_VERSION_DIR", tmp_path / "versions")
    monkeypatch.setattr(secrets, "_SECRETS_DIR", tmp_path / "secrets")
    cache_bus.reset_for_tests()
    realtime_bus.reset_for_tests()
    yield


# ── tracing ───────────────────────────────────────────────────────────


def test_span_records_to_disk():
    with tracing.span("test.outer", attributes={"x": 1}) as s:
        s.set_attribute("y", 2)
        time.sleep(0.01)
    recent = tracing.list_recent(days=1, limit=10)
    assert any(r["name"] == "test.outer" for r in recent)


def test_nested_spans_share_trace_id():
    with tracing.span("parent") as parent:
        with tracing.span("child") as child:
            assert child.trace_id == parent.trace_id
            assert child.parent_span_id == parent.span_id


def test_span_exception_marks_error():
    try:
        with tracing.span("bad"):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    recent = tracing.list_recent(days=1, limit=10)
    bad = next((r for r in recent if r["name"] == "bad"), None)
    assert bad is not None
    assert bad["status"] == "error"
    assert "boom" in bad["error_message"]


def test_trace_tree_groups_by_trace_id():
    with tracing.span("a") as outer:
        with tracing.span("b"):
            pass
    tree = tracing.trace_tree(outer.trace_id)
    assert len(tree) >= 2


# ── alerts ────────────────────────────────────────────────────────────


def test_upsert_and_list_rule():
    alerts.upsert_rule(rule_id="r1", name="queue depth high", metric="queue.depth",
                         operator=">", threshold=100)
    rules = alerts.list_rules()
    assert any(r.rule_id == "r1" for r in rules)


def test_evaluate_fires_when_threshold_crossed():
    alerts.upsert_rule(rule_id="r2", name="x", metric="m", operator=">",
                         threshold=10, severity=4)
    fired = alerts.evaluate_rules(metric_values={"m": 15})
    assert fired
    assert fired[0].state == "open"


def test_evaluate_does_not_double_fire():
    alerts.upsert_rule(rule_id="r3", name="x", metric="m", operator=">",
                         threshold=10)
    alerts.evaluate_rules(metric_values={"m": 15})
    second = alerts.evaluate_rules(metric_values={"m": 15})
    # Active alert exists; evaluator should not open a duplicate
    active_count = len(alerts.list_active(rule_id="r3"))
    assert active_count == 1


def test_resolve_clears_alert():
    alerts.upsert_rule(rule_id="r4", name="x", metric="m", operator=">",
                         threshold=10)
    fired = alerts.evaluate_rules(metric_values={"m": 15})
    a = alerts.resolve(fired[0].alert_id)
    assert a.state == "resolved"
    assert not alerts.list_active(rule_id="r4")


def test_evaluate_resolves_when_value_drops():
    alerts.upsert_rule(rule_id="r5", name="x", metric="m", operator=">",
                         threshold=10)
    alerts.evaluate_rules(metric_values={"m": 15})
    alerts.evaluate_rules(metric_values={"m": 5})
    assert not alerts.list_active(rule_id="r5")


def test_acknowledge_alert():
    alerts.upsert_rule(rule_id="r6", name="x", metric="m", operator=">",
                         threshold=10)
    fired = alerts.evaluate_rules(metric_values={"m": 15})
    a = alerts.acknowledge(fired[0].alert_id, actor="alice")
    assert a.state == "acknowledged"


# ── notifications ────────────────────────────────────────────────────


def test_upsert_channel_validates_kind():
    with pytest.raises(ValueError):
        notifications.upsert_channel(channel_id="x", name="x", kind="bogus", config={})


def test_send_to_missing_channel_records_failure():
    rec = notifications.send("nonexistent", title="t", body="b")
    assert rec.success is False


def test_webhook_send_with_unreachable_url_records_failure(tmp_path):
    notifications.upsert_channel(channel_id="wh", name="webhook", kind="webhook",
                                    config={"url": "http://127.0.0.1:1/nope"})
    rec = notifications.send("wh", title="t", body="b")
    assert rec.success is False
    assert rec.attempt_count == notifications.MAX_RETRIES


# ── prometheus_exporter ──────────────────────────────────────────────


def test_render_includes_help_lines():
    text = prometheus_exporter.render()
    assert "# HELP" in text
    assert "# TYPE" in text
    assert "ops_queue_depth" in text
    assert "ops_capability_total" in text
