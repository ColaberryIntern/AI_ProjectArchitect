"""Phase 10A tests: transactional_outbox + DLQ + replay reconciliation."""

import json
import time
from datetime import datetime, timezone

import pytest

from execution.ops_platform import (
    audit_log, cache_bus, distributed_event_bus, event_fabric,
    notifications, redis_backends, secrets, transactional_outbox,
)

from ._fakeredis import FakeRedis


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(transactional_outbox, "_OUTBOX_DIR", tmp_path / "outbox")
    monkeypatch.setattr(transactional_outbox, "_DLQ_DIR", tmp_path / "outbox_dlq")
    monkeypatch.setattr(transactional_outbox, "_SEEN_KEYS_DIR",
                          tmp_path / "outbox_dedup")
    monkeypatch.setattr(event_fabric, "_EVENTS_DIR", tmp_path / "fabric")
    monkeypatch.setattr(event_fabric, "_SEQUENCE_PATH", tmp_path / "seq.json")
    monkeypatch.setattr(audit_log, "_AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(notifications, "_NOTIF_DIR", tmp_path / "notifications")
    monkeypatch.setattr(notifications, "_CHANNELS_DIR",
                          tmp_path / "notifications" / "channels")
    monkeypatch.setattr(secrets, "_SECRETS_DIR", tmp_path / "secrets")
    monkeypatch.setattr(cache_bus, "_VERSION_DIR", tmp_path / "versions")
    cache_bus.reset_for_tests()
    event_fabric.reset_for_tests()
    redis_backends._CLIENT = None
    yield
    redis_backends._CLIENT = None


def test_enqueue_creates_pending_entry():
    entry = transactional_outbox.enqueue(event_type="test.x",
                                              payload={"a": 1},
                                              target="fabric")
    assert entry.state == "pending"
    assert entry.attempts == 0
    assert entry.idempotency_key


def test_idempotency_dedupes():
    e1 = transactional_outbox.enqueue(event_type="x",
                                          payload={}, target="fabric",
                                          idempotency_key="same-key")
    e2 = transactional_outbox.enqueue(event_type="x",
                                          payload={}, target="fabric",
                                          idempotency_key="same-key")
    assert e1.outbox_id == e2.outbox_id


def test_drain_publishes_to_fabric():
    transactional_outbox.enqueue(event_type="ok.fab", payload={},
                                      target="fabric")
    result = transactional_outbox.drain_once()
    assert result["published"] == 1


def test_drain_to_redis_stream_with_fake_client(monkeypatch):
    monkeypatch.setattr(redis_backends, "_REDIS_AVAILABLE", True)
    redis_backends._CLIENT = FakeRedis()
    redis_backends._KEY_PREFIX = "ops:"
    transactional_outbox.enqueue(event_type="redis.test",
                                      payload={"k": 1},
                                      target="redis_stream")
    result = transactional_outbox.drain_once()
    assert result["published"] == 1


def test_failed_publish_schedules_retry():
    # Target "notification:nonexistent" will fail synchronously
    transactional_outbox.enqueue(event_type="x", payload={},
                                      target="notification:does-not-exist",
                                      max_attempts=2)
    result = transactional_outbox.drain_once()
    assert result["failed"] == 1
    entries = transactional_outbox.list_entries(state="failed")
    assert entries and entries[0].next_attempt_at


def test_dead_letter_after_max_attempts():
    entry = transactional_outbox.enqueue(
        event_type="x", payload={},
        target="notification:does-not-exist",
        max_attempts=1,
    )
    # Force the entry to be due immediately on retry by zeroing
    # next_attempt_at via direct file edit
    transactional_outbox.drain_once()
    # First attempt failed; the entry should now be dead-lettered if
    # max_attempts=1 and we drained once.
    dlq = transactional_outbox.list_dlq()
    assert any(e.outbox_id == entry.outbox_id for e in dlq)


def test_replay_dlq_re_enqueues():
    e = transactional_outbox.enqueue(event_type="x", payload={},
                                          target="notification:nope",
                                          max_attempts=1)
    transactional_outbox.drain_once()
    assert transactional_outbox.list_dlq()
    replayed = transactional_outbox.replay_dlq(e.outbox_id, actor="alice")
    assert replayed is not None
    assert replayed.state == "pending"


def test_metrics_returns_counts():
    transactional_outbox.enqueue(event_type="a", payload={}, target="fabric")
    transactional_outbox.enqueue(event_type="b", payload={}, target="fabric")
    metrics = transactional_outbox.metrics()
    assert metrics["total"] == 2
    assert metrics["by_state"]["pending"] == 2


def test_dedup_helpers():
    consumer = "test-consumer"
    key = "dedup-key-1"
    assert transactional_outbox.is_already_processed(key, consumer=consumer) is False
    transactional_outbox.mark_processed(key, consumer=consumer)
    assert transactional_outbox.is_already_processed(key, consumer=consumer) is True


def test_mark_acknowledged():
    transactional_outbox.enqueue(event_type="ack.test",
                                      payload={}, target="fabric",
                                      idempotency_key="ack-1")
    transactional_outbox.drain_once()
    entry = transactional_outbox.list_entries(state="published")[0]
    acked = transactional_outbox.mark_acknowledged(entry.outbox_id,
                                                        ack_ref="consumer-A")
    assert acked.state == "acknowledged"


def test_reconcile_without_redis_returns_explanation():
    result = transactional_outbox.reconcile_after_outage()
    assert result["reconciled"] is False
    assert "Redis" in result["reason"]


def test_history_tracks_state_transitions():
    entry = transactional_outbox.enqueue(event_type="hist.test",
                                              payload={}, target="fabric",
                                              idempotency_key="hist-1")
    transactional_outbox.drain_once()
    updated = transactional_outbox.get(entry.outbox_id)
    # 'enqueued' at minimum + 'published' after drain
    assert any(h["state"] == "pending" for h in updated.history)
    assert any(h["state"] == "published" for h in updated.history)
