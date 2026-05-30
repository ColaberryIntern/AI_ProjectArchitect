"""Phase 9B tests: Redis-backed distributed_event_bus, distributed_lock_v2,
distributed_presence — validated against an in-memory FakeRedis double.

These tests exercise the PROTOCOL. Live cluster validation is operator-driven.
"""

import pytest

from execution.ops_platform import (
    distributed_event_bus, distributed_lock_v2, distributed_presence,
    event_fabric, redis_backends,
)

from ._fakeredis import FakeRedis


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(event_fabric, "_EVENTS_DIR", tmp_path / "fabric")
    monkeypatch.setattr(event_fabric, "_SEQUENCE_PATH", tmp_path / "seq.json")
    event_fabric.reset_for_tests()
    # Wire fake redis client
    monkeypatch.setattr(redis_backends, "_REDIS_AVAILABLE", True)
    fake = FakeRedis()
    redis_backends._CLIENT = fake
    redis_backends._KEY_PREFIX = "ops:"
    yield fake
    redis_backends._CLIENT = None


# ── distributed_event_bus ────────────────────────────────────────────


def test_publish_returns_message_id(isolated):
    ev = event_fabric.emit("test.publish", payload={"k": 1},
                              durability_scope="redis-distributed")
    result = distributed_event_bus.publish(ev)
    assert result.published
    assert result.stream_message_id


def test_publish_without_client_returns_false(isolated):
    redis_backends._CLIENT = None
    ev = event_fabric.emit("nope", durability_scope="single-host")
    result = distributed_event_bus.publish(ev)
    assert result.published is False
    assert "not wired" in result.reason


def test_consume_group_delivers_then_acks(isolated):
    ev = event_fabric.emit("consume.test", payload={"x": 1},
                              durability_scope="redis-distributed")
    distributed_event_bus.publish(ev)
    msgs = distributed_event_bus.consume_group(group="g1", consumer="c1",
                                                    event_types=["consume.test"])
    assert msgs
    # Ack the first
    first = msgs[0]
    assert distributed_event_bus.ack(group="g1",
                                         stream_key=first["stream_key"],
                                         message_id=first["stream_message_id"])


def test_consume_without_client_raises(isolated):
    redis_backends._CLIENT = None
    with pytest.raises(redis_backends.RedisNotConfigured):
        distributed_event_bus.consume_group(group="x", consumer="y")


def test_replay_local_to_redis_drains(isolated):
    # Publish nothing to redis first; just emit local events
    event_fabric.emit("drain.1")
    event_fabric.emit("drain.2")
    result = distributed_event_bus.replay_local_to_redis()
    assert result["pushed"] >= 2


def test_stream_lag_returns_metadata(isolated):
    ev = event_fabric.emit("lag.test", durability_scope="redis-distributed")
    distributed_event_bus.publish(ev)
    # Create a consumer group
    distributed_event_bus.consume_group(group="lag-grp", consumer="c",
                                            event_types=["lag.test"])
    lag = distributed_event_bus.stream_lag(group="lag-grp",
                                                stream_key="ops:fabric:lag.test")
    assert "stream_length" in lag


# ── distributed_lock_v2 ──────────────────────────────────────────────


def test_acquire_returns_fencing_token(isolated):
    lease = distributed_lock_v2.acquire("dlv2.test", lease_seconds=30)
    assert lease.owner_token
    assert lease.fencing_token >= 1


def test_fencing_token_monotonic(isolated):
    l1 = distributed_lock_v2.acquire("dlv2.mono", lease_seconds=30)
    distributed_lock_v2.release("dlv2.mono", owner_token=l1.owner_token)
    l2 = distributed_lock_v2.acquire("dlv2.mono", lease_seconds=30)
    assert l2.fencing_token > l1.fencing_token


def test_second_acquire_blocks(isolated):
    distributed_lock_v2.acquire("dlv2.busy", lease_seconds=30)
    with pytest.raises(distributed_lock_v2.LockBusy):
        distributed_lock_v2.acquire("dlv2.busy", lease_seconds=30,
                                       acquire_timeout_seconds=1)


def test_release_then_reacquire(isolated):
    l1 = distributed_lock_v2.acquire("dlv2.release", lease_seconds=30)
    assert distributed_lock_v2.release("dlv2.release", owner_token=l1.owner_token)
    l2 = distributed_lock_v2.acquire("dlv2.release", lease_seconds=30)
    assert l2.owner_token != l1.owner_token


def test_verify_fencing_token_rejects_stale(isolated):
    l1 = distributed_lock_v2.acquire("dlv2.fence", lease_seconds=30)
    distributed_lock_v2.release("dlv2.fence", owner_token=l1.owner_token)
    l2 = distributed_lock_v2.acquire("dlv2.fence", lease_seconds=30)
    # Stale fencing token (l1) must be rejected
    assert distributed_lock_v2.verify_fencing_token("dlv2.fence",
                                                          l1.fencing_token) is False
    # Current fencing token (l2) accepted
    assert distributed_lock_v2.verify_fencing_token("dlv2.fence",
                                                          l2.fencing_token) is True


def test_release_other_owners_token_returns_false(isolated):
    distributed_lock_v2.acquire("dlv2.steal", lease_seconds=30)
    assert distributed_lock_v2.release("dlv2.steal", owner_token="not-me") is False


def test_lock_v2_without_client_raises(isolated):
    redis_backends._CLIENT = None
    with pytest.raises(redis_backends.RedisNotConfigured):
        distributed_lock_v2.acquire("x")


# ── distributed_presence ─────────────────────────────────────────────


def test_presence_heartbeat_appears_in_active(isolated):
    distributed_presence.heartbeat(workspace_id="sales", user_id="alice",
                                       display_name="Alice")
    rows = distributed_presence.list_active("sales")
    assert any(r.user_id == "alice" for r in rows)


def test_presence_leave_removes(isolated):
    distributed_presence.heartbeat(workspace_id="sales", user_id="bob")
    assert distributed_presence.leave(workspace_id="sales", user_id="bob")


def test_presence_mode_reports_redis(isolated):
    m = distributed_presence.mode()
    assert m["scope"] == "redis-distributed-multi-host"
    assert m["active"] is True


def test_presence_mode_when_no_redis(monkeypatch):
    redis_backends._CLIENT = None
    m = distributed_presence.mode()
    assert m["scope"] == "per-process-only"
    assert m["active"] is False


def test_register_ws_subscriber_shows_in_topology(isolated):
    distributed_presence.register_ws_subscriber(subscriber_id="sub-1",
                                                    host_id="host-A",
                                                    workspace_id="sales")
    topo = distributed_presence.ws_topology()
    assert any(s["subscriber_id"] == "sub-1" for s in topo["subscribers"])


def test_unregister_removes_from_topology(isolated):
    distributed_presence.register_ws_subscriber(subscriber_id="sub-2",
                                                    host_id="host-B")
    distributed_presence.unregister_ws_subscriber(subscriber_id="sub-2",
                                                      host_id="host-B")
    topo = distributed_presence.ws_topology()
    assert not any(s["subscriber_id"] == "sub-2" for s in topo["subscribers"])


def test_presence_heartbeat_requires_client(isolated):
    redis_backends._CLIENT = None
    with pytest.raises(redis_backends.RedisNotConfigured):
        distributed_presence.heartbeat(workspace_id="x", user_id="y")
