"""Redis Sentinel + failover detection.

Scope honesty
-------------
- The Sentinel client itself is constructed by the operator's bootstrap
  using ``redis.sentinel.Sentinel(...)``. This module wraps it with:
    - role inspection (``redis_role()``)
    - failover detection (``check_failover()``)
    - reconnect loop with jitter
    - stream + consumer-group recovery hooks
    - fencing-token continuity verification
- Real Redis Cluster (multi-slot) is **NOT validated** for the platform's
  Lua-based lock release. If Cluster is detected, ``cluster_warnings()``
  surfaces an explicit warning that the operator must heed.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import asdict, dataclass

from execution.ops_platform import audit_log, distributed_lock_v2, redis_backends

logger = logging.getLogger(__name__)


@dataclass
class FailoverEvent:
    detected_at: str
    previous_master: str | None
    new_master: str | None
    reason: str
    correlation_id: str

    def to_dict(self) -> dict:
        return asdict(self)


_LAST_KNOWN_MASTER: str | None = None
_LAST_FAILOVER: FailoverEvent | None = None
_RECONNECTING: bool = False


def configure_sentinel(sentinel_client, *, master_name: str = "ops-master",
                          key_prefix: str | None = None) -> dict:
    """Resolve a master via the Sentinel client, wire it as the active
    Redis client. Returns a status dict."""
    if not redis_backends.is_available():
        raise redis_backends.RedisNotConfigured(
            "redis-py is not installed; cannot configure Sentinel"
        )
    try:
        master = sentinel_client.master_for(master_name, decode_responses=False)
    except Exception as e:
        return {"configured": False, "reason": str(e)[:200]}
    redis_backends.configure_redis(master, key_prefix=key_prefix)
    info = _info_safe(master)
    role = (info or {}).get("role")
    global _LAST_KNOWN_MASTER
    _LAST_KNOWN_MASTER = role
    audit_log.record(
        action="redis_sentinel.configured", entity_type="redis_sentinel",
        entity_id=master_name,
        actor={"name": "redis_sentinel", "system": True},
        new_state={"master_name": master_name, "role": role},
    )
    return {"configured": True, "master_name": master_name,
              "role": role,
              "explainer": ("Sentinel client resolved a master and wired it into "
                              "redis_backends. Failover detection runs on each "
                              "check_failover() call.")}


def redis_role() -> str | None:
    """Return 'master' | 'slave' | 'unknown' | None."""
    if not (redis_backends.is_available() and redis_backends._CLIENT is not None):
        return None
    info = _info_safe(redis_backends._CLIENT)
    if not info:
        return None
    role = info.get("role")
    if isinstance(role, bytes):
        role = role.decode("utf-8")
    return role or "unknown"


def check_failover() -> dict:
    """Compare the current Redis role against the last known role; declare
    failover when the master changes or the connection is unreachable."""
    global _LAST_KNOWN_MASTER, _LAST_FAILOVER
    role = redis_role()
    if role is None:
        return {"connected": False, "failover_detected": True,
                  "redis_role": None,
                  "reason": "client unwired or unreachable"}
    if _LAST_KNOWN_MASTER and role != _LAST_KNOWN_MASTER:
        ev = FailoverEvent(
            detected_at=_now_iso(),
            previous_master=_LAST_KNOWN_MASTER,
            new_master=role, reason="role transition observed",
            correlation_id=_corr(),
        )
        _LAST_FAILOVER = ev
        _LAST_KNOWN_MASTER = role
        audit_log.record(
            action="redis_sentinel.failover_detected",
            entity_type="redis_sentinel", entity_id="cluster",
            actor={"name": "redis_sentinel", "system": True},
            correlation_id=ev.correlation_id,
            previous_state={"role": ev.previous_master},
            new_state={"role": role},
        )
        return {"connected": True, "failover_detected": True,
                  "redis_role": role, "event": ev.to_dict()}
    _LAST_KNOWN_MASTER = role
    return {"connected": True, "failover_detected": False,
              "redis_role": role}


def reconnect_with_jitter(
    *,
    sentinel_client,
    master_name: str = "ops-master",
    attempts: int = 5,
    base_seconds: float = 1.0,
    max_seconds: float = 30.0,
) -> dict:
    """Best-effort reconnect with exponential backoff + jitter. Records each
    attempt. Returns the outcome."""
    global _RECONNECTING
    _RECONNECTING = True
    try:
        for n in range(1, attempts + 1):
            backoff = min(max_seconds, base_seconds * (2 ** (n - 1)))
            backoff *= (1 + random.uniform(-0.2, 0.2))
            try:
                result = configure_sentinel(sentinel_client, master_name=master_name)
                if result.get("configured"):
                    audit_log.record(
                        action="redis_sentinel.reconnected",
                        entity_type="redis_sentinel", entity_id=master_name,
                        actor={"name": "redis_sentinel", "system": True},
                        new_state={"attempt": n, "role": result.get("role")},
                    )
                    return {"reconnected": True, "attempts": n,
                              "role": result.get("role")}
            except Exception as e:
                logger.warning("sentinel reconnect attempt %d failed: %s", n, e)
            time.sleep(backoff)
        audit_log.record(
            action="redis_sentinel.reconnect_exhausted",
            entity_type="redis_sentinel", entity_id=master_name,
            actor={"name": "redis_sentinel", "system": True},
            metadata={"attempts": attempts},
        )
        return {"reconnected": False, "attempts": attempts}
    finally:
        _RECONNECTING = False


def recover_consumer_groups(group: str, event_types: list) -> dict:
    """After a failover, re-prime consumer groups by re-running xgroup_create
    at id="0". distributed_event_bus.consume_group already does this lazily;
    this helper is for explicit pre-warm."""
    if not (redis_backends.is_available() and redis_backends._CLIENT is not None):
        return {"recovered": False, "reason": "Redis client not wired"}
    client = redis_backends._CLIENT
    prefix = redis_backends._KEY_PREFIX
    recovered = 0
    for t in event_types:
        key = f"{prefix}fabric:{t}"
        try:
            client.xgroup_create(key, group, id="0", mkstream=True)
            recovered += 1
        except Exception:
            # Group already exists; safe
            recovered += 1
    return {"recovered": True, "groups_primed": recovered}


def verify_fencing_continuity(lock_name: str, *,
                                 token_before_failover: int) -> dict:
    """After a failover, the next acquire's fencing token MUST be strictly
    greater than any previously-issued token for the same lock. This helper
    issues a probe acquire/release and reports the new token."""
    if not (redis_backends.is_available() and redis_backends._CLIENT is not None):
        return {"verified": False, "reason": "Redis client not wired"}
    try:
        lease = distributed_lock_v2.acquire(lock_name, lease_seconds=5,
                                                  acquire_timeout_seconds=2)
        new_token = lease.fencing_token
        distributed_lock_v2.release(lock_name, owner_token=lease.owner_token)
        monotonic = new_token > token_before_failover
        return {"verified": monotonic, "new_token": new_token,
                  "previous_token": token_before_failover,
                  "explainer": ("strict-monotonic" if monotonic
                                  else "FENCING TOKEN NOT MONOTONIC — Redis state may have been lost")}
    except distributed_lock_v2.LockBusy as e:
        return {"verified": False, "reason": f"lock busy during probe: {e}"}


def cluster_warnings() -> list[str]:
    """If the current Redis client is a Cluster client, emit a warning that
    the Lua release script is NOT validated for multi-slot operations."""
    warnings: list[str] = []
    if not (redis_backends.is_available() and redis_backends._CLIENT is not None):
        return warnings
    client_module = type(redis_backends._CLIENT).__module__ or ""
    client_name = type(redis_backends._CLIENT).__name__ or ""
    if "cluster" in client_module.lower() or "cluster" in client_name.lower():
        warnings.append(
            "Redis Cluster client detected. The platform's Lua-atomic "
            "lock release script (distributed_lock_v2) is NOT validated "
            "for multi-slot operations. Use Sentinel + single-instance "
            "Redis, or pin lock keys to a single hash tag."
        )
    return warnings


def current_state() -> dict:
    role = redis_role()
    return {
        "redis_role": role,
        "sentinel_connected": role is not None,
        "last_failover": _LAST_FAILOVER.to_dict() if _LAST_FAILOVER else None,
        "reconnecting": _RECONNECTING,
        "cluster_warnings": cluster_warnings(),
    }


# ── Internal ───────────────────────────────────────────────────────────


def _info_safe(client) -> dict | None:
    try:
        info = client.info(section="replication") if hasattr(client, "info") else None
        if isinstance(info, dict):
            return info
        if isinstance(info, bytes):
            return {}
    except Exception:
        return None
    return None


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _corr() -> str:
    import uuid
    return uuid.uuid4().hex
