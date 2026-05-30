"""Tests for execution/ops_platform/workflow_discovery.py"""

import pytest

from execution.ops_platform import pipeline_engine, workflow_discovery, workflow_runner
from execution.ops_platform.capability_registry import CapabilityRegistry
from execution.ops_platform.plugin_loader import load_plugins
from execution.ops_platform.workflow_runner import RunRecord


@pytest.fixture
def registry(fake_plugin_root, tmp_path, monkeypatch):
    import execution.ops_platform.capability_registry as crm
    monkeypatch.setattr(crm, "_STATS_PATH", tmp_path / "registry_stats.json")
    monkeypatch.setattr(workflow_runner, "_RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(pipeline_engine, "_PIPELINES_DIR", tmp_path / "pipelines")
    monkeypatch.setattr(pipeline_engine, "_PLUGIN_PIPELINES_DIR", tmp_path / "plugin_pipelines")
    monkeypatch.setattr(workflow_discovery, "_DISCOVERY_DIR", tmp_path / "discoveries")
    return CapabilityRegistry(load_fn=lambda: load_plugins(root=fake_plugin_root))


def _run(rid, cap_id, ts, initiator="alice"):
    workflow_runner._persist(RunRecord(
        run_id=rid, capability_id=cap_id,
        started_at=ts, finished_at=ts, status="succeeded",
        inputs={"__initiator": initiator},
    ))


def test_no_runs_returns_empty(registry):
    assert workflow_discovery.discover_patterns(registry=registry) == []


def test_finds_repeated_pair(registry):
    caps = registry.snapshot().capabilities
    a, b = caps[0]["id"], caps[1]["id"]
    # alice runs A->B three times
    _run("r1", a, "2026-05-26T10:00:00+00:00")
    _run("r2", b, "2026-05-26T10:01:00+00:00")
    _run("r3", a, "2026-05-26T11:00:00+00:00")
    _run("r4", b, "2026-05-26T11:01:00+00:00")
    _run("r5", a, "2026-05-26T12:00:00+00:00")
    _run("r6", b, "2026-05-26T12:01:00+00:00")
    patterns = workflow_discovery.discover_patterns(
        window=2, min_occurrences=2, registry=registry
    )
    found = [tuple(p.sequence) for p in patterns]
    assert (a, b) in found


def test_aa_sequences_filtered_out(registry):
    caps = registry.snapshot().capabilities
    a = caps[0]["id"]
    for i in range(5):
        _run(f"r{i}", a, f"2026-05-26T1{i}:00:00+00:00")
    patterns = workflow_discovery.discover_patterns(
        window=2, min_occurrences=2, registry=registry
    )
    assert all(tuple(p.sequence) != (a, a) for p in patterns)


def test_existing_pipeline_not_reproposed(registry, tmp_path):
    caps = registry.snapshot().capabilities
    a, b = caps[0]["id"], caps[1]["id"]
    # save a pipeline that already covers A->B
    user_dir = pipeline_engine._PIPELINES_DIR
    user_dir.mkdir(parents=True, exist_ok=True)
    import json
    (user_dir / "existing.json").write_text(json.dumps({
        "pipeline_id": "existing",
        "name": "existing",
        "version": "1.0.0",
        "created_by": {"name": "x"},
        "steps": [
            {"step_id": "s1", "capability_id": a},
            {"step_id": "s2", "capability_id": b},
        ],
    }))
    for i in range(3):
        _run(f"r{i*2}", a, f"2026-05-26T1{i}:00:00+00:00")
        _run(f"r{i*2+1}", b, f"2026-05-26T1{i}:01:00+00:00")
    patterns = workflow_discovery.discover_patterns(
        window=2, min_occurrences=2, registry=registry
    )
    assert all(tuple(p.sequence) != (a, b) for p in patterns)


def test_snapshot_roundtrips(registry):
    patterns = []
    target = workflow_discovery.snapshot_discoveries(patterns)
    assert target.exists()
    snap = workflow_discovery.latest_snapshot()
    assert snap is not None
    assert "patterns" in snap


def test_draft_pipeline_shape(registry):
    caps = registry.snapshot().capabilities
    a, b = caps[0]["id"], caps[1]["id"]
    for i in range(3):
        _run(f"r{i*2}", a, f"2026-05-26T1{i}:00:00+00:00")
        _run(f"r{i*2+1}", b, f"2026-05-26T1{i}:01:00+00:00")
    patterns = workflow_discovery.discover_patterns(
        window=2, min_occurrences=2, registry=registry
    )
    if patterns:
        draft = patterns[0].draft_pipeline
        assert "pipeline_id" in draft
        assert "steps" in draft
        assert len(draft["steps"]) == 2
        assert draft["steps"][1]["depends_on"] == ["step_1"]
