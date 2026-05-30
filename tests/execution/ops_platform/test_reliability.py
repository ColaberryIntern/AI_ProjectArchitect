"""Phase 6C tests: reliability_monitor + self_healing + incidents."""

import pytest

from execution.ops_platform import (
    audit_log, cache_bus, controls, distributed_lock, incidents,
    policy_engine, reliability_monitor, runtime_queue, self_healing,
    shared_cache_backend, worker_coordination, workflow_runner,
)
from execution.ops_platform.capability_registry import CapabilityRegistry
from execution.ops_platform.plugin_loader import load_plugins
from execution.ops_platform.workflow_runner import RunRecord


@pytest.fixture
def registry(fake_plugin_root, tmp_path, monkeypatch):
    import execution.ops_platform.capability_registry as crm
    monkeypatch.setattr(crm, "_STATS_PATH", tmp_path / "registry_stats.json")
    monkeypatch.setattr(workflow_runner, "_RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(distributed_lock, "_LOCKS_DIR", tmp_path / "locks")
    monkeypatch.setattr(runtime_queue, "_QUEUE_DIR", tmp_path / "queue")
    monkeypatch.setattr(worker_coordination, "_WORKERS_DIR", tmp_path / "workers")
    monkeypatch.setattr(controls, "_CONTROLS_DIR", tmp_path / "controls")
    monkeypatch.setattr(audit_log, "_AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(incidents, "_INCIDENTS_DIR", tmp_path / "incidents")
    monkeypatch.setattr(policy_engine, "_POLICIES_DIR", tmp_path / "policies")
    monkeypatch.setattr(cache_bus, "_VERSION_DIR", tmp_path / "versions")
    shared_cache_backend.configure(shared_cache_backend.FileBackend(root=tmp_path / "versions"))
    cache_bus.reset_for_tests()
    reg = CapabilityRegistry(load_fn=lambda: load_plugins(root=fake_plugin_root))
    import execution.ops_platform.capability_registry as crm2
    monkeypatch.setattr(crm2, "_DEFAULT_REGISTRY", reg)
    yield reg
    shared_cache_backend.reset_for_tests()


# ── reliability_monitor ──────────────────────────────────────────────


def test_scan_returns_list(registry):
    findings = reliability_monitor.scan(registry=registry)
    assert isinstance(findings, list)


def test_rising_failure_rate_detected(registry):
    cap = registry.snapshot().capabilities[0]
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    # 10 prior succeeds
    # Prior window is (now-2h, now-1h]
    for i in range(10):
        ts = (now - timedelta(minutes=65 + i)).isoformat()
        workflow_runner._persist(RunRecord(
            run_id=f"p{i}", capability_id=cap["id"],
            started_at=ts, finished_at=ts, status="succeeded",
        ))
    # 10 recent fails
    for i in range(10):
        ts = (now - timedelta(minutes=i)).isoformat()
        workflow_runner._persist(RunRecord(
            run_id=f"r{i}", capability_id=cap["id"],
            started_at=ts, finished_at=ts, status="error", error_message="boom",
        ))
    findings = reliability_monitor.scan(registry=registry)
    assert any(f.kind == "rising_failure_rate" and f.target_id == cap["id"]
                for f in findings)


# ── self_healing ─────────────────────────────────────────────────────


def test_self_healing_run_once_returns_actions(registry, monkeypatch):
    # No findings → no actions
    actions = self_healing.run_once(registry=registry)
    assert isinstance(actions, list)


def test_self_healing_dead_worker_evicts(registry):
    import json
    w = worker_coordination.register()
    path = worker_coordination._WORKERS_DIR / f"{w.worker_id}.json"
    row = json.loads(path.read_text())
    row["last_heartbeat_at"] = "2020-01-01T00:00:00+00:00"
    path.write_text(json.dumps(row))
    actions = self_healing.run_once(registry=registry)
    # Either applied or gated — depends on env vars; the action must surface
    assert any(a.kind == "evict_dead_worker" for a in actions)


def test_quarantine_capability_requires_explicit_optin(registry, monkeypatch):
    monkeypatch.delenv("OPS_SELF_HEALING_ALLOWED", raising=False)
    cap = registry.snapshot().capabilities[0]
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    # Prior window is (now-2h, now-1h]
    for i in range(10):
        ts = (now - timedelta(minutes=65 + i)).isoformat()
        workflow_runner._persist(RunRecord(
            run_id=f"p{i}", capability_id=cap["id"],
            started_at=ts, finished_at=ts, status="succeeded",
        ))
    for i in range(10):
        ts = (now - timedelta(minutes=i)).isoformat()
        workflow_runner._persist(RunRecord(
            run_id=f"r{i}", capability_id=cap["id"],
            started_at=ts, finished_at=ts, status="error", error_message="boom",
        ))
    actions = self_healing.run_once(registry=registry)
    quarantine_actions = [a for a in actions if a.kind == "quarantine_capability"]
    assert quarantine_actions
    # Without opt-in, must be denied
    assert all(a.outcome == "denied" for a in quarantine_actions)


# ── incidents ────────────────────────────────────────────────────────


def test_open_and_transition_incident(registry):
    inc = incidents.open_incident(title="test inc", severity=3,
                                    detector="manual", actor="alice")
    assert inc.state == "open"
    moved = incidents.transition(inc.incident_id, to_state="mitigating",
                                    actor="alice")
    assert moved.state == "mitigating"


def test_incident_postmortem_drafted(registry):
    inc = incidents.open_incident(title="t", severity=2, detector="x")
    incidents.add_timeline_entry(inc.incident_id, note="started investigating",
                                    actor="alice")
    drafted = incidents.draft_postmortem(inc.incident_id)
    assert drafted.state == "postmortem_drafted"
    assert "# " in drafted.postmortem


def test_incident_correlation_id_threads_audit(registry):
    inc = incidents.open_incident(title="x", severity=2, detector="m")
    rows = audit_log.list_entries(correlation_id=inc.correlation_id)
    assert any(r["action"] == "incident.opened" for r in rows)
