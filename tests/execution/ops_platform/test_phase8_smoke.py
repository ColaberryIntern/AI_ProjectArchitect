"""Phase 8 end-to-end smoke — collab + agents + orchestration + scorecards +
forecasting + backup together."""

import pytest

from execution.ops_platform import (
    agent_registry, agent_runtime, approvals, audit_log, backup_restore,
    cache_bus, chaos_engine, collab_sessions, controls, distributed_lock,
    forecasting, governance_scorecards, incidents, knowledge_graph,
    migrations, optimistic_concurrency, orchestration_engine, presence,
    realtime_bus, runtime_queue, scheduler, secrets, service_identities,
    signed_audit, worker_coordination, workflow_runner,
)
from execution.ops_platform.capability_registry import CapabilityRegistry
from execution.ops_platform.identity import IdentityContext
from execution.ops_platform.plugin_loader import load_plugins


@pytest.fixture
def registry(fake_plugin_root, tmp_path, monkeypatch):
    import execution.ops_platform.capability_registry as crm
    monkeypatch.setattr(crm, "_STATS_PATH", tmp_path / "registry_stats.json")
    monkeypatch.setattr(workflow_runner, "_RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(collab_sessions, "_SESSIONS_DIR", tmp_path / "collab/sessions")
    monkeypatch.setattr(collab_sessions, "_REVISIONS_DIR", tmp_path / "collab/revisions")
    monkeypatch.setattr(collab_sessions, "_COMMENTS_DIR", tmp_path / "collab/comments")
    monkeypatch.setattr(distributed_lock, "_LOCKS_DIR", tmp_path / "locks")
    monkeypatch.setattr(realtime_bus, "_EVENTS_DIR", tmp_path / "events")
    monkeypatch.setattr(realtime_bus, "_SEQUENCE_PATH", tmp_path / "sequence.json")
    monkeypatch.setattr(presence, "_PRESENCE_DIR", tmp_path / "presence")
    monkeypatch.setattr(audit_log, "_AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(signed_audit, "_SIGNED_DIR", tmp_path / "audit_signed")
    monkeypatch.setattr(agent_registry, "_AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr(agent_runtime, "_EXECUTIONS_DIR", tmp_path / "agent_executions")
    monkeypatch.setattr(approvals, "_APPROVALS_DIR", tmp_path / "approvals")
    monkeypatch.setattr(controls, "_CONTROLS_DIR", tmp_path / "controls")
    monkeypatch.setattr(scheduler, "_SCHEDULES_DIR", tmp_path / "schedules")
    monkeypatch.setattr(runtime_queue, "_QUEUE_DIR", tmp_path / "queue")
    monkeypatch.setattr(chaos_engine, "_CHAOS_DIR", tmp_path / "chaos")
    monkeypatch.setattr(orchestration_engine, "_ORCH_DIR", tmp_path / "orchestrations")
    monkeypatch.setattr(incidents, "_INCIDENTS_DIR", tmp_path / "incidents")
    monkeypatch.setattr(knowledge_graph, "_GRAPH_DIR", tmp_path / "kg")
    monkeypatch.setattr(worker_coordination, "_WORKERS_DIR", tmp_path / "workers")
    monkeypatch.setattr(service_identities, "_SERVICES_DIR", tmp_path / "service_ids")
    monkeypatch.setattr(governance_scorecards, "_SCORECARDS_DIR", tmp_path / "scorecards")
    monkeypatch.setattr(backup_restore, "_BACKUPS_DIR", tmp_path / "backups")
    monkeypatch.setattr(backup_restore, "_OPS_ROOT", tmp_path / "ops_root")
    monkeypatch.setattr(migrations, "_MIGRATIONS_DIR", tmp_path / "migrations")
    monkeypatch.setattr(migrations, "_APPLIED_PATH", tmp_path / "migrations" / "applied.json")
    monkeypatch.setattr(secrets, "_SECRETS_DIR", tmp_path / "secrets")
    monkeypatch.setattr(cache_bus, "_VERSION_DIR", tmp_path / "versions")
    controls._RATE_LIMIT_HITS.clear()
    migrations._REGISTRY.clear()
    cache_bus.reset_for_tests()
    realtime_bus.reset_for_tests()
    reg = CapabilityRegistry(load_fn=lambda: load_plugins(root=fake_plugin_root))
    import execution.ops_platform.capability_registry as crm2
    monkeypatch.setattr(crm2, "_DEFAULT_REGISTRY", reg)
    yield reg
    migrations._REGISTRY.clear()


def test_phase8_substrate_holds_together(registry):
    identity = IdentityContext(user_id="alice", display_name="Alice",
                                  auth_provider="HEADER_AUTH", authenticated=True,
                                  roles=["operator"], workspace_ids=["sales"])

    # 1. Collab session round-trip
    s = collab_sessions.open_session(entity_type="approval", entity_id="appr-1",
                                        editor=identity)
    cm = collab_sessions.post_comment(entity_type="approval", entity_id="appr-1",
                                          author=identity, body="LGTM")
    assert cm.comment_id
    assert collab_sessions.close_session(s.session_id, editor=identity)

    # 2. Optimistic concurrency conflict
    rev = optimistic_concurrency.new_revision()
    with pytest.raises(optimistic_concurrency.ConcurrencyConflict):
        optimistic_concurrency.compare(entity_type="x", entity_id="1",
                                           observed_revision="old",
                                           current_revision=rev)

    # 3. Agent flow — recommend_only never mutates
    a = agent_registry.register_agent(
        name="reliability-bot", description="x",
        autonomy_policy="recommend_only",
        confidence_threshold=0.5,
        permitted_actions=["reclaim_stale_queue"],
        created_by=identity.as_actor(),
    )
    ex = agent_runtime.execute(
        agent_id=a.agent_id, action_kind="reclaim_stale_queue",
        target={}, inputs={},
        reasoning_chain=["queue stuck"], evidence_refs=[{"source": "test"}],
        confidence=0.9, rollback_plan="re-pend nothing",
    )
    assert ex.outcome == "SUGGESTED"

    # 4. Chaos drill + revert
    cap_id = registry.snapshot().capabilities[0]["id"]
    inj = chaos_engine.inject(kind="queue_stall", target_id=cap_id,
                                  duration_seconds=60, actor="chaos")
    assert controls.is_blocked(cap_id)
    chaos_engine.revert(inj.injection_id, actor="chaos")
    assert controls.is_blocked(cap_id) is None

    # 5. Orchestration with a wait + completion path
    o = orchestration_engine.create_orchestration(
        name="t",
        steps=[{"step_id": "wait", "kind": "wait", "wait_seconds": 0}],
    )
    assert o.orchestration_id

    # 6. Knowledge graph build
    g = knowledge_graph.build_graph(registry=registry, lookback_days=7)
    assert len(g.nodes) >= 1

    # 7. Signed audit chain + verify
    audit_log.record(action="phase8.smoke", entity_type="t", entity_id="e",
                       actor="alice")
    signed = signed_audit.sign_pending(days_back=1)
    assert signed >= 1
    report = signed_audit.verify_chain(days=1)
    assert report.valid

    # 8. Governance scorecard
    card = governance_scorecards.build(workspace_id="global", lookback_days=7)
    assert 0 <= card.overall_score <= 100

    # 9. Forecasting
    f = forecasting.forecast_incident_probability(horizon_hours=6)
    assert hasattr(f, "predicted_value")

    # 10. Backup + restore
    backup_restore._OPS_ROOT.mkdir(parents=True, exist_ok=True)
    (backup_restore._OPS_ROOT / "x.txt").write_text("y")
    snap = backup_restore.snapshot(actor="alice")
    assert snap.bytes > 0
    listed = backup_restore.list_snapshots()
    assert listed

    # 11. Migrations
    migrations.register_migration(version="2026.05.0001", description="z",
                                      up=lambda: None, down=lambda: None)
    results = migrations.apply_pending()
    assert results and results[0]["applied"]
