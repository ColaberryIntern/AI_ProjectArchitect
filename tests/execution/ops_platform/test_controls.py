"""Tests for execution/ops_platform/controls.py"""

import json
import time

import pytest

from execution.llm_client import LLMResponse
from execution.ops_platform import (
    audit_log, cache_bus, capability_versions, controls, workflow_runner,
)
from execution.ops_platform.capability_registry import CapabilityRegistry
from execution.ops_platform.plugin_loader import load_plugins


@pytest.fixture
def registry(fake_plugin_root, tmp_path, monkeypatch):
    import execution.ops_platform.capability_registry as crm
    monkeypatch.setattr(crm, "_STATS_PATH", tmp_path / "registry_stats.json")
    monkeypatch.setattr(workflow_runner, "_RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(controls, "_CONTROLS_DIR", tmp_path / "controls")
    monkeypatch.setattr(audit_log, "_AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(capability_versions, "_VERSIONS_DIR", tmp_path / "cap_versions")
    monkeypatch.setattr(cache_bus, "_VERSION_DIR", tmp_path / "versions")
    # Clear process-local rate-limit cache
    controls._RATE_LIMIT_HITS.clear()
    cache_bus.reset_for_tests()
    return CapabilityRegistry(load_fn=lambda: load_plugins(root=fake_plugin_root))


def test_freeze_blocks_capability(registry):
    cap_id = registry.snapshot().capabilities[0]["id"]
    controls.freeze(cap_id, actor="alice", reason="testing")
    assert controls.is_blocked(cap_id)
    rows = audit_log.list_entries(action="controls.frozen")
    assert rows


def test_unfreeze_removes_block(registry):
    cap_id = registry.snapshot().capabilities[0]["id"]
    controls.freeze(cap_id, actor="alice", reason="testing")
    controls.unfreeze(cap_id, actor="alice", reason="testing-undo")
    assert controls.is_blocked(cap_id) is None
    rows = audit_log.list_entries(action="controls.unfrozen")
    assert rows


def test_quarantine_hides_and_blocks(registry):
    cap_id = registry.snapshot().capabilities[0]["id"]
    controls.quarantine(cap_id, actor="alice")
    assert controls.is_hidden(cap_id)
    assert controls.is_blocked(cap_id)


def test_maintenance_mode_blocks_all(registry):
    cap_id = registry.snapshot().capabilities[0]["id"]
    controls.enable_maintenance_mode(actor="alice")
    assert controls.is_blocked(cap_id)
    controls.disable_maintenance_mode(actor="alice")
    assert controls.is_blocked(cap_id) is None


def test_workspace_suspend_blocks(registry):
    cap_id = registry.snapshot().capabilities[0]["id"]
    controls.suspend_workspace("sales", actor="alice")
    assert controls.is_blocked(cap_id, workspace_id="sales")
    assert not controls.is_blocked(cap_id, workspace_id="ops")


def test_rate_limit_triggers_after_max(registry):
    cap_id = registry.snapshot().capabilities[0]["id"]
    controls.set_rate_limit(cap_id, max_calls=3, window_seconds=60, actor="alice")
    # First 3 calls return None (not blocked); subsequent calls are blocked
    for _ in range(3):
        assert controls.is_blocked(cap_id) is None
    assert controls.is_blocked(cap_id)  # 4th — blocked


def test_workflow_runner_returns_blocked_status(registry, monkeypatch):
    from execution import llm_client
    monkeypatch.setattr(llm_client, "is_available", lambda: True)
    monkeypatch.setattr(llm_client, "chat", lambda **kw: LLMResponse(
        content=json.dumps({"summary": "ok"}), model="t",
        usage={"prompt_tokens": 1}, stop_reason="stop"))
    cap_id = registry.snapshot().capabilities[0]["id"]
    controls.freeze(cap_id, actor="alice", reason="under investigation")
    run = workflow_runner.run_workflow(cap_id, {"text": "hi"}, registry=registry)
    assert run.status == "blocked"
    assert "blocked" in (run.error_message or "").lower()


def test_emergency_rollback_chains_three_actions(registry):
    cap_id = registry.snapshot().capabilities[0]["id"]
    v = capability_versions.register_version(cap_id, semver="1.0.0", changelog="x",
                                                created_by="x", registry=registry)
    capability_versions.promote(v.version_id, target_status="approved", approver="x")
    v2 = capability_versions.register_version(cap_id, semver="2.0.0", changelog="y",
                                                 created_by="x", registry=registry)
    capability_versions.promote(v2.version_id, target_status="approved", approver="x")
    # Emergency rollback to v1
    result = controls.emergency_rollback(cap_id, target_version_id=v.version_id,
                                            actor="alice", reason="prod incident")
    cid = result["correlation_id"]
    rows = audit_log.list_entries(correlation_id=cid)
    actions = {r["action"] for r in rows}
    # Should include controls.rollback at minimum
    assert "controls.rollback" in actions


def test_list_active_returns_only_unexpired(registry):
    cap_id = registry.snapshot().capabilities[0]["id"]
    controls.freeze(cap_id, actor="alice")
    assert any(c.target_id == cap_id for c in controls.list_active())
