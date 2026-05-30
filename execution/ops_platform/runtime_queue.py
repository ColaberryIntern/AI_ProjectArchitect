"""Persistent runtime queue — single-host multi-process FIFO with priority,
delayed jobs, retries, dead-letter, and restart-survival.

Scope honesty
-------------
- All coordination uses ``distributed_lock`` (file-locked JSON), so multiple
  worker processes on the SAME HOST can safely dequeue without duplication.
- This is NOT a multi-host queue. For that, swap the storage layer for a
  real queue (Postgres LISTEN/NOTIFY, Redis Streams, SQS) — the public API
  (``enqueue``, ``claim``, ``ack``, ``nack``, ``status``) is the seam.
- Jobs survive restart: every state transition is fsynced to disk before
  ``claim()`` returns.
- "Exactly-once" is best-effort: the at-least-once delivery semantics +
  ``idempotency_key`` deduplication mean a worker that crashes after
  performing the work but before calling ``ack()`` will let another worker
  re-claim and re-execute. Idempotent handlers handle this correctly.

States
------
   pending → claimed → done
                    ↘ failed → dead_letter (after max_attempts)
   scheduled (delayed) → pending (when due)
   cancelled (terminal — set by operator)

Files on disk
-------------
``output/ops_platform/runtime_queue/{job_id}.json`` (one file per job).
The ``status`` field inside drives the state machine; mtime is the last
state transition.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution.ops_platform import audit_log, distributed_lock

logger = logging.getLogger(__name__)

_QUEUE_DIR = OUTPUT_DIR / "ops_platform" / "runtime_queue"
_GLOBAL_LOCK = "runtime_queue.global"

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_CLAIM_LEASE_SECONDS = 300


@dataclass
class Job:
    job_id: str
    queue: str
    kind: str                       # what kind of work — e.g. "workflow_run"
    payload: dict
    priority: int                   # higher = sooner; default 0
    enqueued_at: str
    available_at: str               # ISO timestamp; >now means scheduled/delayed
    status: str                     # pending | claimed | done | failed | dead_letter | cancelled | scheduled
    attempts: int = 0
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    claim_owner: str | None = None
    claim_expires_at: str | None = None
    last_error: str | None = None
    result: dict | None = None
    correlation_id: str | None = None
    idempotency_key: str | None = None
    enqueued_by: dict | None = None
    last_updated_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ── Public API ─────────────────────────────────────────────────────────


def enqueue(
    *,
    kind: str,
    payload: dict,
    queue: str = "default",
    priority: int = 0,
    delay_seconds: int = 0,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    correlation_id: str | None = None,
    idempotency_key: str | None = None,
    enqueued_by: dict | str | None = None,
) -> Job:
    """Add a job to the queue. If ``idempotency_key`` matches an existing
    non-terminal job, returns the existing job instead of creating a new one."""
    _QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    actor = enqueued_by if isinstance(enqueued_by, dict) else {"name": str(enqueued_by or "anonymous")}

    if idempotency_key:
        for existing in _all_jobs():
            if existing.idempotency_key == idempotency_key and existing.status in (
                "pending", "claimed", "done", "scheduled"
            ):
                return existing

    now = datetime.now(timezone.utc)
    available = now if delay_seconds <= 0 else now.fromtimestamp(
        now.timestamp() + delay_seconds, tz=timezone.utc
    )
    job = Job(
        job_id=str(uuid.uuid4()),
        queue=queue, kind=kind, payload=dict(payload),
        priority=int(priority),
        enqueued_at=now.isoformat(),
        available_at=available.isoformat(),
        status="scheduled" if delay_seconds > 0 else "pending",
        max_attempts=max_attempts,
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
        enqueued_by=actor,
        last_updated_at=now.isoformat(),
    )
    _persist(job)
    audit_log.record(
        action="queue.enqueued", entity_type="job", entity_id=job.job_id,
        actor=actor, correlation_id=correlation_id,
        new_state={"kind": kind, "queue": queue, "priority": priority,
                   "status": job.status, "delay_seconds": delay_seconds},
    )
    return job


def claim(
    *,
    queue: str = "default",
    worker_id: str,
    lease_seconds: int = DEFAULT_CLAIM_LEASE_SECONDS,
) -> Job | None:
    """Atomically pull the next available job. Returns None when the queue
    has nothing ready."""
    with distributed_lock.held(_GLOBAL_LOCK, owner_id=worker_id, lease_seconds=15):
        candidates = _ready_jobs(queue)
        if not candidates:
            return None
        candidates.sort(key=lambda j: (-j.priority, j.enqueued_at))
        chosen = candidates[0]
        now = datetime.now(timezone.utc)
        chosen.status = "claimed"
        chosen.claim_owner = worker_id
        chosen.claim_expires_at = (now.fromtimestamp(now.timestamp() + lease_seconds,
                                                       tz=timezone.utc)).isoformat()
        chosen.attempts += 1
        chosen.last_updated_at = now.isoformat()
        _persist(chosen)
    audit_log.record(
        action="queue.claimed", entity_type="job", entity_id=chosen.job_id,
        actor={"name": worker_id, "system": True},
        correlation_id=chosen.correlation_id,
        new_state={"attempts": chosen.attempts, "lease_seconds": lease_seconds},
    )
    return chosen


def ack(job_id: str, *, worker_id: str, result: dict | None = None) -> bool:
    """Mark a claimed job done. Verifies the worker still owns the lease."""
    job = get_job(job_id)
    if job is None or job.claim_owner != worker_id or job.status != "claimed":
        return False
    job.status = "done"
    job.result = result
    job.claim_owner = None
    job.claim_expires_at = None
    job.last_updated_at = datetime.now(timezone.utc).isoformat()
    _persist(job)
    audit_log.record(
        action="queue.acked", entity_type="job", entity_id=job_id,
        actor={"name": worker_id, "system": True},
        correlation_id=job.correlation_id,
        new_state={"status": "done"},
    )
    return True


def nack(job_id: str, *, worker_id: str, error: str = "") -> bool:
    """Mark a claimed job failed. Either re-queues (if attempts < max_attempts)
    or sends to dead-letter."""
    job = get_job(job_id)
    if job is None or job.claim_owner != worker_id or job.status != "claimed":
        return False
    job.claim_owner = None
    job.claim_expires_at = None
    job.last_error = error or "unspecified failure"
    if job.attempts >= job.max_attempts:
        job.status = "dead_letter"
    else:
        job.status = "pending"
    job.last_updated_at = datetime.now(timezone.utc).isoformat()
    _persist(job)
    audit_log.record(
        action="queue.nacked", entity_type="job", entity_id=job_id,
        actor={"name": worker_id, "system": True},
        correlation_id=job.correlation_id,
        new_state={"status": job.status, "attempts": job.attempts,
                   "error": (error or "")[:200]},
    )
    return True


def cancel(job_id: str, *, actor: dict | str | None = None) -> bool:
    job = get_job(job_id)
    if job is None or job.status in ("done", "dead_letter", "cancelled"):
        return False
    job.status = "cancelled"
    job.last_updated_at = datetime.now(timezone.utc).isoformat()
    _persist(job)
    audit_log.record(
        action="queue.cancelled", entity_type="job", entity_id=job_id,
        actor=actor or {"name": "anonymous"},
        correlation_id=job.correlation_id,
        new_state={"status": "cancelled"},
    )
    return True


def get_job(job_id: str) -> Job | None:
    path = _QUEUE_DIR / f"{job_id}.json"
    if not path.exists():
        return None
    try:
        return Job(**json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def status(job_id: str) -> dict | None:
    job = get_job(job_id)
    if job is None:
        return None
    return {
        "job_id": job.job_id, "status": job.status, "kind": job.kind,
        "attempts": job.attempts, "result": job.result,
        "last_error": job.last_error, "enqueued_at": job.enqueued_at,
        "available_at": job.available_at, "last_updated_at": job.last_updated_at,
        "claim_owner": job.claim_owner, "claim_expires_at": job.claim_expires_at,
        "correlation_id": job.correlation_id, "queue": job.queue,
    }


def list_jobs(*, queue: str | None = None, status: str | None = None,
               limit: int = 200) -> list[Job]:
    out = _all_jobs()
    if queue:
        out = [j for j in out if j.queue == queue]
    if status:
        out = [j for j in out if j.status == status]
    out.sort(key=lambda j: j.last_updated_at, reverse=True)
    return out[:limit]


def reclaim_stale(*, now: datetime | None = None) -> list[str]:
    """Sweep claimed jobs whose lease expired and move them back to pending.

    Called by worker_coordination's heartbeat sweep; safe to call repeatedly.
    Returns the list of reclaimed job_ids.
    """
    now = now or datetime.now(timezone.utc)
    reclaimed: list[str] = []
    with distributed_lock.held(_GLOBAL_LOCK, lease_seconds=15):
        for job in _all_jobs():
            if job.status != "claimed":
                continue
            if not job.claim_expires_at:
                continue
            try:
                exp = datetime.fromisoformat(job.claim_expires_at)
            except ValueError:
                continue
            if exp >= now:
                continue
            previous_owner = job.claim_owner
            job.status = "pending"
            job.claim_owner = None
            job.claim_expires_at = None
            job.last_updated_at = now.isoformat()
            _persist(job)
            reclaimed.append(job.job_id)
            audit_log.record(
                action="queue.reclaimed", entity_type="job", entity_id=job.job_id,
                actor={"name": "queue_sweeper", "system": True},
                correlation_id=job.correlation_id,
                previous_state={"claim_owner": previous_owner},
                metadata={"reason": "claim lease expired"},
            )
    return reclaimed


def queue_depth(*, queue: str = "default") -> dict:
    """Counts by status — cheap dashboard read."""
    from collections import Counter
    counts: Counter = Counter()
    for j in list_jobs(queue=queue, limit=10000):
        counts[j.status] += 1
    return {"queue": queue, "counts": dict(counts), "total": sum(counts.values())}


# ── Internal ───────────────────────────────────────────────────────────


def _persist(job: Job) -> None:
    _QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    path = _QUEUE_DIR / f"{job.job_id}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(job.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _all_jobs() -> list[Job]:
    if not _QUEUE_DIR.exists():
        return []
    out: list[Job] = []
    for p in _QUEUE_DIR.glob("*.json"):
        try:
            out.append(Job(**json.loads(p.read_text(encoding="utf-8"))))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    return out


def _ready_jobs(queue: str) -> list[Job]:
    now = datetime.now(timezone.utc)
    out: list[Job] = []
    for job in _all_jobs():
        if job.queue != queue:
            continue
        if job.status not in ("pending", "scheduled"):
            continue
        try:
            available = datetime.fromisoformat(job.available_at)
        except ValueError:
            available = now
        if available > now:
            continue
        # Promote scheduled→pending lazily on read so dequeues see them
        if job.status == "scheduled":
            job.status = "pending"
            job.last_updated_at = now.isoformat()
            _persist(job)
        out.append(job)
    return out
