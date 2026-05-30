"""Phase 6F tests: experiments + evaluation + per-version KPIs."""

import pytest

from execution.ops_platform import (
    audit_log, cache_bus, evaluation, experiments, workflow_runner,
)
from execution.ops_platform.capability_registry import CapabilityRegistry
from execution.ops_platform.plugin_loader import load_plugins
from execution.ops_platform.workflow_runner import RunRecord


@pytest.fixture
def registry(fake_plugin_root, tmp_path, monkeypatch):
    import execution.ops_platform.capability_registry as crm
    monkeypatch.setattr(crm, "_STATS_PATH", tmp_path / "registry_stats.json")
    monkeypatch.setattr(workflow_runner, "_RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(experiments, "_EXPERIMENTS_DIR", tmp_path / "experiments")
    monkeypatch.setattr(audit_log, "_AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(cache_bus, "_VERSION_DIR", tmp_path / "versions")
    cache_bus.reset_for_tests()
    return CapabilityRegistry(load_fn=lambda: load_plugins(root=fake_plugin_root))


def test_create_experiment_validates_weights(registry):
    with pytest.raises(ValueError):
        experiments.create_experiment(
            name="bad", capability_id=registry.snapshot().capabilities[0]["id"],
            arms=[{"arm_id": "a", "label": "A", "weight": 60}],
        )


def test_create_then_run(registry):
    cap = registry.snapshot().capabilities[0]
    exp = experiments.create_experiment(
        name="ab1", capability_id=cap["id"],
        arms=[
            {"arm_id": "control", "label": "Control", "weight": 50},
            {"arm_id": "treatment", "label": "Treatment", "weight": 50},
        ],
    )
    started = experiments.transition(exp.experiment_id, to_state="running")
    assert started.state == "running"


def test_assign_is_deterministic(registry):
    cap = registry.snapshot().capabilities[0]
    exp = experiments.create_experiment(
        name="det", capability_id=cap["id"],
        arms=[
            {"arm_id": "c", "label": "C", "weight": 50},
            {"arm_id": "t", "label": "T", "weight": 50},
        ],
    )
    experiments.transition(exp.experiment_id, to_state="running")
    a1 = experiments.assign(exp.experiment_id, session_id="bob")
    a2 = experiments.assign(exp.experiment_id, session_id="bob")
    assert a1["assignment"]["arm_id"] == a2["assignment"]["arm_id"]


def test_shadow_arm_excluded_from_assignment(registry):
    cap = registry.snapshot().capabilities[0]
    exp = experiments.create_experiment(
        name="shadow", capability_id=cap["id"],
        arms=[
            {"arm_id": "c", "label": "C", "weight": 100},
            {"arm_id": "s", "label": "Shadow", "weight": 0, "is_shadow": True},
        ],
    )
    experiments.transition(exp.experiment_id, to_state="running")
    a = experiments.assign(exp.experiment_id, session_id="x")
    assert a["assignment"]["arm_id"] == "c"
    assert a["shadow_arms"][0]["arm_id"] == "s"


def test_assignment_audit_record(registry):
    cap = registry.snapshot().capabilities[0]
    exp = experiments.create_experiment(
        name="ar", capability_id=cap["id"],
        arms=[{"arm_id": "c", "label": "C", "weight": 100}],
    )
    experiments.transition(exp.experiment_id, to_state="running")
    experiments.assign(exp.experiment_id, session_id="foo")
    rows = audit_log.list_entries(action="experiment.assigned")
    assert rows


def test_evaluate_returns_kpis(registry):
    cap = registry.snapshot().capabilities[0]
    exp = experiments.create_experiment(
        name="ev", capability_id=cap["id"],
        arms=[
            {"arm_id": "ctrl", "label": "Ctrl", "weight": 50,
             "capability_version_id": "v-ctrl"},
            {"arm_id": "treat", "label": "Treat", "weight": 50,
             "capability_version_id": "v-treat"},
        ],
    )
    experiments.transition(exp.experiment_id, to_state="running")
    # Seed runs tagged to arms
    for i in range(10):
        workflow_runner._persist(RunRecord(
            run_id=f"c{i}", capability_id=cap["id"],
            started_at="2026-05-26T10:00:00+00:00",
            finished_at="2026-05-26T10:00:01+00:00",
            status="succeeded", duration_ms=1000,
            inputs={"__capability_version_id": "v-ctrl"},
            response={"summary": "ok"},
        ))
    for i in range(10):
        workflow_runner._persist(RunRecord(
            run_id=f"t{i}", capability_id=cap["id"],
            started_at="2026-05-26T10:00:00+00:00",
            finished_at="2026-05-26T10:00:01+00:00",
            status="error",
            inputs={"__capability_version_id": "v-treat"},
        ))
    out = evaluation.evaluate_experiment(exp.experiment_id)
    assert "arm_kpis" in out
    assert out["control_arm_id"]
    # Treatment should show as significantly worse
    assert any(s["direction"] == "treatment_worse" for s in out["significance"])


def test_version_scorecard(registry):
    cap = registry.snapshot().capabilities[0]
    workflow_runner._persist(RunRecord(
        run_id="v1-1", capability_id=cap["id"],
        started_at="2026-05-26T10:00:00+00:00",
        finished_at="2026-05-26T10:00:01+00:00",
        status="succeeded", duration_ms=800,
        inputs={"__capability_version_id": "v-x"},
    ))
    sc = evaluation.version_scorecard(cap["id"], "v-x")
    assert sc["sample_size"] == 1
