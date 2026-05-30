"""Transactional outbox — durable append-before-publish for the event fabric.

Scope honesty
-------------
- This module provides **effectively-once** semantics for idempotent
  consumers. It does NOT make events exactly-once — every consumer must
  honor ``idempotency_key`` deduplication on its own.
- Coordination scope: **single-host multi-process** for the outbox state
  itself (file-locked rows). Cross-host fanout still requires
  ``distributed_event_bus`` + a wired Redis client.

State machine
-------------
   pending → published      → acknowledged
          → failed (×attempts) → dead_letter

  - ``pending``:      the event is persisted locally but not yet pushed downstream
  - ``published``:    the downstream system accepted it
  - ``acknowledged``: a consumer has acked it (only for streams with consumer groups)
  - ``failed``:       last attempt failed; will retry with exponential backoff
  - ``dead_letter``:  max retries exhausted; operator review required

Persistence
-----------
``output/ops_platform/outbox/{outbox_id}.json``        — one row per outbox entry
``output/ops_platform/outbox_dlq/{outbox_id}.json``    — dead-letter copies
"""

from __future__ import annotations

import json
import logging
import random
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution.ops_platform import audit_log, distributed_event_bus, event_fabric, redis_backends

logger = logging.getLogger(__name__)

_OUTBOX_DIR = OUTPUT_DIR / "ops_platform" / "outbox"
_DLQ_DIR = OUTPUT_DIR / "ops_platform" / "outbox_dlq"

DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_BASE_BACKOFF_SECONDS = 2.0
DEFAULT_MAX_BACKOFF_SECONDS = 300.0


@dataclass
class OutboxEntry:
    outbox_id: str
    idempotency_key: str
    event_type: str
    payload: dict
    correlation_id: str | None
    state: str                          # pending | published | acknowledged | failed | dead_letter
    attempts: int
    max_attempts: int
    next_attempt_at: str | None
    last_error: str | None
    created_at: str
    updated_at: str
    last_attempt_at: str | None
    target: str                          # "fabric" | "redis_stream" | "notification:<channel_id>"
    target_message_id: str | None
    history: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ── Public API ─────────────────────────────────────────────────────────


def enqueue(
    *,
    event_type: str,
    payload: dict,
    target: str = "redis_stream",
    idempotency_key: str | None = None,
    correlation_id: str | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> OutboxEntry:
    """Append-before-publish: persist the intent BEFORE attempting downstream.
    Idempotent on ``idempotency_key`` — re-enqueueing the same key returns
    the existing entry instead of creating a duplicate row.
    """
    if not idempotency_key:
        idempotency_key = uuid.uuid4().hex

    existing = _find_by_idempotency_key(idempotency_key)
    if existing is not None:
        return existing

    now_iso = datetime.now(timezone.utc).isoformat()
    entry = OutboxEntry(
        outbox_id=f"obx_{uuid.uuid4().hex[:12]}",
        idempotency_key=idempotency_key,
        event_type=event_type,
        payload=dict(payload),
        correlation_id=correlation_id,
        state="pending",
        attempts=0,
        max_attempts=max(1, int(max_attempts)),
        next_attempt_at=now_iso,
        last_error=None,
        created_at=now_iso,
        updated_at=now_iso,
        last_attempt_at=None,
        target=target,
        target_message_id=None,
        history=[{"at": now_iso, "state": "pending", "note": "enqueued"}],
    )
    _persist(entry)
    audit_log.record(
        action="outbox.enqueued", entity_type="outbox_entry",
        entity_id=entry.outbox_id,
        actor={"name": "transactional_outbox", "system": True},
        correlation_id=correlation_id,
        new_state={"event_type": event_type, "target": target,
                   "idempotency_key": idempotency_key},
    )
    return entry


def drain_once(*, max_batch: int = 25) -> dict:
    """Process up to ``max_batch`` due entries. Returns counts by outcome."""
    now = datetime.now(timezone.utc)
    pushed = failed = dead = skipped = 0
    for entry in _list(state_in=("pending", "failed"), limit=max_batch * 4):
        if entry.next_attempt_at:
            try:
                due = datetime.fromisoformat(entry.next_attempt_at)
            except ValueError:
                due = now
            if due > now:
                skipped += 1
                continue
        result = _try_publish(entry)
        if result == "published":
            pushed += 1
        elif result == "dead_letter":
            dead += 1
        else:
            failed += 1
        if pushed + failed + dead >= max_batch:
            break
    return {"published": pushed, "failed": failed, "dead_letter": dead,
              "skipped": skipped}


def mark_acknowledged(outbox_id: str, *, ack_ref: str | None = None) -> OutboxEntry | None:
    entry = get(outbox_id)
    if entry is None or entry.state != "published":
        return entry
    entry.state = "acknowledged"
    entry.updated_at = datetime.now(timezone.utc).isoformat()
    entry.history.append({"at": entry.updated_at, "state": "acknowledged",
                            "note": f"ack_ref={ack_ref or ''}"})
    _persist(entry)
    audit_log.record(
        action="outbox.acknowledged", entity_type="outbox_entry",
        entity_id=outbox_id,
        actor={"name": "transactional_outbox", "system": True},
        correlation_id=entry.correlation_id,
        metadata={"ack_ref": ack_ref},
    )
    return entry


def replay_dlq(outbox_id: str, *, actor: dict | str = "anonymous") -> OutboxEntry | None:
    """Move a DLQ entry back to pending for a fresh attempt. Operator-driven."""
    path = _DLQ_DIR / f"{outbox_id}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entry = OutboxEntry(**data)
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    entry.state = "pending"
    entry.attempts = 0
    entry.last_error = None
    entry.next_attempt_at = datetime.now(timezone.utc).isoformat()
    entry.updated_at = entry.next_attempt_at
    entry.history.append({"at": entry.updated_at, "state": "pending",
                            "note": "replayed from DLQ"})
    _persist(entry)
    try:
        path.unlink()
    except OSError:
        pass
    audit_log.record(
        action="outbox.replayed_from_dlq", entity_type="outbox_entry",
        entity_id=outbox_id,
        actor=actor if isinstance(actor, dict) else {"name": str(actor)},
        correlation_id=entry.correlation_id,
    )
    return entry


def reconcile_after_outage() -> dict:
    """After a Redis outage, sweep the outbox for events that should have
    landed on Redis streams but didn't. Drains them. Returns the outcome
    counts so the operator can review."""
    if not (redis_backends.is_available() and redis_backends._CLIENT is not None):
        return {"reconciled": False,
                  "reason": "Redis not wired; reconciliation requires a live client"}
    result = drain_once(max_batch=100)
    audit_log.record(
        action="outbox.reconciliation_run", entity_type="outbox",
        entity_id="reconcile",
        actor={"name": "outbox_reconciler", "system": True},
        metadata=result,
    )
    return {"reconciled": True, **result}


def get(outbox_id: str) -> OutboxEntry | None:
    path = _OUTBOX_DIR / f"{outbox_id}.json"
    if not path.exists():
        return None
    try:
        return OutboxEntry(**json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def list_entries(*, state: str | None = None, limit: int = 100) -> list[OutboxEntry]:
    out = _list(state_in=(state,) if state else None, limit=limit)
    return out


def list_dlq(*, limit: int = 100) -> list[OutboxEntry]:
    if not _DLQ_DIR.exists():
        return []
    out: list[OutboxEntry] = []
    for p in sorted(_DLQ_DIR.glob("obx_*.json"), reverse=True)[:limit]:
        try:
            out.append(OutboxEntry(**json.loads(p.read_text(encoding="utf-8"))))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    return out


def metrics() -> dict:
    """Cheap dashboard read — counts by state + DLQ + oldest-pending age."""
    from collections import Counter
    counts: Counter = Counter()
    oldest_pending_seconds: float | None = None
    now = datetime.now(timezone.utc)
    for entry in _list(state_in=None, limit=10000):
        counts[entry.state] += 1
        if entry.state in ("pending", "failed"):
            try:
                created = datetime.fromisoformat(entry.created_at)
                age = (now - created).total_seconds()
                if oldest_pending_seconds is None or age > oldest_pending_seconds:
                    oldest_pending_seconds = age
            except ValueError:
                continue
    dlq_count = sum(1 for _ in (_DLQ_DIR.glob("obx_*.json") if _DLQ_DIR.exists() else []))
    return {
        "total": sum(counts.values()),
        "by_state": dict(counts),
        "dead_letter_count": dlq_count,
        "oldest_pending_age_seconds": (round(oldest_pending_seconds, 1)
                                         if oldest_pending_seconds is not None else None),
    }


# ── Consumer helper: dedup by idempotency_key ─────────────────────────


_SEEN_KEYS_DIR = OUTPUT_DIR / "ops_platform" / "outbox_dedup"


def is_already_processed(idempotency_key: str, *, consumer: str) -> bool:
    """Check before processing a message — returns True if this consumer
    has already handled this idempotency_key."""
    path = _SEEN_KEYS_DIR / consumer / f"{idempotency_key}.seen"
    return path.exists()


def mark_processed(idempotency_key: str, *, consumer: str) -> None:
    target = _SEEN_KEYS_DIR / consumer
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{idempotency_key}.seen").write_text(
        datetime.now(timezone.utc).isoformat(), encoding="utf-8",
    )


# ── Internal ───────────────────────────────────────────────────────────


def _try_publish(entry: OutboxEntry) -> str:
    """Attempt one publish. Returns 'published' | 'failed' | 'dead_letter'."""
    entry.attempts += 1
    entry.last_attempt_at = datetime.now(timezone.utc).isoformat()
    try:
        if entry.target == "fabric":
            ev = event_fabric.emit(
                entry.event_type, payload=entry.payload,
                correlation_id=entry.correlation_id,
                durability_scope="single-host", consistency_scope="at-least-once",
            )
            entry.target_message_id = str(ev.sequence)
            entry.state = "published"
        elif entry.target == "redis_stream":
            ev = event_fabric.emit(
                entry.event_type, payload=entry.payload,
                correlation_id=entry.correlation_id,
                durability_scope="redis-distributed",
                consistency_scope="at-least-once",
            )
            result = distributed_event_bus.publish(ev)
            if not result.published:
                raise RuntimeError(result.reason or "redis publish failed")
            entry.target_message_id = result.stream_message_id
            entry.state = "published"
        elif entry.target.startswith("notification:"):
            from execution.ops_platform import notifications
            channel_id = entry.target.split(":", 1)[1]
            rec = notifications.send(channel_id, title=entry.event_type,
                                        body=json.dumps(entry.payload, ensure_ascii=False),
                                        correlation_id=entry.correlation_id)
            if not rec.success:
                raise RuntimeError(rec.error or "notification send failed")
            entry.target_message_id = rec.delivery_id
            entry.state = "published"
        else:
            raise RuntimeError(f"unknown target '{entry.target}'")
    except Exception as e:
        entry.last_error = str(e)[:280]
        if entry.attempts >= entry.max_attempts:
            entry.state = "dead_letter"
            entry.history.append({"at": entry.last_attempt_at,
                                    "state": "dead_letter",
                                    "note": f"exhausted retries: {entry.last_error[:100]}"})
            _to_dlq(entry)
            audit_log.record(
                action="outbox.dead_letter", entity_type="outbox_entry",
                entity_id=entry.outbox_id,
                actor={"name": "transactional_outbox", "system": True},
                correlation_id=entry.correlation_id,
                metadata={"attempts": entry.attempts, "last_error": entry.last_error},
            )
            return "dead_letter"
        # Schedule next attempt with exponential backoff + jitter
        backoff = min(DEFAULT_MAX_BACKOFF_SECONDS,
                        DEFAULT_BASE_BACKOFF_SECONDS * (2 ** (entry.attempts - 1)))
        backoff *= (1 + random.uniform(-0.2, 0.2))
        entry.next_attempt_at = (
            datetime.now(timezone.utc) + timedelta(seconds=backoff)
        ).isoformat()
        entry.state = "failed"
        entry.history.append({"at": entry.last_attempt_at, "state": "failed",
                                "note": f"attempt {entry.attempts} failed; "
                                          f"retry in ~{int(backoff)}s"})
        _persist(entry)
        return "failed"

    entry.updated_at = datetime.now(timezone.utc).isoformat()
    entry.next_attempt_at = None
    entry.history.append({"at": entry.updated_at, "state": "published",
                            "note": f"target_message_id={entry.target_message_id}"})
    _persist(entry)
    audit_log.record(
        action="outbox.published", entity_type="outbox_entry",
        entity_id=entry.outbox_id,
        actor={"name": "transactional_outbox", "system": True},
        correlation_id=entry.correlation_id,
        metadata={"target": entry.target,
                  "target_message_id": entry.target_message_id},
    )
    return "published"


def _persist(entry: OutboxEntry) -> None:
    _OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
    (_OUTBOX_DIR / f"{entry.outbox_id}.json").write_text(
        json.dumps(entry.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _to_dlq(entry: OutboxEntry) -> None:
    _DLQ_DIR.mkdir(parents=True, exist_ok=True)
    (_DLQ_DIR / f"{entry.outbox_id}.json").write_text(
        json.dumps(entry.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    try:
        (_OUTBOX_DIR / f"{entry.outbox_id}.json").unlink()
    except OSError:
        pass


def _list(*, state_in: tuple | None, limit: int) -> list[OutboxEntry]:
    if not _OUTBOX_DIR.exists():
        return []
    out: list[OutboxEntry] = []
    for p in _OUTBOX_DIR.glob("obx_*.json"):
        try:
            entry = OutboxEntry(**json.loads(p.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        if state_in and entry.state not in state_in:
            continue
        out.append(entry)
    out.sort(key=lambda e: e.created_at)
    return out[:limit]


def _find_by_idempotency_key(key: str) -> OutboxEntry | None:
    if not _OUTBOX_DIR.exists():
        return None
    for entry in _list(state_in=None, limit=10000):
        if entry.idempotency_key == key:
            return entry
    return None
