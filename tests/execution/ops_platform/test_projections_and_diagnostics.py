"""Phase 9E/9G tests: projection_engine + coordination_diagnostics +
extended chaos + prometheus."""

import pytest

from execution.ops_platform import (
    cache_bus, chaos_engine, controls, coordination_diagnostics,
    distributed_lock, event_fabric, orchestration_engine,
    orchestration_runtime, projection_engine, prometheus_exporter,
    redis_backends, runtime_queue, worker_coordination,
)
from execution.ops_platform.capability_registry import CapabilityRegistry
from execution.ops_platform.plugin_loader import load_plugins


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(event_fabric, "_EVENTS_DIR", tmp_path / "fabric")
    monkeypatch.setattr(event_fabric, "_SEQUENCE_PATH", tmp_path / "seq.json")
    monkeypatch.setattr(projection_engine, "_PROJECTIONS_DIR", tmp_path / "projections")
    monkeypatch.setattr(orchestration_engine, "_ORCH_DIR", tmp_path / "orchestrations")
    monkeypatch.setattr(orchestration_runtime, "_CLAIMS_DIR", tmp_path / "claims")
    monkeypatch.setattr(distributed_lock, "_LOCKS_DIR", tmp_path / "locks")
    monkeypatch.setattr(chaos_engine, "_CHAOS_DIR", tmp_path / "chaos")
    monkeypatch.setattr(controls, "_CONTROLS_DIR", tmp_path / "controls")
    monkeypatch.setattr(runtime_queue, "_QUEUE_DIR", tmp_path / "queue")
    monkeypatch.setattr(worker_coordination, "_WORKERS_DIR", tmp_path / "workers")
    monkeypatch.setattr(cache_bus, "_VERSION_DIR", tmp_path / "versions")
    cache_bus.reset_for_tests()
    event_fabric.reset_for_tests()
    redis_backends._CLIENT = None
    controls._RATE_LIMIT_HITS.clear()
    projection_engine._REGISTRY.clear()
    yield
    projection_engine._REGISTRY.clear()


# ── projection_engine ────────────────────────────────────────────────


def test_register_default_projections():
    projection_engine.register_default_projections()
    names = [p["name"] for p in projection_engine.list_projections()]
    assert "incident_timeline" in names
    assert "active_alerts" in names
    assert "operator_activity" in names


def test_rebuild_unknown_raises():
    with pytest.raises(KeyError):
        projection_engine.rebuild("nope")


def test_rebuild_incident_timeline_deterministic():
    projection_engine.register_default_projections()
    event_fabric.emit("incident.opened", correlation_id="inc-1", actor_id="alice",
                          payload={"severity": 3})
    event_fabric.emit("incident.transitioned", correlation_id="inc-1",
                          actor_id="alice", payload={"to": "mitigating"})
    a = projection_engine.rebuild("incident_timeline")
    b = projection_engine.rebuild("incident_timeline")
    # Determinism: identical state for the same event log
    assert a["state"] == b["state"]
    assert "inc-1" in a["state"]
    assert len(a["state"]["inc-1"]) == 2


def test_rebuild_active_alerts_resolves_correctly():
    projection_engine.register_default_projections()
    event_fabric.emit("alert.opened", payload={"alert_id": "a1"})
    event_fabric.emit("alert.opened", payload={"alert_id": "a2"})
    event_fabric.emit("alert.resolved", payload={"alert_id": "a1"})
    result = projection_engine.rebuild("active_alerts")
    assert "a2" in result["state"]
    assert "a1" not in result["state"]


def test_rebuild_operator_activity_counts():
    projection_engine.register_default_projections()
    event_fabric.emit("x", actor_id="alice")
    event_fabric.emit("y", actor_id="alice")
    event_fabric.emit("z", actor_id="bob")
    result = projection_engine.rebuild("operator_activity")
    assert result["state"]["alice"]["events"] == 2
    assert result["state"]["bob"]["events"] == 1


def test_compare_with_latest_detects_match():
    projection_engine.register_default_projections()
    event_fabric.emit("x", actor_id="alice")
    projection_engine.rebuild("operator_activity")
    # Same events → states match
    compare = projection_engine.compare_with_latest("operator_activity")
    assert compare["states_match"]


def test_rebuild_persists_latest():
    projection_engine.register_default_projections()
    event_fabric.emit("x", actor_id="alice")
    result = projection_engine.rebuild("operator_activity")
    cached = projection_engine.latest("operator_activity")
    assert cached["state"] == result["state"]


# ── coordination_diagnostics ─────────────────────────────────────────


def test_coordination_topology_returns_dict():
    topo = coordination_diagnostics.coordination_topology()
    assert "redis_client_wired" in topo
    assert "event_fabric" in topo
    assert "orchestration_runtime" in topo


def test_lock_inspector_with_active_lock():
    distributed_lock.acquire("inspect.test", owner_id="me", lease_seconds=60)
    rows = coordination_diagnostics.lock_inspector(lock_names=["inspect.test"])
    assert any(r["lock_name"] == "inspect.test" for r in rows)


def test_replay_backlog_returns_counts():
    backlog = coordination_diagnostics.replay_backlog()
    assert "queue_total" in backlog
    assert "active_orchestrations" in backlog


def test_cluster_health_returns_ready_or_issues():
    h = coordination_diagnostics.cluster_health()
    assert "ready" in h
    assert "issues" in h
    # Without Redis, the issue list mentions it but still ready (advisory)
    assert "topology" in h
    assert "backlog" in h


def test_stream_lag_report_without_redis():
    out = coordination_diagnostics.stream_lag_report()
    assert out["scope"] == "no-redis"


# ── chaos drills (Phase 9F extensions) ───────────────────────────────


def test_split_brain_chaos_creates_two_stale_claims():
    inj = chaos_engine.inject(kind="split_brain", target_id="orch-test",
                                  duration_seconds=60, actor="chaos")
    assert inj.state == "active"
    expired = orchestration_runtime.reclaim_expired()
    # Both stale claims should be reclaimable
    assert any(c.orchestration_id == "orch-test" for c in expired)


def test_duplicate_delivery_chaos_emits_twice():
    initial = event_fabric.replay(event_types=["chaos.duplicate_delivery"])
    chaos_engine.inject(kind="duplicate_delivery", target_id="x",
                            duration_seconds=10, actor="chaos")
    after = event_fabric.replay(event_types=["chaos.duplicate_delivery"])
    assert len(after) >= len(initial) + 2


def test_delayed_replay_chaos_emits_with_explicit_consistency():
    chaos_engine.inject(kind="delayed_replay", target_id="x",
                            duration_seconds=10, actor="chaos")
    rows = event_fabric.replay(event_types=["chaos.delayed_replay"])
    assert rows
    assert rows[0].consistency_scope == "best-effort"


def test_lock_lease_expired_chaos_forces_expiry():
    distributed_lock.acquire("expire.test", owner_id="me", lease_seconds=300)
    chaos_engine.inject(kind="lock_lease_expired", target_id="expire.test",
                            duration_seconds=10, actor="chaos")
    # Lock should now register as expired (None on is_held)
    assert distributed_lock.is_held("expire.test") is None


def test_ws_partition_chaos_clears_subscribers():
    from execution.ops_platform import realtime_bus
    sub_id, q, notify = realtime_bus.subscribe(event_types=["x"])
    chaos_engine.inject(kind="ws_partition", target_id="sales",
                            duration_seconds=10, actor="chaos")
    assert len(realtime_bus._SUBSCRIBERS) == 0


# ── prometheus_exporter additions ────────────────────────────────────


def test_prometheus_includes_coordination_metrics():
    text = prometheus_exporter.render()
    assert "ops_coordination_scope" in text
    assert "ops_active_step_claims" in text
    assert "ops_redis_client_wired" in text
    assert "ops_replay_backlog_queue" in text
    assert "ops_orphan_orchestrations" in text
