"""Phase 7 smoke test — exercises the new realtime + observability +
governance + copilot modules together."""

import json

import pytest

from execution.ops_platform import (
    alerts, approvals, audit_log, cache_bus, capability_versions,
    change_requests, compliance_reports, controls, copilot, distributed_lock,
    incidents, notifications, optimistic_concurrency, presence,
    prometheus_exporter, realtime_bus, reliability_monitor, runtime_queue,
    secrets, shared_cache_backend, tracing, workflow_runner, workspaces,
)
from execution.ops_platform.capability_registry import CapabilityRegistry
from execution.ops_platform.identity import IdentityContext
from execution.ops_platform.plugin_loader import load_plugins


@pytest.fixture
def registry(fake_plugin_root, tmp_path, monkeypatch):
    import execution.ops_platform.capability_registry as crm
    monkeypatch.setattr(crm, "_STATS_PATH", tmp_path / "registry_stats.json")
    monkeypatch.setattr(workflow_runner, "_RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(realtime_bus, "_EVENTS_DIR", tmp_path / "events")
    monkeypatch.setattr(realtime_bus, "_SEQUENCE_PATH", tmp_path / "sequence.json")
    monkeypatch.setattr(presence, "_PRESENCE_DIR", tmp_path / "presence")
    monkeypatch.setattr(distributed_lock, "_LOCKS_DIR", tmp_path / "locks")
    monkeypatch.setattr(runtime_queue, "_QUEUE_DIR", tmp_path / "queue")
    monkeypatch.setattr(controls, "_CONTROLS_DIR", tmp_path / "controls")
    monkeypatch.setattr(audit_log, "_AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(alerts, "_ALERTS_DIR", tmp_path / "alerts")
    monkeypatch.setattr(alerts, "_RULES_DIR", tmp_path / "alerts" / "rules")
    monkeypatch.setattr(alerts, "_ACTIVE_DIR", tmp_path / "alerts" / "active")
    monkeypatch.setattr(alerts, "_HISTORY_DIR", tmp_path / "alerts" / "history")
    monkeypatch.setattr(notifications, "_NOTIF_DIR", tmp_path / "notifications")
    monkeypatch.setattr(notifications, "_CHANNELS_DIR", tmp_path / "notifications" / "channels")
    monkeypatch.setattr(tracing, "_TRACING_DIR", tmp_path / "tracing")
    monkeypatch.setattr(change_requests, "_CR_DIR", tmp_path / "crs")
    monkeypatch.setattr(approvals, "_APPROVALS_DIR", tmp_path / "approvals")
    monkeypatch.setattr(incidents, "_INCIDENTS_DIR", tmp_path / "incidents")
    monkeypatch.setattr(workspaces, "_WORKSPACES_DIR", tmp_path / "workspaces")
    monkeypatch.setattr(capability_versions, "_VERSIONS_DIR", tmp_path / "cap_versions")
    monkeypatch.setattr(secrets, "_SECRETS_DIR", tmp_path / "secrets")
    monkeypatch.setattr(compliance_reports, "_REPORTS_DIR", tmp_path / "compliance")
    monkeypatch.setattr(cache_bus, "_VERSION_DIR", tmp_path / "versions")
    shared_cache_backend.configure(shared_cache_backend.FileBackend(root=tmp_path / "versions"))
    controls._RATE_LIMIT_HITS.clear()
    cache_bus.reset_for_tests()
    realtime_bus.reset_for_tests()
    reg = CapabilityRegistry(load_fn=lambda: load_plugins(root=fake_plugin_root))
    import execution.ops_platform.capability_registry as crm2
    monkeypatch.setattr(crm2, "_DEFAULT_REGISTRY", reg)
    yield reg
    shared_cache_backend.reset_for_tests()


def test_phase7_substrate_holds_together(registry):
    # 1. Realtime event lands in replay
    realtime_bus.emit("workflow.started", actor={"name": "alice"})
    rows = realtime_bus.replay()
    assert rows
    assert any(r.event_type == "workflow.started" for r in rows)

    # 2. Presence heartbeat + read
    identity = IdentityContext(user_id="alice", display_name="Alice",
                                  auth_provider="HEADER_AUTH", authenticated=True,
                                  roles=["operator"], workspace_ids=["sales"])
    presence.heartbeat(workspace_id="sales", identity=identity)
    active = presence.active_in_workspace("sales")
    assert any(r["user_id"] == "alice" for r in active)

    # 3. Optimistic concurrency conflict
    rev = optimistic_concurrency.new_revision()
    with pytest.raises(optimistic_concurrency.ConcurrencyConflict):
        optimistic_concurrency.compare(entity_type="x", entity_id="1",
                                           observed_revision="stale",
                                           current_revision=rev)

    # 4. Tracing spans persist
    with tracing.span("smoke.workflow") as outer:
        with tracing.span("smoke.step"):
            pass
    tree = tracing.trace_tree(outer.trace_id)
    assert len(tree) >= 2

    # 5. Alert lifecycle
    alerts.upsert_rule(rule_id="smoke", name="x", metric="m", operator=">",
                         threshold=5, severity=3)
    fired = alerts.evaluate_rules(metric_values={"m": 10})
    assert fired
    alerts.acknowledge(fired[0].alert_id, actor="alice")
    alerts.resolve(fired[0].alert_id, actor="alice")
    assert not alerts.list_active(rule_id="smoke")

    # 6. Notification delivery records failure (no real webhook)
    notifications.upsert_channel(channel_id="wh", name="x", kind="webhook",
                                    config={"url": "http://127.0.0.1:1/nope"})
    rec = notifications.send("wh", title="t", body="b")
    assert rec.success is False

    # 7. Change request lifecycle
    cr = change_requests.draft(
        title="t", action="x", entity_type="t", entity_id="e",
        proposed_change={}, rollback_plan="undo", requested_by="alice",
    )
    change_requests.submit(cr.cr_id, single_approver_roles=["admin"])
    appr_id = change_requests.get(cr.cr_id).approval_request_id
    approvals.submit_decision(appr_id, approver={"name": "bob", "roles": ["admin"]},
                                 decision="approved")
    synced = change_requests.sync_state_from_approval(cr.cr_id)
    assert synced.state == "approved"

    # 8. Compliance report renders
    report = compliance_reports.operational_report(days=7, format="json")
    assert "audit_summary" in report

    # 9. Copilot answers blocked-approvals
    ans = copilot.ask("What approvals are blocked?")
    assert ans.intent == "blocked_approvals"

    # 10. Prometheus exporter renders text
    text = prometheus_exporter.render()
    assert "ops_capability_total" in text
