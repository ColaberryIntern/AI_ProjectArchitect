"""Phase 8C, 8D, 8E tests: knowledge_graph + chaos_engine + orchestration_engine."""

import pytest

from execution.ops_platform import (
    approvals, audit_log, cache_bus, chaos_engine, controls,
    distributed_lock, incidents, knowledge_graph, orchestration_engine,
    realtime_bus, runtime_queue, scheduler, worker_coordination,
    workflow_runner,
)
from execution.ops_platform.capability_registry import CapabilityRegistry
from execution.ops_platform.plugin_loader import load_plugins


@pytest.fixture
def registry(fake_plugin_root, tmp_path, monkeypatch):
    import execution.ops_platform.capability_registry as crm
    monkeypatch.setattr(crm, "_STATS_PATH", tmp_path / "registry_stats.json")
    monkeypatch.setattr(workflow_runner, "_RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(incidents, "_INCIDENTS_DIR", tmp_path / "incidents")
    monkeypatch.setattr(controls, "_CONTROLS_DIR", tmp_path / "controls")
    monkeypatch.setattr(scheduler, "_SCHEDULES_DIR", tmp_path / "schedules")
    monkeypatch.setattr(distributed_lock, "_LOCKS_DIR", tmp_path / "locks")
    monkeypatch.setattr(runtime_queue, "_QUEUE_DIR", tmp_path / "queue")
    monkeypatch.setattr(chaos_engine, "_CHAOS_DIR", tmp_path / "chaos")
    monkeypatch.setattr(orchestration_engine, "_ORCH_DIR", tmp_path / "orchestrations")
    monkeypatch.setattr(approvals, "_APPROVALS_DIR", tmp_path / "approvals")
    monkeypatch.setattr(knowledge_graph, "_GRAPH_DIR", tmp_path / "kg")
    monkeypatch.setattr(audit_log, "_AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(realtime_bus, "_EVENTS_DIR", tmp_path / "events")
    monkeypatch.setattr(realtime_bus, "_SEQUENCE_PATH", tmp_path / "sequence.json")
    monkeypatch.setattr(cache_bus, "_VERSION_DIR", tmp_path / "versions")
    monkeypatch.setattr(worker_coordination, "_WORKERS_DIR", tmp_path / "workers")
    controls._RATE_LIMIT_HITS.clear()
    cache_bus.reset_for_tests()
    realtime_bus.reset_for_tests()
    reg = CapabilityRegistry(load_fn=lambda: load_plugins(root=fake_plugin_root))
    import execution.ops_platform.capability_registry as crm2
    monkeypatch.setattr(crm2, "_DEFAULT_REGISTRY", reg)
    return reg


# ── knowledge_graph ──────────────────────────────────────────────────


def test_build_graph_returns_nodes_and_edges(registry):
    g = knowledge_graph.build_graph(lookback_days=7, registry=registry)
    assert len(g.nodes) >= 1


def test_related_includes_anchor(registry):
    cap = registry.snapshot().capabilities[0]
    knowledge_graph.build_graph(registry=registry)
    out = knowledge_graph.related(f"capability:{cap['id']}", max_depth=1,
                                       registry=registry)
    assert out["anchor"] == f"capability:{cap['id']}"
    assert any(n.get("node_id") == f"capability:{cap['id']}" for n in out["nodes"])


def test_causal_replay_requires_incident_or_correlation(registry):
    with pytest.raises(ValueError):
        knowledge_graph.causal_replay()


def test_causal_replay_finds_audit_timeline(registry):
    inc = incidents.open_incident(title="test", severity=3, detector="m",
                                      impacted_capabilities=[
                                          registry.snapshot().capabilities[0]["id"]
                                      ])
    incidents.add_timeline_entry(inc.incident_id, note="started")
    out = knowledge_graph.causal_replay(incident_id=inc.incident_id)
    assert "timeline" in out
    assert "root_cause_candidates" in out


# ── chaos_engine ─────────────────────────────────────────────────────


def test_inject_queue_stall_freezes_target(registry):
    cap = registry.snapshot().capabilities[0]
    inj = chaos_engine.inject(kind="queue_stall", target_id=cap["id"],
                                  duration_seconds=300, actor="chaos")
    assert inj.state == "active"
    assert controls.is_blocked(cap["id"])


def test_revert_restores_state(registry):
    cap = registry.snapshot().capabilities[0]
    inj = chaos_engine.inject(kind="queue_stall", target_id=cap["id"],
                                  duration_seconds=300, actor="chaos")
    reverted = chaos_engine.revert(inj.injection_id, actor="chaos")
    assert reverted.state == "reverted"
    assert controls.is_blocked(cap["id"]) is None


def test_lock_starvation_holds_then_releases(registry):
    inj = chaos_engine.inject(kind="lock_starvation",
                                  target_id="chaos.test.lock",
                                  duration_seconds=60, actor="chaos")
    assert distributed_lock.is_held("chaos.test.lock") is not None
    chaos_engine.revert(inj.injection_id, actor="chaos")
    assert distributed_lock.is_held("chaos.test.lock") is None


def test_unknown_kind_raises(registry):
    with pytest.raises(ValueError):
        chaos_engine.inject(kind="bogus", target_id="x")


def test_chaos_audit_carries_correlation_id(registry):
    cap = registry.snapshot().capabilities[0]
    inj = chaos_engine.inject(kind="queue_stall", target_id=cap["id"],
                                  duration_seconds=60, actor="chaos",
                                  reason="drill")
    rows = audit_log.list_entries(correlation_id=inj.correlation_id)
    assert any(r["action"] == "chaos.injected" for r in rows)


def test_mttr_returns_dict(registry):
    out = chaos_engine.measure_mttr()
    assert "mttr_seconds" in out
    assert "samples" in out


# ── orchestration_engine ─────────────────────────────────────────────


def test_create_requires_steps(registry):
    with pytest.raises(ValueError):
        orchestration_engine.create_orchestration(name="empty", steps=[])


def test_create_advances_and_persists(registry):
    o = orchestration_engine.create_orchestration(
        name="t",
        steps=[{"step_id": "wait", "kind": "wait", "wait_seconds": 0}],
    )
    assert o.orchestration_id
    assert orchestration_engine.get(o.orchestration_id) is not None


def test_pause_then_resume(registry):
    o = orchestration_engine.create_orchestration(
        name="p",
        steps=[
            {"step_id": "wait1", "kind": "wait", "wait_seconds": 60},
            {"step_id": "wait2", "kind": "wait", "wait_seconds": 60},
        ],
    )
    paused = orchestration_engine.pause(o.orchestration_id, actor="alice")
    assert paused.state == "paused"
    resumed = orchestration_engine.resume(o.orchestration_id, actor="alice")
    assert resumed.state in ("running", "completed")


def test_rewind_resets_steps(registry):
    o = orchestration_engine.create_orchestration(
        name="rw",
        steps=[
            {"step_id": "s1", "kind": "wait", "wait_seconds": 0},
            {"step_id": "s2", "kind": "wait", "wait_seconds": 0},
        ],
    )
    rewound = orchestration_engine.rewind(o.orchestration_id, to_step_id="s1",
                                              actor="alice")
    assert rewound.current_step_index == 0


def test_approval_gate_creates_approval(registry):
    o = orchestration_engine.create_orchestration(
        name="ag",
        steps=[{"step_id": "gate", "kind": "approval_gate",
                  "approval_action": "deploy"}],
    )
    assert o.state == "awaiting_approval"
    record = o.step_records[0]
    assert record["approval_request_id"]
