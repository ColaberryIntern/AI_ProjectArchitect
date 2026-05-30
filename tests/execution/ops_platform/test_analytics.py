"""Tests for execution/ops_platform/analytics.py"""

import pytest

from execution.ops_platform import (
    analytics,
    feedback_store,
    pipeline_engine,
    reputation_scorer,
    training_agent,
    workflow_runner,
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
    monkeypatch.setattr(pipeline_engine, "_PIPELINE_RUNS_DIR", tmp_path / "pipeline_runs")
    monkeypatch.setattr(training_agent, "_TRAINING_DIR", tmp_path / "training")
    return CapabilityRegistry(load_fn=lambda: load_plugins(root=fake_plugin_root))


def test_top_capabilities_by_usage_empty(registry):
    rows = analytics.top_capabilities(by="usage", top_n=5, registry=registry)
    assert len(rows) <= 5


def test_department_usage_includes_known_departments(registry):
    rows = analytics.department_usage(registry=registry)
    depts = [r["department"] for r in rows]
    assert "Sales" in depts


def test_automation_roi_aggregates_minutes(registry):
    cap = registry.snapshot().capabilities[0]
    workflow_runner._persist(RunRecord(
        run_id="x1", capability_id=cap["id"],
        started_at="2026-05-26T10:00:00+00:00", finished_at="2026-05-26T10:00:01+00:00",
        status="succeeded",
    ))
    workflow_runner._persist(RunRecord(
        run_id="x2", capability_id=cap["id"],
        started_at="2026-05-26T10:01:00+00:00", finished_at="2026-05-26T10:01:01+00:00",
        status="succeeded",
    ))
    roi = analytics.automation_roi(registry=registry)
    assert roi["total_minutes_saved"] == 20  # 10 minutes_per_run * 2 successful runs
    assert roi["estimated_dollars_saved"] > 0


def test_bottlenecks_flag_failing_capability(registry):
    cap = registry.snapshot().capabilities[0]
    for i in range(8):
        workflow_runner._persist(RunRecord(
            run_id=f"f{i}", capability_id=cap["id"],
            started_at="2026-05-26T10:00:00+00:00", finished_at="2026-05-26T10:00:01+00:00",
            status="error",
        ))
    rows = analytics.bottlenecks(min_runs=5, registry=registry)
    assert any(r["capability_id"] == cap["id"] for r in rows)


def test_training_gaps_includes_popular_capability_without_walkthrough(registry):
    cap = registry.snapshot().capabilities[0]
    for _ in range(6):
        registry.record_usage(cap["id"])
    gaps = analytics.training_gaps(min_usage=5, registry=registry)
    assert any(g["capability_id"] == cap["id"] for g in gaps)


def test_feedback_pulse_handles_no_records():
    pulse = analytics.feedback_pulse()
    assert "last_7d_average" in pulse


def test_executive_summary_renders_with_zero_data(registry):
    summary = analytics.executive_summary(registry=registry)
    assert summary["capability_count"] >= 1
    assert "total_hours_saved" in summary
