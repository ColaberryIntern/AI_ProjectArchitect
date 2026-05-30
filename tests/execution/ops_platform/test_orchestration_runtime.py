"""Phase 9D tests: distributed orchestration runtime (claims, lease, fencing)."""

import time

import pytest

from execution.ops_platform import (
    audit_log, cache_bus, distributed_lock, event_fabric,
    orchestration_engine, orchestration_runtime, redis_backends,
    realtime_bus,
)

from ._fakeredis import FakeRedis


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestration_runtime, "_CLAIMS_DIR", tmp_path / "claims")
    monkeypatch.setattr(orchestration_engine, "_ORCH_DIR", tmp_path / "orchestrations")
    monkeypatch.setattr(distributed_lock, "_LOCKS_DIR", tmp_path / "locks")
    monkeypatch.setattr(audit_log, "_AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(event_fabric, "_EVENTS_DIR", tmp_path / "fabric")
    monkeypatch.setattr(event_fabric, "_SEQUENCE_PATH", tmp_path / "seq.json")
    monkeypatch.setattr(realtime_bus, "_EVENTS_DIR", tmp_path / "rt")
    monkeypatch.setattr(realtime_bus, "_SEQUENCE_PATH", tmp_path / "rt_seq.json")
    monkeypatch.setattr(cache_bus, "_VERSION_DIR", tmp_path / "versions")
    cache_bus.reset_for_tests()
    event_fabric.reset_for_tests()
    realtime_bus.reset_for_tests()
    yield


def test_claim_step_file_based(isolated):
    redis_backends._CLIENT = None
    o = orchestration_engine.create_orchestration(
        name="t", steps=[{"step_id": "s1", "kind": "wait", "wait_seconds": 60}],
    )
    claim = orchestration_runtime.claim_step(
        o.orchestration_id, step_id="s1", worker_id="w1",
    )
    assert claim is not None
    assert claim.coordination_scope == "single-host"
    assert claim.fencing_token == 0


def test_claim_step_blocked_by_other_worker(isolated):
    redis_backends._CLIENT = None
    o = orchestration_engine.create_orchestration(
        name="b", steps=[{"step_id": "s1", "kind": "wait", "wait_seconds": 60}],
    )
    claim1 = orchestration_runtime.claim_step(o.orchestration_id, step_id="s1",
                                                  worker_id="w1")
    assert claim1 is not None
    claim2 = orchestration_runtime.claim_step(o.orchestration_id, step_id="s1",
                                                  worker_id="w2")
    assert claim2 is None


def test_release_claim(isolated):
    redis_backends._CLIENT = None
    o = orchestration_engine.create_orchestration(
        name="r", steps=[{"step_id": "s1", "kind": "wait", "wait_seconds": 60}],
    )
    claim = orchestration_runtime.claim_step(o.orchestration_id, step_id="s1",
                                                 worker_id="w1")
    assert orchestration_runtime.release_claim(claim, worker_id="w1")


def test_reclaim_expired_removes_stale(isolated):
    redis_backends._CLIENT = None
    o = orchestration_engine.create_orchestration(
        name="e", steps=[{"step_id": "s1", "kind": "wait", "wait_seconds": 60}],
    )
    claim = orchestration_runtime.claim_step(o.orchestration_id, step_id="s1",
                                                 worker_id="w1", lease_seconds=1)
    # Manually rewrite the claim's lease_until to the past
    import json
    path = orchestration_runtime._CLAIMS_DIR / orchestration_runtime._claim_filename(claim)
    row = json.loads(path.read_text())
    row["lease_until_epoch"] = time.time() - 1
    path.write_text(json.dumps(row))
    expired = orchestration_runtime.reclaim_expired()
    assert any(c.orchestration_id == claim.orchestration_id for c in expired)


def test_coordination_mode_reports_file_based_without_redis(isolated):
    redis_backends._CLIENT = None
    m = orchestration_runtime.coordination_mode()
    assert m["scope"] == "single-host-multi-process"
    assert m["fencing_tokens_enabled"] is False


def test_coordination_mode_reports_redis_when_wired(isolated, monkeypatch):
    monkeypatch.setattr(redis_backends, "_REDIS_AVAILABLE", True)
    redis_backends._CLIENT = FakeRedis()
    m = orchestration_runtime.coordination_mode()
    assert m["scope"] == "redis-distributed-multi-host"
    assert m["fencing_tokens_enabled"] is True


def test_claim_uses_redis_v2_when_available(isolated, monkeypatch):
    monkeypatch.setattr(redis_backends, "_REDIS_AVAILABLE", True)
    redis_backends._CLIENT = FakeRedis()
    o = orchestration_engine.create_orchestration(
        name="rd", steps=[{"step_id": "s1", "kind": "wait", "wait_seconds": 60}],
    )
    claim = orchestration_runtime.claim_step(o.orchestration_id, step_id="s1",
                                                 worker_id="w1")
    assert claim is not None
    assert claim.coordination_scope == "redis-distributed"
    assert claim.fencing_token >= 1


def test_stuck_orchestrations_identifies_old_state(isolated):
    redis_backends._CLIENT = None
    o = orchestration_engine.create_orchestration(
        name="stuck", steps=[{"step_id": "s1", "kind": "wait", "wait_seconds": 60}],
    )
    # Backdate updated_at
    import json
    path = orchestration_engine._ORCH_DIR / f"{o.orchestration_id}.json"
    row = json.loads(path.read_text())
    row["updated_at"] = "2020-01-01T00:00:00+00:00"
    row["state"] = "running"
    path.write_text(json.dumps(row))
    out = orchestration_runtime.stuck_orchestrations(age_minutes=30)
    assert any(s["orchestration_id"] == o.orchestration_id for s in out)
