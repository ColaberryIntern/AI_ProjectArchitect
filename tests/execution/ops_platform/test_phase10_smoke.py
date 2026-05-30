"""Phase 10 smoke — outbox + poison + recovery + backup integrity +
orchestration recovery + load suite all hold together."""

import pytest

from execution.ops_platform import (
    agent_registry, audit_log, backup_integrity, backup_restore, cache_bus,
    chaos_engine, controls, distributed_event_bus, distributed_lock,
    event_fabric, load_test, orchestration_engine, orchestration_recovery,
    orchestration_runtime, poison_handler, projection_engine,
    recovery_coordinator, redis_backends, redis_sentinel, runtime_queue,
    transactional_outbox, worker_coordination, workflow_runner,
)
from execution.ops_platform.capability_registry import CapabilityRegistry
from execution.ops_platform.plugin_loader import load_plugins

from ._fakeredis import FakeRedis


@pytest.fixture
def registry(fake_plugin_root, tmp_path, monkeypatch):
    import execution.ops_platform.capability_registry as crm
    monkeypatch.setattr(crm, "_STATS_PATH", tmp_path / "registry_stats.json")
    monkeypatch.setattr(workflow_runner, "_RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(transactional_outbox, "_OUTBOX_DIR", tmp_path / "outbox")
    monkeypatch.setattr(transactional_outbox, "_DLQ_DIR", tmp_path / "outbox_dlq")
    monkeypatch.setattr(transactional_outbox, "_SEEN_KEYS_DIR",
                          tmp_path / "outbox_dedup")
    monkeypatch.setattr(event_fabric, "_EVENTS_DIR", tmp_path / "fabric")
    monkeypatch.setattr(event_fabric, "_SEQUENCE_PATH", tmp_path / "seq.json")
    monkeypatch.setattr(audit_log, "_AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(poison_handler, "_QUARANTINE_DIR",
                          tmp_path / "poison_quarantine")
    monkeypatch.setattr(poison_handler, "_RETRIES_DIR",
                          tmp_path / "poison_retries")
    monkeypatch.setattr(agent_registry, "_AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr(orchestration_engine, "_ORCH_DIR", tmp_path / "orchestrations")
    monkeypatch.setattr(orchestration_runtime, "_CLAIMS_DIR", tmp_path / "claims")
    monkeypatch.setattr(orchestration_recovery, "_CHECKPOINTS_DIR",
                          tmp_path / "checkpoints")
    monkeypatch.setattr(orchestration_recovery, "_HEARTBEATS_DIR",
                          tmp_path / "heartbeats")
    monkeypatch.setattr(projection_engine, "_PROJECTIONS_DIR", tmp_path / "projections")
    monkeypatch.setattr(distributed_lock, "_LOCKS_DIR", tmp_path / "locks")
    monkeypatch.setattr(chaos_engine, "_CHAOS_DIR", tmp_path / "chaos")
    monkeypatch.setattr(controls, "_CONTROLS_DIR", tmp_path / "controls")
    monkeypatch.setattr(runtime_queue, "_QUEUE_DIR", tmp_path / "queue")
    monkeypatch.setattr(worker_coordination, "_WORKERS_DIR", tmp_path / "workers")
    monkeypatch.setattr(backup_restore, "_BACKUPS_DIR", tmp_path / "backups")
    monkeypatch.setattr(backup_restore, "_OPS_ROOT", tmp_path / "ops_root")
    monkeypatch.setattr(backup_integrity, "_MANIFESTS_DIR",
                          tmp_path / "backup_manifests")
    monkeypatch.setattr(load_test, "_BENCHMARKS_DIR", tmp_path / "load_tests")
    monkeypatch.setattr(cache_bus, "_VERSION_DIR", tmp_path / "versions")
    cache_bus.reset_for_tests()
    event_fabric.reset_for_tests()
    controls._RATE_LIMIT_HITS.clear()
    monkeypatch.setattr(redis_backends, "_REDIS_AVAILABLE", True)
    redis_backends._CLIENT = FakeRedis()
    redis_backends._KEY_PREFIX = "ops:"
    projection_engine._REGISTRY.clear()
    reg = CapabilityRegistry(load_fn=lambda: load_plugins(root=fake_plugin_root))
    import execution.ops_platform.capability_registry as crm2
    monkeypatch.setattr(crm2, "_DEFAULT_REGISTRY", reg)
    yield reg
    redis_backends._CLIENT = None
    projection_engine._REGISTRY.clear()


def test_phase10_substrate_holds_together(registry):
    # 1. Transactional outbox round-trip → fabric
    out_entry = transactional_outbox.enqueue(event_type="smoke.fab",
                                                  payload={"x": 1},
                                                  target="fabric")
    drain = transactional_outbox.drain_once()
    assert drain["published"] >= 1
    metrics = transactional_outbox.metrics()
    assert metrics["total"] >= 1

    # 2. Outbox via Redis stream (FakeRedis client)
    transactional_outbox.enqueue(event_type="smoke.redis",
                                      payload={"y": 2},
                                      target="redis_stream")
    drain2 = transactional_outbox.drain_once()
    assert drain2["published"] >= 1

    # 3. Sentinel state visible
    state = redis_sentinel.current_state()
    assert "redis_role" in state
    assert state["cluster_warnings"] == []  # FakeRedis is not Cluster

    # 4. Poison quarantine + projection skip
    event_fabric.emit("poison.candidate", payload={"bad": True})
    last = event_fabric.replay(limit=1)
    poison_handler.quarantine_event(event_id=last[0].event_id,
                                        reason="malformed",
                                        detected_by="test")
    projection_engine.register_default_projections()
    rebuilt = projection_engine.rebuild("operator_activity")
    assert rebuilt["skipped_poison"] >= 1

    # 5. Recovery coordinator scan + proposal
    recs = recovery_coordinator.scan()
    assert isinstance(recs, list)

    # 6. Backup with manifest + verify
    backup_restore._OPS_ROOT.mkdir(parents=True, exist_ok=True)
    (backup_restore._OPS_ROOT / "smoke.json").write_text('{"k":1}')
    manifest = backup_integrity.snapshot_with_manifest(actor="alice")
    verify = backup_integrity.verify_snapshot(manifest.manifest_id)
    assert verify["verified"] is True

    # 7. Orchestration checkpoint round-trip
    orchestration_recovery.save_checkpoint(
        orchestration_id="smoke-orch", step_id="s1",
        payload={"progress": 0.5},
    )
    loaded = orchestration_recovery.load_checkpoint(orchestration_id="smoke-orch",
                                                        step_id="s1")
    assert loaded.payload["progress"] == 0.5

    # 8. Load test suite runs
    suite = load_test.run_suite()
    assert len(suite) == 4
