"""Tests for Phase 3 pipeline engine extensions: parallel, when, retry_n."""

import json
from unittest.mock import patch

import pytest

from execution.llm_client import LLMResponse
from execution.ops_platform import cache_bus, pipeline_engine, workflow_runner
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
    monkeypatch.setattr(cache_bus, "_VERSION_DIR", tmp_path / "versions")
    cache_bus.reset_for_tests()
    return CapabilityRegistry(load_fn=lambda: load_plugins(root=fake_plugin_root))


def _fake_llm_ok(monkeypatch, payload):
    from execution import llm_client
    monkeypatch.setattr(llm_client, "is_available", lambda: True)
    monkeypatch.setattr(llm_client, "chat", lambda **kw: LLMResponse(
        content=json.dumps(payload), model="t",
        usage={"prompt_tokens": 1, "completion_tokens": 1}, stop_reason="stop",
    ))


def _write_parallel_pipeline(registry):
    manifest = {
        "pipeline_id": "parallel_test",
        "name": "Parallel Test",
        "version": "1.0.0",
        "created_by": {"name": "tests"},
        "execution_strategy": "parallel_independent",
        "steps": [
            {"step_id": "a", "capability_id": "test_summary"},
            {"step_id": "b", "capability_id": "test_compose"},
        ],
    }
    pipeline_engine._PIPELINES_DIR.mkdir(parents=True, exist_ok=True)
    (pipeline_engine._PIPELINES_DIR / "parallel_test.json").write_text(json.dumps(manifest))


def _write_conditional_pipeline(registry):
    manifest = {
        "pipeline_id": "conditional_test",
        "name": "Conditional Test",
        "version": "1.0.0",
        "created_by": {"name": "tests"},
        "inputs": [{"name": "run_second", "type": "string", "required": True}],
        "steps": [
            {"step_id": "a", "capability_id": "test_summary"},
            {
                "step_id": "b",
                "capability_id": "test_compose",
                "depends_on": ["a"],
                "when": {"equals": ["$pipeline.run_second", "yes"]},
            },
        ],
    }
    pipeline_engine._PIPELINES_DIR.mkdir(parents=True, exist_ok=True)
    (pipeline_engine._PIPELINES_DIR / "conditional_test.json").write_text(json.dumps(manifest))


def test_parallel_independent_runs_both(registry, monkeypatch, make_response):
    _fake_llm_ok(monkeypatch, make_response(summary="Completed successfully."))
    _write_parallel_pipeline(registry)
    record = pipeline_engine.run_pipeline("parallel_test", {}, registry=registry)
    if record.status != "succeeded":
        details = [(sr.step_id, sr.status, sr.error_message) for sr in record.step_runs]
        raise AssertionError(f"pipeline status={record.status}; step details={details}")
    assert len(record.step_runs) == 2
    assert all(sr.status == "succeeded" for sr in record.step_runs)


def test_when_clause_skips_step(registry, monkeypatch, make_response):
    _fake_llm_ok(monkeypatch, make_response(summary="Completed successfully."))
    _write_conditional_pipeline(registry)
    record = pipeline_engine.run_pipeline(
        "conditional_test", {"run_second": "no"}, registry=registry,
    )
    step_b = next(sr for sr in record.step_runs if sr.step_id == "b")
    assert step_b.status == "skipped"
    assert "condition not met" in (step_b.error_message or "")


def test_when_clause_runs_step_when_true(registry, monkeypatch, make_response):
    _fake_llm_ok(monkeypatch, make_response(summary="Completed successfully."))
    _write_conditional_pipeline(registry)
    record = pipeline_engine.run_pipeline(
        "conditional_test", {"run_second": "yes"}, registry=registry,
    )
    step_b = next(sr for sr in record.step_runs if sr.step_id == "b")
    assert step_b.status == "succeeded"


def test_retry_n_policy_succeeds_on_second_attempt(registry, monkeypatch, make_response):
    from execution import llm_client
    monkeypatch.setattr(llm_client, "is_available", lambda: True)

    calls = {"n": 0}
    def flaky(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return LLMResponse(content="not json", model="t", usage={}, stop_reason="stop")
        return LLMResponse(content=json.dumps(make_response(summary="Recovered on the second attempt.")),
                           model="t",
                           usage={"prompt_tokens": 1, "completion_tokens": 1}, stop_reason="stop")
    monkeypatch.setattr(llm_client, "chat", flaky)

    manifest = {
        "pipeline_id": "retry_test",
        "name": "Retry Test",
        "version": "1.0.0",
        "created_by": {"name": "tests"},
        "steps": [
            {"step_id": "a", "capability_id": "test_summary",
             "on_failure": {"retry": 2}},
        ],
    }
    pipeline_engine._PIPELINES_DIR.mkdir(parents=True, exist_ok=True)
    (pipeline_engine._PIPELINES_DIR / "retry_test.json").write_text(json.dumps(manifest))

    record = pipeline_engine.run_pipeline("retry_test", {}, registry=registry)
    assert record.step_runs[0].status == "retried_succeeded"


def test_save_pipeline_validates_and_emits(registry, monkeypatch):
    events = []
    cache_bus.subscribe(cache_bus.Topic.PIPELINE_CREATED, lambda e: events.append(e))
    bad = {"pipeline_id": "x"}  # missing required fields
    with pytest.raises(ValueError):
        pipeline_engine.save_pipeline(bad)
    good = {
        "pipeline_id": "good_pipeline",
        "name": "good",
        "version": "1.0.0",
        "created_by": {"name": "test"},
        "steps": [{"step_id": "s", "capability_id": "test_summary"}],
    }
    pipeline_engine.save_pipeline(good)
    assert any(e.payload.get("pipeline_id") == "good_pipeline" for e in events)


def test_replay_pipeline_from_step(registry, monkeypatch, make_response):
    _fake_llm_ok(monkeypatch, make_response(summary="Completed successfully."))
    manifest = {
        "pipeline_id": "replay_test",
        "name": "Replay Test",
        "version": "1.0.0",
        "created_by": {"name": "tests"},
        "inputs": [{"name": "text", "type": "text", "required": True}],
        "steps": [
            {"step_id": "first", "capability_id": "test_summary",
             "input_bindings": {"text": "$pipeline.text"}},
            {"step_id": "second", "capability_id": "test_compose",
             "depends_on": ["first"],
             "input_bindings": {"text": "$pipeline.text"}},
        ],
    }
    pipeline_engine._PIPELINES_DIR.mkdir(parents=True, exist_ok=True)
    (pipeline_engine._PIPELINES_DIR / "replay_test.json").write_text(json.dumps(manifest))
    original = pipeline_engine.run_pipeline("replay_test", {"text": "hi"}, registry=registry)
    replayed = pipeline_engine.replay_pipeline_from(
        original.pipeline_run_id, from_step_id="second", registry=registry,
    )
    assert replayed is not None
    assert replayed.pipeline_id.startswith("replay_test_recover_")
