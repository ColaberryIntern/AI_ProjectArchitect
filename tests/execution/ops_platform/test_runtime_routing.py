"""Tests for execution/ops_platform/runtime_router.py + workflow_runner wiring."""

import json
from unittest.mock import patch

import pytest

from execution.llm_client import LLMResponse
from execution.ops_platform import (
    audit_log, cache_bus, capability_versions, controls, runtime_router,
    workflow_runner,
)
from execution.ops_platform.capability_registry import CapabilityRegistry
from execution.ops_platform.plugin_loader import load_plugins


@pytest.fixture
def registry(fake_plugin_root, tmp_path, monkeypatch):
    import execution.ops_platform.capability_registry as crm
    monkeypatch.setattr(crm, "_STATS_PATH", tmp_path / "registry_stats.json")
    monkeypatch.setattr(workflow_runner, "_RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(audit_log, "_AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(capability_versions, "_VERSIONS_DIR", tmp_path / "cap_versions")
    monkeypatch.setattr(controls, "_CONTROLS_DIR", tmp_path / "controls")
    monkeypatch.setattr(cache_bus, "_VERSION_DIR", tmp_path / "versions")
    cache_bus.reset_for_tests()
    return CapabilityRegistry(load_fn=lambda: load_plugins(root=fake_plugin_root))


def test_route_with_no_versions_falls_back(registry):
    cap_id = registry.snapshot().capabilities[0]["id"]
    decision = runtime_router.route(cap_id, session_id="s1", record_audit=False)
    assert decision.rollout_source == "fallback"
    assert decision.selected_version_id is None


def test_route_picks_approved_when_present(registry):
    cap_id = registry.snapshot().capabilities[0]["id"]
    v = capability_versions.register_version(cap_id, semver="1.0.0", changelog="x",
                                                created_by="x", registry=registry)
    capability_versions.promote(v.version_id, target_status="approved", approver="x")
    decision = runtime_router.route(cap_id, session_id="s1", record_audit=False)
    assert decision.rollout_source == "approved"
    assert decision.selected_version_id == v.version_id


def test_deterministic_bucket_same_session_same_result(registry):
    cap_id = registry.snapshot().capabilities[0]["id"]
    v = capability_versions.register_version(cap_id, semver="1.0.0", changelog="x",
                                                created_by="x", registry=registry)
    capability_versions.promote(v.version_id, target_status="approved", approver="x")
    d1 = runtime_router.route(cap_id, session_id="constant", record_audit=False)
    d2 = runtime_router.route(cap_id, session_id="constant", record_audit=False)
    assert d1.bucket == d2.bucket
    assert d1.selected_version_id == d2.selected_version_id


def test_experimental_consumes_rollout_percentage(registry):
    cap_id = registry.snapshot().capabilities[0]["id"]
    v_app = capability_versions.register_version(cap_id, semver="1.0.0", changelog="x",
                                                    created_by="x", registry=registry)
    capability_versions.promote(v_app.version_id, target_status="approved", approver="x")
    v_exp = capability_versions.register_version(cap_id, semver="2.0.0", changelog="exp",
                                                    created_by="x", registry=registry)
    capability_versions.promote(v_exp.version_id, target_status="experimental",
                                  approver="x", rollout_percentage=100.0)
    decision = runtime_router.route(cap_id, session_id="s1", record_audit=False)
    # 100% experimental should always pick the experimental
    assert decision.selected_version_id == v_exp.version_id
    assert decision.rollout_source == "experimental"


def test_simulate_returns_distribution(registry):
    cap_id = registry.snapshot().capabilities[0]["id"]
    v = capability_versions.register_version(cap_id, semver="1.0.0", changelog="x",
                                                created_by="x", registry=registry)
    capability_versions.promote(v.version_id, target_status="approved", approver="x")
    out = runtime_router.simulate(cap_id, samples=100)
    assert out["samples"] == 100
    assert sum(out["distribution"].values()) == pytest.approx(100.0, abs=0.5)


def test_route_records_audit_row(registry):
    cap_id = registry.snapshot().capabilities[0]["id"]
    decision = runtime_router.route(cap_id, session_id="s9", record_audit=True)
    rows = audit_log.list_entries(action="routing.selected")
    assert any(r.get("correlation_id") == decision.correlation_id for r in rows)


def test_workflow_runner_routes_when_session_present(registry, monkeypatch):
    """The runner reads __session_id from inputs and substitutes the version's
    manifest_snapshot."""
    from execution import llm_client
    monkeypatch.setattr(llm_client, "is_available", lambda: True)
    monkeypatch.setattr(llm_client, "chat", lambda **kw: LLMResponse(
        content=json.dumps({
            "summary": "Routed and completed successfully.",
            "files_created": [], "files_modified": [], "components_added": [],
            "database_changes": [], "routes_added": [], "dependencies_added": [],
            "mcp_servers_used": [], "agents_used": [], "tests_written": [],
            "known_issues": [], "verification_steps": [], "next_recommended_tasks": [],
        }), model="t", usage={"prompt_tokens": 1, "completion_tokens": 1}, stop_reason="stop"))

    cap_id = registry.snapshot().capabilities[0]["id"]
    v = capability_versions.register_version(cap_id, semver="1.0.0", changelog="x",
                                                created_by="x", registry=registry)
    capability_versions.promote(v.version_id, target_status="approved", approver="x")

    run = workflow_runner.run_workflow(cap_id, {"text": "hi", "__session_id": "s1"},
                                          registry=registry)
    assert run.status == "succeeded"
    assert run.inputs.get("__capability_version_id") == v.version_id


def test_workflow_runner_skips_routing_without_session(registry, monkeypatch):
    """Without __session_id the runner skips the router entirely (Phase 1-4 behavior)."""
    from execution import llm_client
    monkeypatch.setattr(llm_client, "is_available", lambda: True)
    monkeypatch.setattr(llm_client, "chat", lambda **kw: LLMResponse(
        content=json.dumps({
            "summary": "Normal Phase 1-4 invocation.",
            "files_created": [], "files_modified": [], "components_added": [],
            "database_changes": [], "routes_added": [], "dependencies_added": [],
            "mcp_servers_used": [], "agents_used": [], "tests_written": [],
            "known_issues": [], "verification_steps": [], "next_recommended_tasks": [],
        }), model="t", usage={"prompt_tokens": 1, "completion_tokens": 1}, stop_reason="stop"))
    cap_id = registry.snapshot().capabilities[0]["id"]
    run = workflow_runner.run_workflow(cap_id, {"text": "hi"}, registry=registry)
    assert run.status == "succeeded"
    assert "__capability_version_id" not in run.inputs
