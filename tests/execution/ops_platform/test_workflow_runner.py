"""Tests for execution/ops_platform/workflow_runner.py"""

import json
from unittest.mock import patch

import pytest

from execution.llm_client import LLMResponse
from execution.ops_platform.capability_registry import CapabilityRegistry
from execution.ops_platform.plugin_loader import load_plugins
from execution.ops_platform import workflow_runner
from execution.ops_platform.workflow_runner import RunRecord, run_workflow


@pytest.fixture
def registry(fake_plugin_root, tmp_path, monkeypatch):
    import execution.ops_platform.capability_registry as crm
    monkeypatch.setattr(crm, "_STATS_PATH", tmp_path / "registry_stats.json")
    monkeypatch.setattr(workflow_runner, "_RUNS_DIR", tmp_path / "runs")
    return CapabilityRegistry(load_fn=lambda: load_plugins(root=fake_plugin_root))


@pytest.fixture
def llm_responds(monkeypatch, make_response):
    """Patch llm_client.chat to return whatever the caller specifies."""
    def _patch(payload, *, available=True):
        from execution import llm_client
        monkeypatch.setattr(llm_client, "is_available", lambda: available)
        def fake_chat(**kwargs):
            return LLMResponse(
                content=json.dumps(payload),
                model="test",
                usage={"prompt_tokens": 10, "completion_tokens": 20},
                stop_reason="stop",
            )
        monkeypatch.setattr(llm_client, "chat", fake_chat)
    return _patch


def test_run_unknown_capability_returns_error_record(registry):
    run = run_workflow("does-not-exist", {}, registry=registry)
    assert run.status == "error"
    assert "not registered" in run.error_message


def test_run_when_llm_unavailable(registry, monkeypatch):
    from execution import llm_client
    monkeypatch.setattr(llm_client, "is_available", lambda: False)
    run = run_workflow("test_summary", {"text": "hi"}, registry=registry)
    assert run.status == "llm_unavailable"


def test_successful_run_validates_and_persists(registry, llm_responds, make_response):
    payload = make_response(summary="A solid result.")
    llm_responds(payload)
    run = run_workflow("test_summary", {"text": "hi"}, registry=registry)
    assert run.status == "succeeded"
    assert run.response["summary"] == "A solid result."
    # Persisted to disk and re-loadable
    loaded = workflow_runner.get_run(run.run_id)
    assert loaded is not None
    assert loaded.response["summary"] == "A solid result."


def test_partial_response_is_coerced(registry, llm_responds):
    # Only summary returned; runner coerces the rest to empty defaults.
    llm_responds({"summary": "only summary"})
    run = run_workflow("test_summary", {"text": "hi"}, registry=registry)
    assert run.status == "succeeded"
    assert run.response["files_created"] == []
    assert run.response["summary"] == "only summary"


def test_garbage_response_marks_contract_failed(registry, monkeypatch):
    from execution import llm_client
    monkeypatch.setattr(llm_client, "is_available", lambda: True)
    def fake_chat(**kwargs):
        return LLMResponse(content="not JSON at all", model="test", usage={}, stop_reason="stop")
    monkeypatch.setattr(llm_client, "chat", fake_chat)
    run = run_workflow("test_summary", {"text": "hi"}, registry=registry)
    assert run.status == "contract_failed"
    assert run.contract_errors


def test_usage_count_increments_on_success(registry, llm_responds, make_response):
    llm_responds(make_response())
    run_workflow("test_summary", {"text": "x"}, registry=registry)
    cap = registry.get("test_summary")
    assert cap["usage_count"] == 1


def test_run_id_is_uuid(registry, llm_responds, make_response):
    llm_responds(make_response())
    run = run_workflow("test_summary", {"text": "x"}, registry=registry)
    import re
    assert re.match(r"^[0-9a-f-]{36}$", run.run_id)
