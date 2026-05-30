"""Worker coordination — single-host multi-process worker registry with
heartbeat, draining, leader election, and stale eviction.

Scope honesty
-------------
Workers register a JSON row under ``output/ops_platform/workers/{worker_id}.json``
and refresh ``last_heartbeat_at`` periodically. The leader is the worker
holding the ``worker.leader`` lock from ``distributed_lock``. Coordination
scope: single host, multi-process. Multi-host scale requires a network lock
(Redis/etcd) — wire it through ``distributed_lock`` when ready.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution.ops_platform import audit_log, distributed_lock

logger = logging.getLogger(__name__)

_WORKERS_DIR = OUTPUT_DIR / "ops_platform" / "workers"
_LEADER_LOCK = "worker.leader"

DEFAULT_HEARTBEAT_TTL_SECONDS = 30


@dataclass
class Worker:
    worker_id: str
    pid: int
    started_at: str
    last_heartbeat_at: str
    role: str         # "general" | "scheduler" | "self_heal" | "user-defined"
    queues: list      # which queues this worker pulls from
    status: str       # "active" | "draining" | "stopped"
    host: str = ""
    metadata: dict = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["metadata"] = d.get("metadata") or {}
        return d


# ── Public API ─────────────────────────────────────────────────────────


def register(
    *,
    role: str = "general",
    queues: list | None = None,
    metadata: dict | None = None,
) -> Worker:
    _WORKERS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    worker = Worker(
        worker_id=f"{os.getpid()}-{uuid.uuid4().hex[:8]}",
        pid=os.getpid(),
        started_at=now,
        last_heartbeat_at=now,
        role=role,
        queues=list(queues or ["default"]),
        status="active",
        host=os.environ.get("HOSTNAME", "localhost"),
        metadata=metadata or {},
    )
    _persist(worker)
    audit_log.record(
        action="worker.registered", entity_type="worker",
        entity_id=worker.worker_id,
        actor={"name": worker.worker_id, "system": True},
        new_state={"role": role, "queues": worker.queues, "pid": worker.pid},
    )
    return worker


def heartbeat(worker_id: str) -> bool:
    w = get_worker(worker_id)
    if w is None:
        return False
    w.last_heartbeat_at = datetime.now(timezone.utc).isoformat()
    _persist(w)
    return True


def drain(worker_id: str, *, actor: dict | str | None = None) -> bool:
    w = get_worker(worker_id)
    if w is None or w.status == "stopped":
        return False
    w.status = "draining"
    _persist(w)
    audit_log.record(
        action="worker.draining", entity_type="worker", entity_id=worker_id,
        actor=actor or {"name": "anonymous"},
    )
    return True


def stop(worker_id: str, *, actor: dict | str | None = None) -> bool:
    w = get_worker(worker_id)
    if w is None:
        return False
    w.status = "stopped"
    _persist(w)
    audit_log.record(
        action="worker.stopped", entity_type="worker", entity_id=worker_id,
        actor=actor or {"name": "anonymous"},
        previous_state={"status": "active"},
    )
    return True


def evict_stale(*, ttl_seconds: int = DEFAULT_HEARTBEAT_TTL_SECONDS) -> list[str]:
    """Remove worker rows whose heartbeats are older than ttl_seconds."""
    evicted: list[str] = []
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=ttl_seconds)
    for w in list_workers():
        try:
            hb = datetime.fromisoformat(w.last_heartbeat_at)
        except ValueError:
            continue
        if hb < cutoff and w.status != "stopped":
            try:
                (_WORKERS_DIR / f"{w.worker_id}.json").unlink()
                evicted.append(w.worker_id)
                audit_log.record(
                    action="worker.evicted", entity_type="worker",
                    entity_id=w.worker_id,
                    actor={"name": "worker_coordinator", "system": True},
                    metadata={"reason": "stale heartbeat"},
                )
            except OSError:
                continue
    return evicted


def get_worker(worker_id: str) -> Worker | None:
    path = _WORKERS_DIR / f"{worker_id}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Worker(**data)
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def list_workers(*, status: str | None = None,
                   role: str | None = None) -> list[Worker]:
    if not _WORKERS_DIR.exists():
        return []
    out: list[Worker] = []
    for p in _WORKERS_DIR.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            w = Worker(**data)
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        if status and w.status != status:
            continue
        if role and w.role != role:
            continue
        out.append(w)
    out.sort(key=lambda w: w.last_heartbeat_at, reverse=True)
    return out


def acquire_leadership(worker_id: str, *, lease_seconds: int = 60) -> bool:
    """Attempt to become the singleton leader. Returns True if held."""
    try:
        distributed_lock.acquire(_LEADER_LOCK, owner_id=worker_id,
                                  lease_seconds=lease_seconds,
                                  acquire_timeout_seconds=1)
        return True
    except distributed_lock.LockAcquisitionError:
        return False


def refresh_leadership(worker_id: str, *, lease_seconds: int = 60) -> bool:
    """Heartbeat the leader lock."""
    try:
        distributed_lock.heartbeat(_LEADER_LOCK, owner_id=worker_id,
                                     lease_seconds=lease_seconds)
        return True
    except distributed_lock.LockAcquisitionError:
        return False


def release_leadership(worker_id: str) -> bool:
    return distributed_lock.release(_LEADER_LOCK, owner_id=worker_id)


def current_leader() -> dict | None:
    return distributed_lock.is_held(_LEADER_LOCK)


# ── Internal ───────────────────────────────────────────────────────────


def _persist(worker: Worker) -> None:
    _WORKERS_DIR.mkdir(parents=True, exist_ok=True)
    path = _WORKERS_DIR / f"{worker.worker_id}.json"
    path.write_text(json.dumps(worker.to_dict(), indent=2, ensure_ascii=False),
                     encoding="utf-8")
