"""Tests for execution/ops_platform/pipeline_engine.py"""

import json
from pathlib import Path

import pytest

from execution.llm_client import LLMResponse
from execution.ops_platform import pipeline_engine, workflow_runner
from execution.ops_platform.capability_registry import CapabilityRegistry
from execution.ops_platform.plugin_loader import load_plugins


@pytest.fixture
def registry(fake_plugin_root, tmp_path, monkeypatch):
    import execution.ops_platform.capability_registry as crm
    monkeypatch.setattr(crm, "_STATS_PATH", tmp_path / "registry_stats.json")
    monkeypatch.setattr(workflow_runner, "_RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(pipeline_engine, "_PIPELINES_DIR", tmp_path / "pipelines")
    monkeypatch.setattr(pipeline_engine, "_PLUGIN_PIPELINES_DIR", tmp_path / "plugin_pipelines")
    monkeypatch.setattr(pipeline_engine, "_PIPELINE_RUNS_DIR", tmp_path / "pipeline_runs")
    return CapabilityRegistry(load_fn=lambda: load_plugins(root=fake_plugin_root))


@pytest.fixture
def two_step_pipeline(tmp_path, registry):
    manifest = {
        "pipeline_id": "test_pipeline",
        "name": "Test Pipeline",
        "version": "1.0.0",
        "created_by": {"name": "tests"},
        "execution_strategy": "sequential",
        "inputs": [{"name": "text", "type": "text", "required": True}],
        "steps": [
            {
                "step_id": "first",
                "capability_id": "test_summary",
                "depends_on": [],
                "on_failure": "abort",
                "input_bindings": {"text": "$pipeline.text"},
            },
            {
                "step_id": "second",
                "capability_id": "test_compose",
                "depends_on": ["first"],
                "on_failure": "abort",
                "input_bindings": {"text": "$step.first.summary"},
            },
        ],
    }
    user_dir = pipeline_engine._PIPELINES_DIR
    user_dir.mkdir(parents=True, exist_ok=True)
    (user_dir / "test_pipeline.json").write_text(json.dumps(manifest))
    return manifest


def _fake_llm(monkeypatch, payload):
    from execution import llm_client
    monkeypatch.setattr(llm_client, "is_available", lambda: True)
    def fake_chat(**kw):
        return LLMResponse(content=json.dumps(payload), model="t",
                           usage={"prompt_tokens": 1, "completion_tokens": 1}, stop_reason="stop")
    monkeypatch.setattr(llm_client, "chat", fake_chat)


def test_list_pipelines_finds_user_created(registry, two_step_pipeline):
    pipelines = pipeline_engine.list_pipelines()
    ids = [p["pipeline_id"] for p in pipelines]
    assert "test_pipeline" in ids


def test_load_unknown_pipeline_returns_none(registry):
    assert pipeline_engine.load_pipeline("nope") is None


def test_run_pipeline_propagates_outputs(registry, two_step_pipeline, monkeypatch, make_response):
    _fake_llm(monkeypatch, make_response(summary="stage-result"))
    record = pipeline_engine.run_pipeline("test_pipeline", {"text": "hello"}, registry=registry)
    assert record.status in ("succeeded", "partial_failure")
    assert len(record.step_runs) == 2
    assert record.step_runs[0].step_id == "first"
    assert record.step_runs[1].step_id == "second"


def test_run_pipeline_unknown_id_records_error(registry):
    record = pipeline_engine.run_pipeline("missing", {})
    assert record.status in ("aborted", "failed", "error")


def test_pipeline_run_persists_and_reloads(registry, two_step_pipeline, monkeypatch, make_response):
    _fake_llm(monkeypatch, make_response(summary="ok"))
    record = pipeline_engine.run_pipeline("test_pipeline", {"text": "hi"}, registry=registry)
    loaded = pipeline_engine.get_pipeline_run(record.pipeline_run_id)
    assert loaded is not None
    assert loaded.pipeline_id == "test_pipeline"
