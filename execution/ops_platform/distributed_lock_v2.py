"""Distributed lock v2 — Redis-backed with fencing tokens.

Why v2
------
Phase 6's ``distributed_lock`` is file-based: single-host multi-process. Phase
7's ``redis_backends.acquire`` shipped a real Redis SETNX path but did not
issue a fencing token. v2 issues a monotonic fencing token via Redis INCR
so a process that *thinks* it still owns a lock after a long pause can be
rejected by the resource it's trying to write to.

Scope honesty
-------------
- Requires a wired Redis client. Raises ``RedisNotConfigured`` otherwise.
- Coordination scope: **multi-host** when a real Redis cluster is reachable.
- Failure semantics: lock expires after ``lease_seconds`` from last successful
  ``heartbeat``; the owner does NOT retain it across Redis outages.
- Caller MUST present its fencing token to any downstream system that needs
  to reject stale writes.

Lease + fencing protocol
------------------------
- Acquire: SETNX key=<owner_token> EX=<lease>; if NX fails, deny.
- A separate monotonic INCR returns the fencing token (per resource).
- Heartbeat: extend EX iff the current value still equals our owner_token.
- Release: Lua-atomic DEL iff value matches.
- Reads expose the current fencing token so any peer can prove staleness.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import asdict, dataclass

from execution.ops_platform import redis_backends

logger = logging.getLogger(__name__)


@dataclass
class LeaseRecord:
    lock_name: str
    owner_token: str
    fencing_token: int
    lease_seconds: int
    acquired_at_epoch: float
    expires_at_epoch: float

    def to_dict(self) -> dict:
        return asdict(self)


class LockBusy(Exception):
    """Raised when the lock is held by another owner."""


# Atomic release: DEL only when the current value matches our owner token.
_RELEASE_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""

# Atomic heartbeat: EXPIRE only when the current value matches our owner token.
_HEARTBEAT_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("expire", KEYS[1], ARGV[2])
else
    return 0
end
"""


def _require_client():
    if not (redis_backends.is_available() and redis_backends._CLIENT is not None):
        raise redis_backends.RedisNotConfigured(
            "distributed_lock_v2 requires redis_backends.activate(client)"
        )
    return redis_backends.get_redis()


def acquire(lock_name: str, *, lease_seconds: int = 60,
              acquire_timeout_seconds: int = 5) -> LeaseRecord:
    """Acquire a lease + fencing token. Raises LockBusy if the resource is
    held by another owner within ``acquire_timeout_seconds``."""
    client = _require_client()
    prefix = redis_backends._KEY_PREFIX
    key = f"{prefix}lock:{lock_name}"
    fencing_key = f"{prefix}lockfencing:{lock_name}"
    owner_token = uuid.uuid4().hex
    deadline = time.time() + acquire_timeout_seconds
    while True:
        acquired = client.set(key, owner_token, nx=True, ex=lease_seconds)
        if acquired:
            try:
                fencing = int(client.incr(fencing_key))
            except Exception:
                fencing = 0
            now = time.time()
            return LeaseRecord(
                lock_name=lock_name, owner_token=owner_token,
                fencing_token=fencing, lease_seconds=lease_seconds,
                acquired_at_epoch=now,
                expires_at_epoch=now + lease_seconds,
            )
        if time.time() >= deadline:
            raise LockBusy(f"lock '{lock_name}' busy")
        time.sleep(0.05)


def heartbeat(lock_name: str, *, owner_token: str,
                lease_seconds: int = 60) -> bool:
    client = _require_client()
    key = f"{redis_backends._KEY_PREFIX}lock:{lock_name}"
    try:
        result = client.eval(_HEARTBEAT_LUA, 1, key, owner_token, str(lease_seconds))
        return int(result) == 1
    except Exception:
        return False


def release(lock_name: str, *, owner_token: str) -> bool:
    client = _require_client()
    key = f"{redis_backends._KEY_PREFIX}lock:{lock_name}"
    try:
        result = client.eval(_RELEASE_LUA, 1, key, owner_token)
        return int(result) == 1
    except Exception:
        return False


def is_held(lock_name: str) -> dict | None:
    client = _require_client()
    key = f"{redis_backends._KEY_PREFIX}lock:{lock_name}"
    fencing_key = f"{redis_backends._KEY_PREFIX}lockfencing:{lock_name}"
    try:
        owner_token = client.get(key)
    except Exception:
        return None
    if owner_token is None:
        return None
    if isinstance(owner_token, bytes):
        owner_token = owner_token.decode("utf-8")
    try:
        ttl = client.ttl(key)
    except Exception:
        ttl = None
    try:
        fencing = client.get(fencing_key)
        if isinstance(fencing, bytes):
            fencing = fencing.decode("utf-8")
        fencing_value = int(fencing) if fencing else None
    except Exception:
        fencing_value = None
    return {
        "lock_name": lock_name, "owner_token": owner_token,
        "ttl_seconds": int(ttl) if ttl is not None else None,
        "fencing_token": fencing_value,
    }


def verify_fencing_token(lock_name: str, presented_token: int) -> bool:
    """Resource-side check. A downstream system that accepts an operation
    on behalf of a lock holder should call this before applying — if a
    later holder has been granted a higher fencing token, the older holder's
    operation must be rejected."""
    client = _require_client()
    fencing_key = f"{redis_backends._KEY_PREFIX}lockfencing:{lock_name}"
    try:
        current = client.get(fencing_key)
    except Exception:
        return False
    if current is None:
        return False
    if isinstance(current, bytes):
        current = current.decode("utf-8")
    try:
        return int(presented_token) >= int(current)
    except (TypeError, ValueError):
        return False
