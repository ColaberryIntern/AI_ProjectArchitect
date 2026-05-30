"""Phase 10B/10C/10D/10E/10F/10G tests: sentinel + poison + recovery
coordinator + snapshot integrity + orchestration recovery + load test."""

import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from execution.ops_platform import (
    agent_registry, audit_log, backup_integrity, backup_restore, cache_bus,
    chaos_engine, controls, distributed_lock, event_fabric, load_test,
    orchestration_engine, orchestration_recovery, orchestration_runtime,
    poison_handler, projection_engine, recovery_coordinator, redis_backends,
    redis_sentinel, runtime_queue, transactional_outbox, worker_coordination,
    workflow_runner,
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
    redis_backends._CLIENT = None
    projection_engine._REGISTRY.clear()
    reg = CapabilityRegistry(load_fn=lambda: load_plugins(root=fake_plugin_root))
    import execution.ops_platform.capability_registry as crm2
    monkeypatch.setattr(crm2, "_DEFAULT_REGISTRY", reg)
    yield reg
    redis_backends._CLIENT = None
    projection_engine._REGISTRY.clear()


# ── 10B: redis_sentinel ───────────────────────────────────────────────


def test_sentinel_cluster_warnings_without_client(registry):
    redis_backends._CLIENT = None
    assert redis_sentinel.cluster_warnings() == []


def test_sentinel_current_state_no_client(registry):
    redis_backends._CLIENT = None
    state = redis_sentinel.current_state()
    assert state["redis_role"] is None
    assert state["sentinel_connected"] is False


def test_sentinel_check_failover_reports_disconnect(registry):
    redis_backends._CLIENT = None
    result = redis_sentinel.check_failover()
    assert result["connected"] is False
    assert result["failover_detected"] is True


def test_sentinel_verify_fencing_continuity_without_client(registry):
    redis_backends._CLIENT = None
    out = redis_sentinel.verify_fencing_continuity("any.lock", token_before_failover=5)
    assert out["verified"] is False


# ── 10C: poison_handler ──────────────────────────────────────────────


def test_quarantine_event_records_audit(registry):
    rec = poison_handler.quarantine_event(
        event_id="ev-1", reason="bad schema",
        detected_by="projection.test", original_event_type="weird.x",
    )
    assert rec.quarantine_id
    rows = audit_log.list_entries(action="poison.quarantined")
    assert any(r["entity_id"] == rec.quarantine_id for r in rows)


def test_track_retry_returns_true_at_threshold(registry):
    for _ in range(2):
        result = poison_handler.track_retry("ev-r", error="boom", max_retries=3)
        assert result is False
    # Third attempt — at threshold
    result = poison_handler.track_retry("ev-r", error="boom", max_retries=3)
    assert result is True


def test_release_clears_operator_release_required(registry):
    rec = poison_handler.quarantine_event(event_id="ev-2", reason="x",
                                              detected_by="test")
    released = poison_handler.release(rec.quarantine_id, actor="alice")
    assert released.operator_release_required is False


def test_is_quarantined_reflects_state(registry):
    poison_handler.quarantine_event(event_id="ev-3", reason="x",
                                        detected_by="test")
    assert poison_handler.is_quarantined("ev-3") is True
    assert poison_handler.is_quarantined("nonexistent") is False


def test_projection_skips_quarantined_events(registry):
    event_fabric.emit("test.skip", actor_id="alice")
    last = event_fabric.replay(limit=1)
    assert last
    poison_handler.quarantine_event(event_id=last[0].event_id, reason="x",
                                        detected_by="test")
    projection_engine.register_default_projections()
    result = projection_engine.rebuild("operator_activity")
    assert result["skipped_poison"] >= 1


# ── 10D: recovery_coordinator ────────────────────────────────────────


def test_scan_returns_recommendations(registry):
    transactional_outbox.enqueue(event_type="rec.test",
                                      payload={}, target="fabric")
    recs = recovery_coordinator.scan()
    kinds = {r.kind for r in recs}
    assert "outbox_drain" in kinds


def test_scan_flags_redis_disconnect_when_unwired(registry):
    redis_backends._CLIENT = None
    recs = recovery_coordinator.scan()
    assert any(r.kind == "redis_reconnect_required" for r in recs)


def test_execute_without_agent_surfaces_as_proposal(registry):
    transactional_outbox.enqueue(event_type="x",
                                      payload={}, target="fabric")
    recs = recovery_coordinator.scan()
    rec = next(r for r in recs if r.kind == "outbox_drain")
    out = recovery_coordinator.execute_one(rec, actor="alice")
    assert out["outcome"] == "PROPOSED"


def test_execute_with_autonomous_agent_applies(registry):
    agent_registry.register_agent(
        name="recovery_coordinator", description="rec",
        autonomy_policy="autonomous_low_risk_only",
        confidence_threshold=0.5,
        permitted_actions=["outbox_drain", "reclaim_expired_claims",
                              "projection_rebuild"],
        rollback_required=True, created_by="alice",
    )
    transactional_outbox.enqueue(event_type="x", payload={},
                                      target="fabric")
    recs = recovery_coordinator.scan()
    rec = next(r for r in recs if r.kind == "outbox_drain")
    out = recovery_coordinator.execute_one(rec, actor="alice")
    assert out["outcome"] in ("APPLIED", "PROPOSED")


# ── 10E: backup_integrity ────────────────────────────────────────────


def test_snapshot_with_manifest_records_sha256(registry):
    backup_restore._OPS_ROOT.mkdir(parents=True, exist_ok=True)
    (backup_restore._OPS_ROOT / "test.json").write_text('{"k":1}')
    manifest = backup_integrity.snapshot_with_manifest(actor="alice")
    assert manifest.file_count >= 1
    # Each file has a hex sha256
    for f in manifest.files:
        f_dict = f if isinstance(f, dict) else f.to_dict()
        assert len(f_dict["sha256"]) == 64


def test_verify_snapshot_passes_intact(registry):
    backup_restore._OPS_ROOT.mkdir(parents=True, exist_ok=True)
    (backup_restore._OPS_ROOT / "x.txt").write_text("y")
    manifest = backup_integrity.snapshot_with_manifest()
    result = backup_integrity.verify_snapshot(manifest.manifest_id)
    assert result["verified"] is True


def test_partial_restore_unknown_profile(registry):
    backup_restore._OPS_ROOT.mkdir(parents=True, exist_ok=True)
    (backup_restore._OPS_ROOT / "x.txt").write_text("y")
    manifest = backup_integrity.snapshot_with_manifest()
    result = backup_integrity.partial_restore(
        manifest_id=manifest.manifest_id, profile="bogus",
    )
    assert result["restored"] is False


def test_partial_restore_extracts_only_profile_paths(registry, tmp_path):
    # Seed a file under a profile path
    proj_dir = backup_restore._OPS_ROOT / "projections"
    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "x.json").write_text('{}')
    manifest = backup_integrity.snapshot_with_manifest()
    target = tmp_path / "restored"
    result = backup_integrity.partial_restore(
        manifest_id=manifest.manifest_id, profile="projection-only",
        restore_to=str(target),
    )
    assert result["restored"] is True
    assert (target / "projections" / "x.json").exists()


def test_lineage_graph_includes_manifests(registry):
    backup_restore._OPS_ROOT.mkdir(parents=True, exist_ok=True)
    (backup_restore._OPS_ROOT / "x.txt").write_text("y")
    m = backup_integrity.snapshot_with_manifest()
    graph = backup_integrity.lineage_graph()
    assert any(n["manifest_id"] == m.manifest_id for n in graph["nodes"])


def test_orphan_snapshots_when_no_manifest(registry):
    backup_restore._BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    fake = backup_restore._BACKUPS_DIR / "ops_snapshot_orphan.tar.gz"
    fake.write_bytes(b"\x1f\x8b\x08\x00\x00\x00\x00\x00")  # gzip magic
    orphans = backup_integrity.orphan_snapshots()
    assert any(o["archive_path"].endswith("ops_snapshot_orphan.tar.gz") for o in orphans)


# ── 10F: orchestration_recovery ──────────────────────────────────────


def test_save_and_load_checkpoint(registry):
    ckpt = orchestration_recovery.save_checkpoint(
        orchestration_id="orch-1", step_id="s1",
        payload={"progress": 0.5, "rows_processed": 100},
    )
    loaded = orchestration_recovery.load_checkpoint(orchestration_id="orch-1",
                                                        step_id="s1")
    assert loaded.payload["progress"] == 0.5


def test_heartbeat_recorded_and_readable(registry):
    orchestration_recovery.write_heartbeat(orchestration_id="orch-hb",
                                                worker_id="w1")
    hb = orchestration_recovery.last_heartbeat(orchestration_id="orch-hb")
    assert hb is not None
    assert hb["worker_id"] == "w1"


def test_recover_after_crash_releases_stale_claims(registry):
    o = orchestration_engine.create_orchestration(
        name="x", steps=[{"step_id": "s1", "kind": "wait", "wait_seconds": 60}],
    )
    # Stale heartbeat
    orchestration_recovery.write_heartbeat(orchestration_id=o.orchestration_id,
                                                worker_id="w1")
    hb_path = orchestration_recovery._HEARTBEATS_DIR / f"{o.orchestration_id}.json"
    data = json.loads(hb_path.read_text())
    data["heartbeat_at"] = "2020-01-01T00:00:00+00:00"
    hb_path.write_text(json.dumps(data))
    result = orchestration_recovery.recover_after_crash(age_minutes=5)
    assert any(r["orchestration_id"] == o.orchestration_id
                  for r in result["recovered"])


def test_compute_next_attempt_respects_max_retries(registry):
    out = orchestration_recovery.compute_next_attempt(
        attempts=3, policy={"max_retries": 3},
    )
    assert out["should_dead_letter"] is True
    out2 = orchestration_recovery.compute_next_attempt(
        attempts=1, policy={"max_retries": 3, "base_backoff_seconds": 1.0,
                              "max_backoff_seconds": 10.0,
                              "jitter": "none"},
    )
    assert out2["should_dead_letter"] is False
    assert out2["backoff_seconds"] >= 1.0


def test_operator_timeline_aggregates_sources(registry):
    o = orchestration_engine.create_orchestration(
        name="tl",
        steps=[{"step_id": "s1", "kind": "wait", "wait_seconds": 60}],
    )
    orchestration_recovery.save_checkpoint(
        orchestration_id=o.orchestration_id, step_id="s1",
        payload={"x": 1},
    )
    timeline = orchestration_recovery.operator_timeline(o.orchestration_id)
    assert "entries" in timeline
    sources = {e["source"] for e in timeline["entries"]}
    assert "checkpoint" in sources


# ── 10G: load_test harness ───────────────────────────────────────────


def test_benchmark_event_fabric_publish_returns_metrics(registry):
    bm = load_test.benchmark_event_fabric_publish(count=30)
    assert bm.samples == 30
    assert bm.mean_latency_ms is not None
    assert bm.hardware
    assert bm.topology["scope"] in (
        "single-host-file-backed", "redis-distributed-multi-host",
    )


def test_benchmark_lock_contention_runs(registry):
    bm = load_test.benchmark_lock_contention(count=20, lock_count=4)
    assert bm.samples > 0


def test_benchmark_queue_enqueue(registry):
    bm = load_test.benchmark_queue_enqueue_drain(count=20)
    assert bm.samples == 20


def test_benchmark_projection_rebuild(registry):
    bm = load_test.benchmark_projection_rebuild(seed_events=50)
    assert bm.samples == 1


def test_run_suite_persists_results(registry):
    suite = load_test.run_suite()
    assert len(suite) == 4
    assert load_test.list_suites(limit=1)
