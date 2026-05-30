"""Tests for execution/ops_platform/trust_engine.py"""

import pytest

from execution.ops_platform import (
    audit_log, cache_bus, capability_versions, feedback_store,
    reputation_scorer, trust_engine, workflow_runner,
)
from execution.ops_platform.capability_registry import CapabilityRegistry
from execution.ops_platform.plugin_loader import load_plugins
from execution.ops_platform.workflow_runner import RunRecord


@pytest.fixture
def registry(fake_plugin_root, tmp_path, monkeypatch):
    import execution.ops_platform.capability_registry as crm
    monkeypatch.setattr(crm, "_STATS_PATH", tmp_path / "registry_stats.json")
    monkeypatch.setattr(workflow_runner, "_RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(feedback_store, "_FEEDBACK_DIR", tmp_path / "feedback")
    monkeypatch.setattr(feedback_store, "_INDEX_PATH", tmp_path / "feedback" / "_index.json")
    monkeypatch.setattr(reputation_scorer, "_SCORE_DIR", tmp_path / "reputation")
    monkeypatch.setattr(reputation_scorer, "_HISTORY_DIR", tmp_path / "reputation_history")
    monkeypatch.setattr(capability_versions, "_VERSIONS_DIR", tmp_path / "cap_versions")
    monkeypatch.setattr(audit_log, "_AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(cache_bus, "_VERSION_DIR", tmp_path / "versions")
    cache_bus.reset_for_tests()
    return CapabilityRegistry(load_fn=lambda: load_plugins(root=fake_plugin_root))


def test_unknown_capability_returns_critical(registry):
    profile = trust_engine.score("does-not-exist", registry=registry)
    assert profile.risk_level == "CRITICAL"
    assert profile.deployment_recommendation == "DO_NOT_DEPLOY"


def test_no_runs_yet_returns_low_confidence(registry):
    cap_id = registry.snapshot().capabilities[0]["id"]
    profile = trust_engine.score(cap_id, registry=registry, record_audit=False)
    assert profile.confidence == 0.0
    assert "no run history yet" in profile.blocking_issues


def test_high_reliability_yields_safe_recommendation(registry):
    cap_id = registry.snapshot().capabilities[0]["id"]
    for i in range(60):
        workflow_runner._persist(RunRecord(
            run_id=f"ok{i}", capability_id=cap_id,
            started_at="2026-05-26T10:00:00+00:00",
            finished_at="2026-05-26T10:00:01+00:00",
            status="succeeded", duration_ms=1000,
        ))
    feedback_store.submit_feedback({
        "capability_id": cap_id,
        "submitter": {"name": "alice", "email": "alice@x.com", "department": "Sales"},
        "ratings": {"usefulness": 5, "accuracy": 5, "time_savings": 5, "reliability": 5},
    }, registry=registry)
    feedback_store.submit_feedback({
        "capability_id": cap_id,
        "submitter": {"name": "bob", "email": "bob@x.com", "department": "Sales"},
        "ratings": {"usefulness": 5, "accuracy": 5, "time_savings": 5, "reliability": 5},
    }, registry=registry)
    reputation_scorer.score_capability(cap_id, registry=registry, persist=True)
    profile = trust_engine.score(cap_id, registry=registry, record_audit=False)
    assert profile.deployment_recommendation in ("SAFE_FOR_PRODUCTION", "LIMITED_ROLLOUT")
    assert profile.risk_level in ("LOW", "MODERATE")


def test_low_reliability_yields_high_risk(registry):
    cap_id = registry.snapshot().capabilities[0]["id"]
    for i in range(20):
        workflow_runner._persist(RunRecord(
            run_id=f"bad{i}", capability_id=cap_id,
            started_at="2026-05-26T10:00:00+00:00",
            finished_at="2026-05-26T10:00:01+00:00",
            status="error", error_message="boom",
        ))
    profile = trust_engine.score(cap_id, registry=registry, record_audit=False)
    assert profile.risk_level in ("HIGH", "CRITICAL")


def test_trust_score_components_complete(registry):
    cap_id = registry.snapshot().capabilities[0]["id"]
    profile = trust_engine.score(cap_id, registry=registry, record_audit=False)
    expected = {
        "reliability", "audit_cleanliness", "rollback_stability",
        "prompt_stability", "execution_consistency", "feedback_quality",
        "operator_approval", "version_maturity", "rollout_success",
    }
    assert expected.issubset(set(profile.components.keys()))


def test_trust_score_deterministic_same_inputs(registry):
    cap_id = registry.snapshot().capabilities[0]["id"]
    p1 = trust_engine.score(cap_id, registry=registry, record_audit=False)
    p2 = trust_engine.score(cap_id, registry=registry, record_audit=False)
    assert p1.trust_score == p2.trust_score


def test_trust_report_returns_sorted(registry):
    rows = trust_engine.trust_report(registry=registry)
    scores = [r["trust_score"] for r in rows]
    assert scores == sorted(scores, reverse=True)


def test_audit_emitted_when_record_audit_true(registry):
    cap_id = registry.snapshot().capabilities[0]["id"]
    trust_engine.score(cap_id, registry=registry, record_audit=True)
    rows = audit_log.list_entries(action="trust.calculated")
    assert rows
