"""Phase 6 end-to-end smoke test — verifies the new runtime substrate
holds together across modules:

  1. Worker registers + heartbeats
  2. Job enqueued, claimed under distributed_lock, ack'd
  3. Scheduler creates an event-triggered schedule and fires it
  4. Reliability monitor scans + self-healing runs gated by policy
  5. Approval request lifecycle approved → executed
  6. Experiment created, started, assigned deterministically, evaluated
"""

import json

import pytest

from execution.ops_platform import (
    approvals, audit_log, auth, cache_bus, capability_versions, controls,
    distributed_lock, distributed_rate_limit, enforcement, evaluation,
    experiments, incidents, policy_engine, reliability_monitor,
    runtime_queue, scheduler, secrets, self_healing, service_identities,
    session_store, shared_cache_backend, worker_coordination, workflow_runner,
)
from execution.ops_platform.capability_registry import CapabilityRegistry
from execution.ops_platform.identity import IdentityContext
from execution.ops_platform.plugin_loader import load_plugins


@pytest.fixture
def registry(fake_plugin_root, tmp_path, monkeypatch):
    import execution.ops_platform.capability_registry as crm
    monkeypatch.setattr(crm, "_STATS_PATH", tmp_path / "registry_stats.json")
    monkeypatch.setattr(workflow_runner, "_RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(distributed_lock, "_LOCKS_DIR", tmp_path / "locks")
    monkeypatch.setattr(runtime_queue, "_QUEUE_DIR", tmp_path / "queue")
    monkeypatch.setattr(worker_coordination, "_WORKERS_DIR", tmp_path / "workers")
    monkeypatch.setattr(distributed_rate_limit, "_RATE_DIR", tmp_path / "rates")
    monkeypatch.setattr(distributed_rate_limit, "_POLICIES_PATH",
                          tmp_path / "rates" / "_policies.json")
    monkeypatch.setattr(scheduler, "_SCHEDULES_DIR", tmp_path / "schedules")
    monkeypatch.setattr(approvals, "_APPROVALS_DIR", tmp_path / "approvals")
    monkeypatch.setattr(incidents, "_INCIDENTS_DIR", tmp_path / "incidents")
    monkeypatch.setattr(experiments, "_EXPERIMENTS_DIR", tmp_path / "experiments")
    monkeypatch.setattr(policy_engine, "_POLICIES_DIR", tmp_path / "policies")
    monkeypatch.setattr(service_identities, "_SERVICES_DIR", tmp_path / "service_ids")
    monkeypatch.setattr(secrets, "_SECRETS_DIR", tmp_path / "secrets")
    monkeypatch.setattr(controls, "_CONTROLS_DIR", tmp_path / "controls")
    monkeypatch.setattr(session_store, "_SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(audit_log, "_AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(cache_bus, "_VERSION_DIR", tmp_path / "versions")
    monkeypatch.setattr(capability_versions, "_VERSIONS_DIR", tmp_path / "cap_versions")
    shared_cache_backend.configure(shared_cache_backend.FileBackend(root=tmp_path / "versions"))
    controls._RATE_LIMIT_HITS.clear()
    cache_bus.reset_for_tests()
    reg = CapabilityRegistry(load_fn=lambda: load_plugins(root=fake_plugin_root))
    import execution.ops_platform.capability_registry as crm2
    monkeypatch.setattr(crm2, "_DEFAULT_REGISTRY", reg)
    yield reg
    shared_cache_backend.reset_for_tests()


def test_phase6_substrate_holds_together(registry, monkeypatch):
    # 1. Worker registers
    worker = worker_coordination.register(role="general")
    assert worker_coordination.heartbeat(worker.worker_id)

    # 2. Enqueue + claim + ack
    job = runtime_queue.enqueue(kind="workflow_run",
                                  payload={"capability_id": "x", "inputs": {}},
                                  enqueued_by="alice")
    claimed = runtime_queue.claim(worker_id=worker.worker_id)
    assert claimed is not None
    assert runtime_queue.ack(claimed.job_id, worker_id=worker.worker_id,
                                result={"ok": True})

    # 3. Scheduler event flow
    sched = scheduler.create_schedule(
        name="deploy-trigger", trigger_kind="event",
        capability_id=registry.snapshot().capabilities[0]["id"],
        event_topic="deploy",
    )
    enqueued = scheduler.fire_event("deploy", payload={"version": "1.2.3"})
    assert len(enqueued) == 1

    # 4. Reliability scan + self-heal (no findings expected with fresh state)
    findings = reliability_monitor.scan(registry=registry)
    assert isinstance(findings, list)
    actions = self_healing.run_once(registry=registry)
    assert isinstance(actions, list)

    # 5. Approval lifecycle
    req = approvals.request_approval(
        action="version.promote", entity_type="capability_version",
        entity_id="v-x", requested_by="alice",
        single_approver_roles=["admin"],
    )
    approved = approvals.submit_decision(req.request_id,
                                            approver={"name": "bob", "roles": ["admin"]},
                                            decision="approved")
    assert approved.state == "approved"
    executed = approvals.mark_executed(req.request_id, execution_ref="run-x")
    assert executed.state == "executed"

    # 6. Experiment lifecycle
    cap_id = registry.snapshot().capabilities[0]["id"]
    exp = experiments.create_experiment(
        name="ab", capability_id=cap_id,
        arms=[
            {"arm_id": "c", "label": "Control", "weight": 50},
            {"arm_id": "t", "label": "Treatment", "weight": 50},
        ],
    )
    experiments.transition(exp.experiment_id, to_state="running")
    a1 = experiments.assign(exp.experiment_id, session_id="seed-1")
    a2 = experiments.assign(exp.experiment_id, session_id="seed-1")
    assert a1["assignment"]["arm_id"] == a2["assignment"]["arm_id"]
    out = evaluation.evaluate_experiment(exp.experiment_id)
    assert "arm_kpis" in out

    # 7. Service identity round-trip
    si, token = service_identities.create(display_name="scheduler-svc",
                                              roles=["operator"])
    auth_identity = service_identities.authenticate(token)
    assert auth_identity is not None
    assert auth_identity.user_id == si.service_id
