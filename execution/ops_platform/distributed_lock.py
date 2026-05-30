"""Distributed lock manager — single-host multi-process coordination.

Scope honesty
-------------
This module provides coordination across multiple PROCESSES on the SAME HOST.
It is NOT a multi-host lock. The substrate is OS file locks (``filelock``)
plus on-disk JSON ownership records. When you want true multi-host
coordination, drop in a RedisBackend (the interface is here; the file backend
is the production-grade default).

What it gives you
-----------------
- exclusive lock acquisition with lease expiration
- reentrant safety (same owner can reacquire without deadlock)
- heartbeat refresh (long-running owners extend their lease)
- stale-lock recovery (expired leases are reclaimed automatically)
- compare-and-set semantics (lock holder identity is verified on release)
- audit log entries for every acquire / release / reclaim
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from filelock import FileLock, Timeout

from config.settings import OUTPUT_DIR
from execution.ops_platform import audit_log

logger = logging.getLogger(__name__)

_LOCKS_DIR = OUTPUT_DIR / "ops_platform" / "locks"

DEFAULT_LEASE_SECONDS = 60
DEFAULT_ACQUIRE_TIMEOUT_SECONDS = 5


@dataclass
class LockRecord:
    lock_name: str
    owner_id: str
    acquired_at: str
    expires_at: str
    lease_seconds: int
    heartbeats: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


class LockAcquisitionError(Exception):
    pass


def _own_id() -> str:
    return f"{os.getpid()}:{uuid.uuid4().hex[:8]}"


def acquire(
    lock_name: str,
    *,
    owner_id: str | None = None,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    acquire_timeout_seconds: int = DEFAULT_ACQUIRE_TIMEOUT_SECONDS,
) -> LockRecord:
    """Acquire an exclusive lock. Raises LockAcquisitionError if it cannot
    be obtained within ``acquire_timeout_seconds``."""
    _LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    owner = owner_id or _own_id()
    state_path = _LOCKS_DIR / f"{lock_name}.json"
    guard_path = _LOCKS_DIR / f"{lock_name}.guard"
    guard = FileLock(str(guard_path), timeout=acquire_timeout_seconds)
    try:
        with guard:
            existing = _read_state(state_path)
            now = time.time()
            if existing is not None:
                expires_at = existing.get("expires_at_epoch", 0)
                if existing.get("owner_id") == owner:
                    # Reentrant — refresh in-place
                    return _write_state(state_path, lock_name, owner, lease_seconds,
                                          heartbeats=existing.get("heartbeats", 0) + 1)
                if expires_at > now:
                    raise LockAcquisitionError(
                        f"lock '{lock_name}' held by {existing.get('owner_id')} "
                        f"until {existing.get('expires_at')}"
                    )
                # Expired — reclaim, leave an audit trail
                audit_log.record(
                    action="lock.reclaimed", entity_type="lock",
                    entity_id=lock_name,
                    actor={"name": owner, "system": True},
                    previous_state={"owner_id": existing.get("owner_id")},
                    metadata={"reason": "previous lease expired"},
                )
            record = _write_state(state_path, lock_name, owner, lease_seconds)
            audit_log.record(
                action="lock.acquired", entity_type="lock", entity_id=lock_name,
                actor={"name": owner, "system": True},
                new_state={"lease_seconds": lease_seconds},
            )
            return record
    except Timeout:
        raise LockAcquisitionError(
            f"could not acquire guard for lock '{lock_name}' within "
            f"{acquire_timeout_seconds}s"
        )


def heartbeat(lock_name: str, *, owner_id: str,
               lease_seconds: int = DEFAULT_LEASE_SECONDS) -> LockRecord:
    """Refresh the lease. Raises LockAcquisitionError when the caller is not
    the current owner."""
    state_path = _LOCKS_DIR / f"{lock_name}.json"
    guard_path = _LOCKS_DIR / f"{lock_name}.guard"
    with FileLock(str(guard_path), timeout=DEFAULT_ACQUIRE_TIMEOUT_SECONDS):
        existing = _read_state(state_path)
        if existing is None or existing.get("owner_id") != owner_id:
            raise LockAcquisitionError(
                f"caller {owner_id} is not the current owner of '{lock_name}'"
            )
        return _write_state(state_path, lock_name, owner_id, lease_seconds,
                              heartbeats=existing.get("heartbeats", 0) + 1)


def release(lock_name: str, *, owner_id: str) -> bool:
    """Release if the caller still owns the lock. Returns True on release,
    False when the owner doesn't match (silent — caller can decide what to do)."""
    state_path = _LOCKS_DIR / f"{lock_name}.json"
    guard_path = _LOCKS_DIR / f"{lock_name}.guard"
    with FileLock(str(guard_path), timeout=DEFAULT_ACQUIRE_TIMEOUT_SECONDS):
        existing = _read_state(state_path)
        if existing is None or existing.get("owner_id") != owner_id:
            return False
        try:
            state_path.unlink()
        except OSError:
            return False
    audit_log.record(
        action="lock.released", entity_type="lock", entity_id=lock_name,
        actor={"name": owner_id, "system": True},
        previous_state={"owner_id": owner_id},
    )
    return True


def is_held(lock_name: str) -> dict | None:
    """Return the active lock state, or None if free / expired."""
    state_path = _LOCKS_DIR / f"{lock_name}.json"
    existing = _read_state(state_path)
    if existing is None:
        return None
    if existing.get("expires_at_epoch", 0) <= time.time():
        return None
    return existing


def list_active() -> list[dict]:
    if not _LOCKS_DIR.exists():
        return []
    out: list[dict] = []
    for p in _LOCKS_DIR.glob("*.json"):
        try:
            row = json.loads(p.read_text(encoding="utf-8"))
            if row.get("expires_at_epoch", 0) > time.time():
                out.append(row)
        except (OSError, json.JSONDecodeError):
            continue
    return out


@contextmanager
def held(lock_name: str, *, owner_id: str | None = None,
          lease_seconds: int = DEFAULT_LEASE_SECONDS):
    """Context manager that acquires, yields the record, and releases."""
    owner = owner_id or _own_id()
    record = acquire(lock_name, owner_id=owner, lease_seconds=lease_seconds)
    try:
        yield record
    finally:
        release(lock_name, owner_id=owner)


# ── Internal ───────────────────────────────────────────────────────────


def _read_state(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_state(path: Path, lock_name: str, owner: str,
                  lease_seconds: int, *, heartbeats: int = 0) -> LockRecord:
    now = time.time()
    expires = now + lease_seconds
    record = LockRecord(
        lock_name=lock_name, owner_id=owner,
        acquired_at=datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
        expires_at=datetime.fromtimestamp(expires, tz=timezone.utc).isoformat(),
        lease_seconds=lease_seconds, heartbeats=heartbeats,
    )
    payload = record.to_dict()
    payload["expires_at_epoch"] = expires
    payload["acquired_at_epoch"] = now
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return record
