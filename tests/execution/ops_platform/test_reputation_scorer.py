"""Tests for execution/ops_platform/reputation_scorer.py"""

import json

import pytest

from execution.ops_platform import feedback_store, reputation_scorer, workflow_runner
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
    return CapabilityRegistry(load_fn=lambda: load_plugins(root=fake_plugin_root))


def _write_run(capability_id: str, *, status: str = "succeeded", known_issues=None):
    run = RunRecord(
        run_id=f"run-{capability_id}-{status}",
        capability_id=capability_id,
        started_at="2026-05-26T10:00:00+00:00",
        finished_at="2026-05-26T10:00:01+00:00",
        status=status,
        inputs={},
        response={"known_issues": known_issues or []} if status == "succeeded" else None,
    )
    workflow_runner._persist(run)


def test_score_zero_signal_capability(registry):
    cap = registry.snapshot().capabilities[0]
    score = reputation_scorer.score_capability(cap["id"], registry=registry, persist=False)
    assert score.reputation_score == 0.0
    assert score.usage_score == 0.0


def test_successful_runs_lift_usage_and_reliability(registry):
    cap = registry.snapshot().capabilities[0]
    for i in range(5):
        rec = RunRecord(
            run_id=f"r{i}", capability_id=cap["id"],
            started_at="2026-05-26T10:00:00+00:00", finished_at="2026-05-26T10:00:01+00:00",
            status="succeeded", response={"known_issues": []},
        )
        workflow_runner._persist(rec)
    score = reputation_scorer.score_capability(cap["id"], registry=registry, persist=False)
    assert score.reliability_score == 100.0
    assert score.usage_score > 0
    assert score.signal_counts["succeeded_runs"] == 5


def test_feedback_drives_business_impact_score(registry):
    cap = registry.snapshot().capabilities[0]
    feedback_store.submit_feedback({
        "capability_id": cap["id"],
        "submitter": {"name": "alice", "email": "alice@x.com", "department": "Sales"},
        "ratings": {"usefulness": 5, "accuracy": 5, "time_savings": 5, "reliability": 5},
        "operational_notes": {"how_used": "Saves hours."},
    }, registry=registry)
    score = reputation_scorer.score_capability(cap["id"], registry=registry, persist=False)
    assert score.business_impact_score == 100.0
    assert score.feedback_score == 100.0


def test_verification_score_proxy(registry):
    cap = registry.snapshot().capabilities[0]
    _write_run(cap["id"], status="succeeded", known_issues=[])
    _write_run(cap["id"], status="succeeded", known_issues=[{"severity": "blocker"}])
    _write_run(cap["id"], status="succeeded", known_issues=[{"severity": "high"}])
    score = reputation_scorer.score_capability(cap["id"], registry=registry, persist=False)
    # 1 green, 1 yellow, 1 red -> (1+0.5+0)/3 * 100 = 50
    assert 40 < score.verification_score < 60


def test_persisted_score_roundtrips(registry):
    cap = registry.snapshot().capabilities[0]
    score = reputation_scorer.score_capability(cap["id"], registry=registry, persist=True)
    loaded = reputation_scorer.load_score(cap["id"])
    assert loaded is not None
    assert loaded["capability_id"] == cap["id"]
    assert loaded["reputation_score"] == score.reputation_score
