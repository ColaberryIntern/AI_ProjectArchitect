"""Phase 5 end-to-end smoke test.

Exercises Phase 5 modules together:
  1. login → identity check
  2. RBAC denial path produces audit row
  3. Workspace isolation: capability owned by workspace A
     is hidden from a user in workspace B
  4. Runtime routing: register a version, route deterministically
  5. Trust + controls: freeze + emergency_rollback chain
  6. Marketplace: publish + fork with workspace attach
  7. Scoped memory: workspace_id filter
"""

import pytest

from execution.ops_platform import (
    audit_log, auth, cache_bus, capability_versions, controls,
    enforcement, marketplace, organizational_memory, pipeline_engine,
    rbac, runtime_router, scoped_memory, session_store, trust_engine,
    workflow_runner, workspaces,
)
from execution.ops_platform.capability_registry import CapabilityRegistry
from execution.ops_platform.errors import OpsError
from execution.ops_platform.identity import IdentityContext
from execution.ops_platform.plugin_loader import load_plugins
from execution.ops_platform.workflow_runner import RunRecord


@pytest.fixture
def registry(fake_plugin_root, tmp_path, monkeypatch):
    import execution.ops_platform.capability_registry as crm
    monkeypatch.setattr(crm, "_STATS_PATH", tmp_path / "registry_stats.json")
    monkeypatch.setattr(workflow_runner, "_RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(session_store, "_SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(audit_log, "_AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(capability_versions, "_VERSIONS_DIR", tmp_path / "cap_versions")
    monkeypatch.setattr(workspaces, "_WORKSPACES_DIR", tmp_path / "workspaces")
    monkeypatch.setattr(controls, "_CONTROLS_DIR", tmp_path / "controls")
    monkeypatch.setattr(marketplace, "_MARKET_DIR", tmp_path / "marketplace")
    monkeypatch.setattr(scoped_memory, "_SCOPED_DIR", tmp_path / "org_memory")
    monkeypatch.setattr(organizational_memory, "_MEMORY_DIR", tmp_path / "org_memory")
    monkeypatch.setattr(pipeline_engine, "_PIPELINES_DIR", tmp_path / "pipelines")
    monkeypatch.setattr(pipeline_engine, "_PLUGIN_PIPELINES_DIR", tmp_path / "plugin_pipelines")
    monkeypatch.setattr(cache_bus, "_VERSION_DIR", tmp_path / "versions")
    from config import settings
    monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
    controls._RATE_LIMIT_HITS.clear()
    cache_bus.reset_for_tests()
    reg = CapabilityRegistry(load_fn=lambda: load_plugins(root=fake_plugin_root))
    import execution.ops_platform.capability_registry as crm2
    monkeypatch.setattr(crm2, "_DEFAULT_REGISTRY", reg)
    return reg


def test_phase5_holds_together(registry, monkeypatch):
    # 1. Login
    alice = auth.login(user_id="alice", roles=["operator"], workspace_ids=["sales"],
                         auth_provider="HEADER_AUTH")
    assert alice.authenticated

    # 2. RBAC denial
    viewer = IdentityContext(
        user_id="viewer", display_name="Viewer", auth_provider="HEADER_AUTH",
        authenticated=True, roles=["viewer"], workspace_ids=[],
    )
    with pytest.raises(OpsError):
        enforcement.enforce(viewer, "capability.publish")
    rows = audit_log.list_entries(action="enforcement.denied")
    assert rows

    # 3. Workspace isolation
    cap_a = registry.snapshot().capabilities[0]["id"]
    cap_b = registry.snapshot().capabilities[1]["id"]
    workspaces.create_workspace(workspace_id="sales", name="Sales",
                                  owner={"name": "alice"}, capability_ids=[cap_a])
    workspaces.create_workspace(workspace_id="ops", name="Ops",
                                  owner={"name": "alice"}, capability_ids=[cap_b])
    ops_user = IdentityContext(
        user_id="ops_user", display_name="Ops user", auth_provider="HEADER_AUTH",
        authenticated=True, roles=["operator"], workspace_ids=["ops"],
    )
    # cap_a is owned by sales — ops_user denied
    with pytest.raises(OpsError):
        enforcement.enforce(ops_user, "capability.execute",
                              workspace_id="ops", capability_id=cap_a)

    # 4. Runtime routing
    v = capability_versions.register_version(cap_a, semver="1.0.0", changelog="x",
                                                created_by="alice", registry=registry)
    capability_versions.promote(v.version_id, target_status="approved", approver="alice")
    decision = runtime_router.route(cap_a, session_id="alice-session", record_audit=False)
    assert decision.selected_version_id == v.version_id

    # 5. Trust score + controls.emergency_rollback chain
    v2 = capability_versions.register_version(cap_a, semver="2.0.0", changelog="y",
                                                 created_by="alice", registry=registry)
    capability_versions.promote(v2.version_id, target_status="approved", approver="alice")
    result = controls.emergency_rollback(cap_a, target_version_id=v.version_id,
                                            actor=alice.as_actor(), reason="incident")
    cid = result["correlation_id"]
    rb_rows = audit_log.list_entries(correlation_id=cid)
    assert any(r["action"] == "controls.rollback" for r in rb_rows)
    trust = trust_engine.score(cap_a, registry=registry, record_audit=False)
    assert trust.deployment_recommendation in (
        "SAFE_FOR_PRODUCTION", "LIMITED_ROLLOUT", "REQUIRES_REVIEW", "DO_NOT_DEPLOY",
    )

    # 6. Marketplace publish + fork
    tpl = marketplace.publish_capability_template(
        capability_id=cap_a, title="Reusable A", category="Sales",
        published_by=alice.as_actor(),
    )
    fork_result = marketplace.fork(tpl.template_id, workspace_id="sales",
                                     actor=alice.as_actor())
    assert "forked_id" in fork_result
    refreshed = marketplace.get_template(tpl.template_id)
    assert refreshed.fork_count == 1

    # 7. Scoped memory: ops workspace user does not see sales capabilities
    monkeypatch.setattr("execution.ops_platform.cache_bus.is_available", lambda: False, raising=False)
    snap = scoped_memory.build_for_workspace("ops", identity=ops_user, persist=False,
                                                registry=registry)
    visible = [r.get("capability_id") for r in (snap.get("what_succeeds") or [])]
    assert cap_a not in visible
