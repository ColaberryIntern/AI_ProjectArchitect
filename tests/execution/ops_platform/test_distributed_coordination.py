"""Phase 6A: distributed_lock + runtime_queue + worker_coordination +
distributed_rate_limit + shared_cache_backend tests."""

import time
from unittest.mock import patch

import pytest

from execution.ops_platform import (
    audit_log, cache_bus, distributed_lock, distributed_rate_limit,
    runtime_queue, shared_cache_backend, worker_coordination,
)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(distributed_lock, "_LOCKS_DIR", tmp_path / "locks")
    monkeypatch.setattr(runtime_queue, "_QUEUE_DIR", tmp_path / "queue")
    monkeypatch.setattr(worker_coordination, "_WORKERS_DIR", tmp_path / "workers")
    monkeypatch.setattr(distributed_rate_limit, "_RATE_DIR", tmp_path / "rates")
    monkeypatch.setattr(distributed_rate_limit, "_POLICIES_PATH",
                          tmp_path / "rates" / "_policies.json")
    monkeypatch.setattr(audit_log, "_AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(cache_bus, "_VERSION_DIR", tmp_path / "versions")
    shared_cache_backend.configure(shared_cache_backend.FileBackend(root=tmp_path / "versions"))
    cache_bus.reset_for_tests()
    yield
    shared_cache_backend.reset_for_tests()


# ── distributed_lock ──────────────────────────────────────────────────


def test_acquire_and_release():
    rec = distributed_lock.acquire("test", owner_id="owner-1")
    assert rec.owner_id == "owner-1"
    assert distributed_lock.release("test", owner_id="owner-1") is True
    assert distributed_lock.is_held("test") is None


def test_double_acquire_by_different_owners_raises():
    distributed_lock.acquire("dup", owner_id="owner-A")
    with pytest.raises(distributed_lock.LockAcquisitionError):
        distributed_lock.acquire("dup", owner_id="owner-B", acquire_timeout_seconds=1)


def test_reentrant_same_owner_succeeds():
    rec1 = distributed_lock.acquire("re", owner_id="owner-R", lease_seconds=30)
    rec2 = distributed_lock.acquire("re", owner_id="owner-R", lease_seconds=30)
    assert rec2.owner_id == rec1.owner_id


def test_expired_lock_can_be_reclaimed():
    distributed_lock.acquire("exp", owner_id="dead", lease_seconds=1)
    time.sleep(1.1)
    rec = distributed_lock.acquire("exp", owner_id="alive")
    assert rec.owner_id == "alive"
    rows = audit_log.list_entries(action="lock.reclaimed")
    assert rows


def test_heartbeat_extends_lease():
    distributed_lock.acquire("hb", owner_id="owner-H", lease_seconds=60)
    rec = distributed_lock.heartbeat("hb", owner_id="owner-H")
    assert rec.heartbeats >= 1


def test_held_context_manager_releases():
    with distributed_lock.held("ctx", owner_id="owner-C"):
        assert distributed_lock.is_held("ctx") is not None
    assert distributed_lock.is_held("ctx") is None


# ── runtime_queue ──────────────────────────────────────────────────────


def test_enqueue_then_claim_then_ack():
    job = runtime_queue.enqueue(kind="workflow_run", payload={"cap": "x"})
    claimed = runtime_queue.claim(worker_id="w1")
    assert claimed is not None
    assert claimed.job_id == job.job_id
    assert runtime_queue.ack(job.job_id, worker_id="w1", result={"ok": True})


def test_nack_requeues_under_max_attempts():
    job = runtime_queue.enqueue(kind="workflow_run", payload={}, max_attempts=2)
    runtime_queue.claim(worker_id="w1")
    runtime_queue.nack(job.job_id, worker_id="w1", error="first try")
    refreshed = runtime_queue.get_job(job.job_id)
    assert refreshed.status == "pending"
    assert refreshed.attempts == 1


def test_nack_after_max_attempts_dead_letters():
    job = runtime_queue.enqueue(kind="workflow_run", payload={}, max_attempts=1)
    runtime_queue.claim(worker_id="w1")
    runtime_queue.nack(job.job_id, worker_id="w1", error="boom")
    refreshed = runtime_queue.get_job(job.job_id)
    assert refreshed.status == "dead_letter"


def test_idempotency_key_dedupes():
    j1 = runtime_queue.enqueue(kind="workflow_run", payload={}, idempotency_key="abc")
    j2 = runtime_queue.enqueue(kind="workflow_run", payload={}, idempotency_key="abc")
    assert j1.job_id == j2.job_id


def test_priority_is_honored():
    runtime_queue.enqueue(kind="workflow_run", payload={"a": 1}, priority=0)
    runtime_queue.enqueue(kind="workflow_run", payload={"a": 2}, priority=10)
    high = runtime_queue.claim(worker_id="w")
    assert high.priority == 10


def test_reclaim_stale_re_pends_expired_claims():
    job = runtime_queue.enqueue(kind="workflow_run", payload={})
    claimed = runtime_queue.claim(worker_id="w1", lease_seconds=0)
    assert claimed is not None
    time.sleep(0.5)
    reclaimed = runtime_queue.reclaim_stale()
    assert job.job_id in reclaimed
    refreshed = runtime_queue.get_job(job.job_id)
    assert refreshed.status == "pending"


def test_queue_depth():
    runtime_queue.enqueue(kind="workflow_run", payload={})
    runtime_queue.enqueue(kind="workflow_run", payload={})
    depth = runtime_queue.queue_depth()
    assert depth["total"] >= 2


def test_cancel_terminal():
    job = runtime_queue.enqueue(kind="workflow_run", payload={})
    runtime_queue.cancel(job.job_id, actor="alice")
    refreshed = runtime_queue.get_job(job.job_id)
    assert refreshed.status == "cancelled"


# ── worker_coordination ───────────────────────────────────────────────


def test_register_and_heartbeat():
    w = worker_coordination.register(role="general")
    assert worker_coordination.heartbeat(w.worker_id) is True


def test_evict_stale_removes_silent_workers(monkeypatch):
    w = worker_coordination.register()
    # Force last_heartbeat_at into the past
    import json
    path = worker_coordination._WORKERS_DIR / f"{w.worker_id}.json"
    row = json.loads(path.read_text())
    row["last_heartbeat_at"] = "2020-01-01T00:00:00+00:00"
    path.write_text(json.dumps(row))
    evicted = worker_coordination.evict_stale()
    assert w.worker_id in evicted


def test_leader_election_grants_one_owner():
    assert worker_coordination.acquire_leadership("w1") is True
    assert worker_coordination.acquire_leadership("w2") is False


# ── distributed_rate_limit ────────────────────────────────────────────


def test_set_and_check_rate_limit():
    distributed_rate_limit.set_policy(kind="user", max_calls=3, window_seconds=60)
    for _ in range(3):
        d = distributed_rate_limit.check_and_increment(kind="user", identifier="alice")
        assert d.allowed
    d = distributed_rate_limit.check_and_increment(kind="user", identifier="alice")
    assert not d.allowed
    assert d.retry_after_seconds > 0


def test_rate_limit_per_identifier_isolated():
    distributed_rate_limit.set_policy(kind="user", max_calls=1, window_seconds=60)
    a = distributed_rate_limit.check_and_increment(kind="user", identifier="alice")
    b = distributed_rate_limit.check_and_increment(kind="user", identifier="bob")
    assert a.allowed and b.allowed


# ── shared_cache_backend ──────────────────────────────────────────────


def test_file_backend_sets_and_reads_mtime(tmp_path):
    backend = shared_cache_backend.FileBackend(root=tmp_path / "vers")
    v1 = backend.set_version("topic_x")
    v2 = backend.get_version("topic_x")
    assert v2 == pytest.approx(v1, abs=0.1)


def test_in_memory_backend_isolated_per_instance():
    a = shared_cache_backend.InMemoryBackend()
    b = shared_cache_backend.InMemoryBackend()
    a.set_version("t")
    assert b.get_version("t") == 0.0


def test_redis_backend_requires_client():
    with pytest.raises(NotImplementedError):
        shared_cache_backend.RedisBackend(None)
