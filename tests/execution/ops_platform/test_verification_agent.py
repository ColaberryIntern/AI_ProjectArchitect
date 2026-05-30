"""Tests for execution/ops_platform/verification_agent.py"""

import json

import pytest

from execution.ops_platform.capability_registry import CapabilityRegistry
from execution.ops_platform.plugin_loader import load_plugins
from execution.ops_platform import workflow_runner
from execution.ops_platform.verification_agent import verify_run


@pytest.fixture
def registry(fake_plugin_root, tmp_path, monkeypatch):
    import execution.ops_platform.capability_registry as crm
    monkeypatch.setattr(crm, "_STATS_PATH", tmp_path / "registry_stats.json")
    monkeypatch.setattr(workflow_runner, "_RUNS_DIR", tmp_path / "runs")
    return CapabilityRegistry(load_fn=lambda: load_plugins(root=fake_plugin_root))


def _make_run(capability_id="test_summary", response=None, status="succeeded", **kw):
    from execution.ops_platform.workflow_runner import RunRecord
    import uuid
    rec = RunRecord(
        run_id=str(uuid.uuid4()),
        capability_id=capability_id,
        started_at="2026-05-26T00:00:00+00:00",
        finished_at="2026-05-26T00:00:05+00:00",
        status=status,
        inputs={"text": "hi"},
        response=response,
    )
    for k, v in kw.items():
        setattr(rec, k, v)
    return rec


def test_unknown_run_returns_red(registry, monkeypatch):
    monkeypatch.setattr("execution.ops_platform.verification_agent.get_run", lambda _id: None)
    from execution import llm_client
    monkeypatch.setattr(llm_client, "is_available", lambda: False)
    result = verify_run("nonexistent", registry=registry, use_llm=False)
    assert result.payload["deployment_readiness"] == "red"
    assert "not found" in result.errors[0]


def test_failed_run_marks_red(registry, monkeypatch, make_response):
    rec = _make_run(response=make_response(), status="error", error_message="boom")
    monkeypatch.setattr("execution.ops_platform.verification_agent.get_run", lambda _id: rec)
    from execution import llm_client
    monkeypatch.setattr(llm_client, "is_available", lambda: False)
    result = verify_run("any", registry=registry, use_llm=False)
    assert result.payload["deployment_readiness"] == "red"


def test_blocker_known_issue_marks_red(registry, monkeypatch, make_response):
    rec = _make_run(response=make_response(
        known_issues=[{"description": "DB lost", "severity": "blocker"}],
        verification_steps=[{"step": "x", "expected": "y"}],
    ))
    monkeypatch.setattr("execution.ops_platform.verification_agent.get_run", lambda _id: rec)
    from execution import llm_client
    monkeypatch.setattr(llm_client, "is_available", lambda: False)
    result = verify_run("any", registry=registry, use_llm=False)
    assert result.payload["deployment_readiness"] == "red"


def test_clean_run_with_outputs_marks_green(registry, monkeypatch, make_response):
    # Manifest declares output 'summary'; provide it. Add a verification step.
    rec = _make_run(response=make_response(
        summary="Did it.",
        verification_steps=[{"step": "check it", "expected": "ok"}],
        tests_written=[{"path": "tests/test_x.py", "count": 1, "scope": "unit"}],
    ))
    monkeypatch.setattr("execution.ops_platform.verification_agent.get_run", lambda _id: rec)
    from execution import llm_client
    monkeypatch.setattr(llm_client, "is_available", lambda: False)
    result = verify_run("any", registry=registry, use_llm=False)
    assert result.payload["deployment_readiness"] == "green"
    assert "summary" in result.payload["completed_requirements"]


def test_missing_verification_steps_downgrades_to_yellow(registry, monkeypatch, make_response):
    rec = _make_run(response=make_response(summary="x"))  # no verification_steps
    monkeypatch.setattr("execution.ops_platform.verification_agent.get_run", lambda _id: rec)
    from execution import llm_client
    monkeypatch.setattr(llm_client, "is_available", lambda: False)
    result = verify_run("any", registry=registry, use_llm=False)
    assert result.payload["deployment_readiness"] in ("yellow", "red")


def test_payload_passes_schema(registry, monkeypatch, make_response):
    rec = _make_run(response=make_response(
        verification_steps=[{"step": "ok", "expected": "ok"}],
        tests_written=[{"path": "t/x.py", "count": 1, "scope": "unit"}],
    ))
    monkeypatch.setattr("execution.ops_platform.verification_agent.get_run", lambda _id: rec)
    from execution import llm_client
    monkeypatch.setattr(llm_client, "is_available", lambda: False)
    result = verify_run("any", registry=registry, use_llm=False)
    # All 8 required keys present
    for k in ["completed_requirements", "partial_requirements", "missing_requirements",
              "architecture_issues", "ui_issues", "technical_debt",
              "recommendations", "deployment_readiness"]:
        assert k in result.payload
