"""Distributed event bus — Redis Streams adapter for the event fabric.

Scope honesty
-------------
- This module REQUIRES a wired Redis client (via ``redis_backends.activate``).
- It does NOT run on Phase 6's file-based primitives. Every operation
  raises ``RedisNotConfigured`` when the client is absent.
- Guarantees declared per operation:
    - ``publish``           → at-least-once delivery via Redis XADD
    - ``consume_group``     → ordered-per-stream within a single consumer
    - ``replay_from``       → bounded by stream MAXLEN retention
- "Exactly-once" is NOT claimed. Idempotency is the consumer's responsibility
  via ``last_delivered_id`` checkpoints.

Stream layout
-------------
- One Redis stream per ``event_type`` family: ``{prefix}fabric:{event_type}``
- One additional global stream ``{prefix}fabric:*`` for fan-in subscribers.
- Stream retention via ``XADD MAXLEN ~ N``; default 100K entries per stream.

Failure semantics
-----------------
- Redis transient failure → ``publish`` returns ``{published: False,
  reason: "..."}`` and the event survives in the local fabric log. Operators
  can drain to Redis later via ``replay_local_to_redis()``.
- Consumer group pending entries are visible via ``pending_entries()`` —
  unacked messages are replayable.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Iterator

from execution.ops_platform import event_fabric, redis_backends

logger = logging.getLogger(__name__)

MAX_STREAM_LEN = 100_000  # approximate cap via XADD MAXLEN ~


@dataclass
class DistributedDeliveryResult:
    published: bool
    stream_key: str | None = None
    stream_message_id: str | None = None
    reason: str | None = None


def publish(event: event_fabric.FabricEvent) -> DistributedDeliveryResult:
    """Append the event to the Redis stream for its type, plus the global
    stream. Returns a result object the caller can inspect."""
    if not (redis_backends.is_available() and redis_backends._CLIENT is not None):
        return DistributedDeliveryResult(
            published=False,
            reason="redis client not wired; event stayed in local fabric log",
        )
    client = redis_backends.get_redis()
    prefix = redis_backends._KEY_PREFIX
    payload = {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "sequence": str(event.sequence),
        "timestamp": event.timestamp,
        "workspace_id": event.workspace_id or "",
        "actor_id": event.actor_id or "",
        "correlation_id": event.correlation_id or "",
        "causation_id": event.causation_id or "",
        "payload_json": json.dumps(event.payload, ensure_ascii=False),
        "evidence_refs_json": json.dumps(event.evidence_refs, ensure_ascii=False),
        "replayable": "1" if event.replayable else "0",
        "durability_scope": event.durability_scope,
        "consistency_scope": event.consistency_scope,
    }
    stream_key = f"{prefix}fabric:{event.event_type}"
    global_key = f"{prefix}fabric:*"
    try:
        message_id = client.xadd(stream_key, payload, maxlen=MAX_STREAM_LEN, approximate=True)
        # Mirror to the global stream so consumer groups can fan in
        client.xadd(global_key, payload, maxlen=MAX_STREAM_LEN, approximate=True)
        if isinstance(message_id, bytes):
            message_id = message_id.decode("utf-8")
        return DistributedDeliveryResult(
            published=True, stream_key=stream_key,
            stream_message_id=message_id,
        )
    except Exception as e:
        return DistributedDeliveryResult(
            published=False, stream_key=stream_key,
            reason=f"redis xadd failed: {str(e)[:160]}",
        )


def consume_group(
    *,
    group: str,
    consumer: str,
    event_types: list | None = None,
    block_ms: int = 1000,
    count: int = 16,
) -> list[dict]:
    """Read up to ``count`` messages via XREADGROUP. Returns a list of dicts
    each shaped like the original FabricEvent payload. Caller acks via
    ``ack(group, message_id)``.

    Guarantee: ordered-per-stream within ``consumer``; at-least-once delivery
    until ``ack()`` is called.
    """
    if not (redis_backends.is_available() and redis_backends._CLIENT is not None):
        raise redis_backends.RedisNotConfigured(
            "consume_group requires a wired Redis client"
        )
    client = redis_backends.get_redis()
    prefix = redis_backends._KEY_PREFIX
    if event_types:
        keys = [f"{prefix}fabric:{t}" for t in event_types]
    else:
        keys = [f"{prefix}fabric:*"]
    streams = {k: ">" for k in keys}
    # Ensure groups exist. Position at "0" (start of stream) so the consumer
    # sees the full replay window — operational semantics demand replay,
    # not tail-only fanout.
    for k in keys:
        try:
            client.xgroup_create(k, group, id="0", mkstream=True)
        except Exception:
            # Already exists; safe to ignore
            pass
    try:
        results = client.xreadgroup(group, consumer, streams,
                                       count=count, block=block_ms)
    except Exception as e:
        logger.warning("xreadgroup failed: %s", e)
        return []
    out: list[dict] = []
    for stream_data in results or []:
        stream_key, messages = stream_data
        if isinstance(stream_key, bytes):
            stream_key = stream_key.decode("utf-8")
        for message_id, fields in messages:
            if isinstance(message_id, bytes):
                message_id = message_id.decode("utf-8")
            row = {k.decode("utf-8") if isinstance(k, bytes) else k:
                     (v.decode("utf-8") if isinstance(v, bytes) else v)
                     for k, v in (fields.items() if isinstance(fields, dict) else fields)}
            out.append({
                "stream_key": stream_key,
                "stream_message_id": message_id,
                "event_id": row.get("event_id"),
                "event_type": row.get("event_type"),
                "sequence": int(row.get("sequence", 0)),
                "timestamp": row.get("timestamp"),
                "workspace_id": row.get("workspace_id") or None,
                "actor_id": row.get("actor_id") or None,
                "correlation_id": row.get("correlation_id") or None,
                "causation_id": row.get("causation_id") or None,
                "payload": json.loads(row.get("payload_json") or "{}"),
                "evidence_refs": json.loads(row.get("evidence_refs_json") or "[]"),
                "replayable": row.get("replayable") == "1",
                "durability_scope": row.get("durability_scope", "redis-distributed"),
                "consistency_scope": row.get("consistency_scope", "at-least-once"),
            })
    return out


def ack(*, group: str, stream_key: str, message_id: str) -> bool:
    """Acknowledge a consumed message so it leaves the pending list."""
    if not (redis_backends.is_available() and redis_backends._CLIENT is not None):
        raise redis_backends.RedisNotConfigured("ack requires a wired Redis client")
    client = redis_backends.get_redis()
    try:
        n = client.xack(stream_key, group, message_id)
        return int(n) > 0
    except Exception:
        return False


def pending_entries(*, group: str, stream_key: str) -> dict:
    """Inspect the pending-entries list for a group — replayable backlog."""
    if not (redis_backends.is_available() and redis_backends._CLIENT is not None):
        raise redis_backends.RedisNotConfigured(
            "pending_entries requires a wired Redis client"
        )
    client = redis_backends.get_redis()
    try:
        info = client.xpending(stream_key, group)
        return {
            "stream_key": stream_key, "group": group,
            "pending_count": int(info.get("pending", 0)) if isinstance(info, dict) else None,
            "raw": info if not isinstance(info, dict) else None,
        }
    except Exception as e:
        return {"stream_key": stream_key, "group": group, "error": str(e)}


def replay_from(*, stream_key: str, start_id: str = "-",
                  end_id: str = "+", count: int = 1000) -> list[dict]:
    """Read past messages without acknowledging — for replay/inspection."""
    if not (redis_backends.is_available() and redis_backends._CLIENT is not None):
        raise redis_backends.RedisNotConfigured("replay_from requires a wired Redis client")
    client = redis_backends.get_redis()
    try:
        results = client.xrange(stream_key, min=start_id, max=end_id, count=count)
    except Exception:
        return []
    out: list[dict] = []
    for message_id, fields in results or []:
        if isinstance(message_id, bytes):
            message_id = message_id.decode("utf-8")
        row = {k.decode("utf-8") if isinstance(k, bytes) else k:
                 (v.decode("utf-8") if isinstance(v, bytes) else v)
                 for k, v in (fields.items() if isinstance(fields, dict) else fields)}
        out.append({"stream_key": stream_key, "stream_message_id": message_id,
                      "row": row})
    return out


def stream_lag(*, group: str, stream_key: str) -> dict:
    """Return {entries_pending, last_delivered_id, length}. Cheap diagnostic
    for the observability page."""
    if not (redis_backends.is_available() and redis_backends._CLIENT is not None):
        raise redis_backends.RedisNotConfigured("stream_lag requires Redis")
    client = redis_backends.get_redis()
    try:
        length = int(client.xlen(stream_key))
    except Exception:
        length = None
    try:
        groups_info = client.xinfo_groups(stream_key)
    except Exception:
        groups_info = []
    last_delivered = None
    pending = None
    for g in groups_info or []:
        name = g.get("name")
        if isinstance(name, bytes):
            name = name.decode("utf-8")
        if name == group:
            pending = g.get("pending")
            last_delivered = g.get("last-delivered-id")
            if isinstance(last_delivered, bytes):
                last_delivered = last_delivered.decode("utf-8")
            break
    return {
        "stream_key": stream_key, "group": group,
        "stream_length": length,
        "pending_entries": pending,
        "last_delivered_id": last_delivered,
    }


def replay_local_to_redis(*, since_sequence: int = 0,
                             event_types: list | None = None) -> dict:
    """Drain local fabric events to Redis (operator-managed convergence
    after a transient Redis outage)."""
    if not (redis_backends.is_available() and redis_backends._CLIENT is not None):
        raise redis_backends.RedisNotConfigured(
            "replay_local_to_redis requires a wired Redis client"
        )
    pushed = 0
    failed = 0
    for ev in event_fabric.replay(since_sequence=since_sequence,
                                      event_types=event_types,
                                      limit=10000):
        r = publish(ev)
        if r.published:
            pushed += 1
        else:
            failed += 1
    return {"pushed": pushed, "failed": failed,
              "since_sequence": since_sequence}
