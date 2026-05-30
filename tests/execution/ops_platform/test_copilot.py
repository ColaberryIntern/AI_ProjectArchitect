"""Phase 7B tests: copilot + recommendations v2."""

import pytest

from execution.ops_platform import (
    approvals, audit_log, cache_bus, capability_versions, controls, copilot,
    feedback_store, incidents, reliability_monitor, reputation_scorer,
    workflow_runner, workspaces,
)
from execution.ops_platform.capability_registry import CapabilityRegistry
from execution.ops_platform.plugin_loader import load_plugins
from execution.ops_platform.workflow_runner import RunRecord


@pytest.fixture
def registry(fake_plugin_root, tmp_path, monkeypatch):
    import execution.ops_platform.capability_registry as crm
    monkeypatch.setattr(crm, "_STATS_PATH", tmp_path / "registry_stats.json")
    monkeypatch.setattr(workflow_runner, "_RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(audit_log, "_AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(approvals, "_APPROVALS_DIR", tmp_path / "approvals")
    monkeypatch.setattr(incidents, "_INCIDENTS_DIR", tmp_path / "incidents")
    monkeypatch.setattr(controls, "_CONTROLS_DIR", tmp_path / "controls")
    monkeypatch.setattr(workspaces, "_WORKSPACES_DIR", tmp_path / "workspaces")
    monkeypatch.setattr(capability_versions, "_VERSIONS_DIR", tmp_path / "cap_versions")
    monkeypatch.setattr(feedback_store, "_FEEDBACK_DIR", tmp_path / "feedback")
    monkeypatch.setattr(feedback_store, "_INDEX_PATH", tmp_path / "feedback" / "_index.json")
    monkeypatch.setattr(reputation_scorer, "_SCORE_DIR", tmp_path / "reputation")
    monkeypatch.setattr(reputation_scorer, "_HISTORY_DIR", tmp_path / "reputation_history")
    monkeypatch.setattr(cache_bus, "_VERSION_DIR", tmp_path / "versions")
    controls._RATE_LIMIT_HITS.clear()
    cache_bus.reset_for_tests()
    reg = CapabilityRegistry(load_fn=lambda: load_plugins(root=fake_plugin_root))
    import execution.ops_platform.capability_registry as crm2
    monkeypatch.setattr(crm2, "_DEFAULT_REGISTRY", reg)
    return reg


def test_unknown_question_returns_unknown_intent(registry):
    a = copilot.ask("Sing me a song about kittens")
    assert a.intent == "unknown"
    assert not a.sufficient_evidence


def test_quarantine_why_returns_evidence(registry):
    cap = registry.snapshot().capabilities[0]
    controls.quarantine(cap["id"], actor="alice", reason="suspect drift")
    a = copilot.ask(f"Why was {cap['id']} quarantined?", capability_id=cap["id"])
    assert a.intent == "quarantine_why"
    assert "quarantine" in a.summary.lower() or "quarantined" in a.summary.lower()
    assert a.evidence_refs


def test_blocked_approvals_summarizes_count(registry):
    approvals.request_approval(action="x", entity_type="t", entity_id="e1",
                                  requested_by="alice")
    a = copilot.ask("what approvals are blocked?")
    assert a.intent == "blocked_approvals"
    assert "1" in a.summary or "block" in a.summary.lower()


def test_failure_summary_finds_rising_failure(registry):
    cap = registry.snapshot().capabilities[0]
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
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
    a = copilot.ask("Why did things fail recently?")
    assert a.intent == "failure_summary"
    assert a.confidence > 0


def test_recommendations_v2_surfaces_rollback_on_routing_degradation(registry):
    cap = registry.snapshot().capabilities[0]
    # Seed routing degradation via mixed-version runs
    v_approved = capability_versions.register_version(
        cap["id"], semver="1.0.0", changelog="x", created_by="alice", registry=registry,
    )
    capability_versions.promote(v_approved.version_id, target_status="approved", approver="alice")
    v_exp = capability_versions.register_version(
        cap["id"], semver="2.0.0", changelog="exp", created_by="alice", registry=registry,
    )
    capability_versions.promote(v_exp.version_id, target_status="experimental",
                                  approver="alice", rollout_percentage=50.0)
    for i in range(10):
        workflow_runner._persist(RunRecord(
            run_id=f"a{i}", capability_id=cap["id"],
            started_at="2026-05-26T10:00:00+00:00",
            finished_at="2026-05-26T10:00:01+00:00",
            status="succeeded",
            inputs={"__capability_version_id": v_approved.version_id},
        ))
    for i in range(10):
        workflow_runner._persist(RunRecord(
            run_id=f"e{i}", capability_id=cap["id"],
            started_at="2026-05-26T10:00:00+00:00",
            finished_at="2026-05-26T10:00:01+00:00",
            status="error",
            inputs={"__capability_version_id": v_exp.version_id},
        ))
    recs = copilot.operational_recommendations(registry=registry)
    assert any(r.kind == "rollback" for r in recs)


def test_recommendations_v2_surfaces_escalate_on_old_pending_approval(registry, monkeypatch):
    import json
    from datetime import datetime, timedelta, timezone
    req = approvals.request_approval(action="x", entity_type="t", entity_id="e",
                                        requested_by="alice")
    # Backdate the approval
    path = approvals._APPROVALS_DIR / f"{req.request_id}.json"
    row = json.loads(path.read_text())
    row["created_at"] = (datetime.now(timezone.utc) - timedelta(hours=20)).isoformat()
    path.write_text(json.dumps(row))
    recs = copilot.operational_recommendations(registry=registry)
    assert any(r.kind == "escalate_approval" for r in recs)
