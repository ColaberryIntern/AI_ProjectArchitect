"""Phase 9A tests: event_fabric — unified event log + consistency declarations."""

import pytest

from execution.ops_platform import event_fabric


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(event_fabric, "_EVENTS_DIR", tmp_path / "fabric")
    monkeypatch.setattr(event_fabric, "_SEQUENCE_PATH", tmp_path / "seq.json")
    event_fabric.reset_for_tests()
    yield
    event_fabric.reset_for_tests()


def test_emit_then_replay_returns_event():
    ev = event_fabric.emit("test.x", payload={"k": 1}, actor_id="alice")
    rows = event_fabric.replay()
    assert any(r.event_id == ev.event_id for r in rows)


def test_sequence_is_monotonic():
    a = event_fabric.emit("seq.a")
    b = event_fabric.emit("seq.b")
    c = event_fabric.emit("seq.c")
    assert b.sequence > a.sequence
    assert c.sequence > b.sequence


def test_emit_rejects_unknown_durability_scope():
    with pytest.raises(ValueError):
        event_fabric.emit("test.x", durability_scope="bogus")


def test_emit_rejects_unknown_consistency_scope():
    with pytest.raises(ValueError):
        event_fabric.emit("test.x", consistency_scope="exactly-once")


def test_explicit_scopes_persist():
    ev = event_fabric.emit("redis.x",
                              durability_scope="redis-distributed",
                              consistency_scope="strict-per-stream")
    rows = event_fabric.replay()
    saved = next(r for r in rows if r.event_id == ev.event_id)
    assert saved.durability_scope == "redis-distributed"
    assert saved.consistency_scope == "strict-per-stream"


def test_replay_since_sequence():
    event_fabric.emit("x")
    pivot = event_fabric.emit("y")
    event_fabric.emit("z")
    out = event_fabric.replay(since_sequence=pivot.sequence)
    assert all(r.sequence > pivot.sequence for r in out)


def test_replay_filters_event_type():
    event_fabric.emit("foo.a")
    event_fabric.emit("bar.b")
    rows = event_fabric.replay(event_types=["foo.a"])
    assert all(r.event_type == "foo.a" for r in rows)


def test_replay_filters_workspace():
    event_fabric.emit("x", workspace_id="ws1")
    event_fabric.emit("x", workspace_id="ws2")
    rows = event_fabric.replay(workspace_id="ws1")
    assert all(r.workspace_id == "ws1" for r in rows)


def test_replay_filters_correlation():
    event_fabric.emit("x", correlation_id="corr-1")
    event_fabric.emit("x", correlation_id="corr-2")
    rows = event_fabric.replay(correlation_id="corr-1")
    assert all(r.correlation_id == "corr-1" for r in rows)


def test_subscriber_receives_emitted_event():
    sub_id, q, notify = event_fabric.subscribe(event_types=["sub.test"])
    event_fabric.emit("sub.test", payload={"hit": True})
    # No threading concerns — emit is synchronous, fanout is in-process
    seen = list(q)
    assert seen and seen[0].event_type == "sub.test"
    event_fabric.unsubscribe(sub_id)


def test_consistency_report_returns_counts():
    event_fabric.emit("a", consistency_scope="at-least-once")
    event_fabric.emit("b", consistency_scope="strict-per-stream")
    report = event_fabric.consistency_report()
    assert report["samples"] >= 2
    assert "at-least-once" in report["by_consistency_scope"]


def test_sse_wire_format():
    ev = event_fabric.emit("sse.test")
    line = ev.to_sse()
    assert "id:" in line
    assert "event: sse.test" in line
    assert "data:" in line


def test_realtime_bus_adapter_forwards_to_fabric(monkeypatch):
    """Calling realtime_bus.emit after installing the adapter results in a
    matching fabric event."""
    from execution.ops_platform import realtime_bus
    # Reset adapter flag so the install is fresh
    if hasattr(realtime_bus, "_fabric_adapter_installed"):
        delattr(realtime_bus, "_fabric_adapter_installed")
    monkeypatch.setattr(realtime_bus, "_EVENTS_DIR", event_fabric._EVENTS_DIR.parent / "rt")
    monkeypatch.setattr(realtime_bus, "_SEQUENCE_PATH", event_fabric._EVENTS_DIR.parent / "rtseq.json")
    realtime_bus.reset_for_tests()
    event_fabric.install_realtime_bus_adapter()
    realtime_bus.emit("adapter.test", actor={"name": "alice"},
                          payload={"x": 1})
    fabric_rows = event_fabric.replay()
    assert any(r.event_type == "adapter.test" for r in fabric_rows)
