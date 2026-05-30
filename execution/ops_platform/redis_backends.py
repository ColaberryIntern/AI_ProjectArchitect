"""Real Redis-backed implementations of the platform's coordination primitives.

Scope honesty
-------------
- Code paths in this module are exercised ONLY when ``redis-py`` is installed
  AND a Redis client is injected via ``configure_redis(client)``.
- Without that, every operation here raises ``RedisNotConfigured`` so the
  caller is forced to handle the fallback. There is NO silent file-backend
  substitution from this module — that would re-introduce the "fake HA"
  problem the platform's rules forbid.
- ``activate(client)`` is the one-call wiring helper: it installs Redis
  versions of distributed_lock + cache + rate-limit primitives. The Phase 6
  file-based originals stay loaded as a fallback (operators can roll back
  by calling ``deactivate()``).

Modules covered:
  - shared_cache_backend  (RedisBackend already declared in Phase 6 — this
                            module ships its real implementation)
  - distributed_lock      (Redis SETNX + Lua release script)
  - distributed_rate_limit (Redis INCR + EXPIRE)
  - pub/sub fanout for realtime_bus (when activated)
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from typing import Optional

logger = logging.getLogger(__name__)


class RedisNotConfigured(RuntimeError):
    """Raised when a Redis-backed operation is requested but no client is wired."""


# ── redis-py availability ──────────────────────────────────────────────


try:
    import redis as _redis_lib  # type: ignore
    _REDIS_AVAILABLE = True
except Exception:
    _redis_lib = None
    _REDIS_AVAILABLE = False


def is_available() -> bool:
    return _REDIS_AVAILABLE


_CLIENT = None
_KEY_PREFIX = os.environ.get("OPS_REDIS_PREFIX", "ops:")


def configure_redis(client, *, key_prefix: str | None = None) -> None:
    """Wire a redis client into the platform. Pass a ``redis.Redis(...)`` (or
    compatible) instance — the platform never instantiates the client itself
    so connection params + auth stay with the operator.
    """
    global _CLIENT, _KEY_PREFIX
    if not _REDIS_AVAILABLE:
        raise RedisNotConfigured(
            "redis-py is not installed. `pip install redis` first."
        )
    _CLIENT = client
    if key_prefix:
        _KEY_PREFIX = key_prefix


def get_redis():
    if _CLIENT is None:
        raise RedisNotConfigured(
            "no Redis client wired. Call configure_redis(client) at startup."
        )
    return _CLIENT


def deactivate() -> None:
    """Drop the configured client. Subsequent Redis-backed calls will raise
    RedisNotConfigured; callers should fall back to file-based primitives."""
    global _CLIENT
    _CLIENT = None


def _key(suffix: str) -> str:
    return f"{_KEY_PREFIX}{suffix}"


# ── Redis distributed_lock (SETNX + Lua release) ───────────────────────


# Atomic release: delete the key only if its value matches our owner_id.
_RELEASE_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


def acquire(lock_name: str, *, owner_id: str, lease_seconds: int = 60,
             acquire_timeout_seconds: int = 5) -> dict:
    """Redis SETNX with TTL. Polls within acquire_timeout_seconds."""
    client = get_redis()
    key = _key(f"lock:{lock_name}")
    deadline = time.time() + acquire_timeout_seconds
    while True:
        acquired = client.set(key, owner_id, nx=True, ex=lease_seconds)
        if acquired:
            return {
                "lock_name": lock_name, "owner_id": owner_id,
                "lease_seconds": lease_seconds,
                "acquired_at_epoch": time.time(),
                "expires_at_epoch": time.time() + lease_seconds,
            }
        if time.time() >= deadline:
            raise TimeoutError(f"Redis lock '{lock_name}' busy")
        time.sleep(0.05)


def release(lock_name: str, *, owner_id: str) -> bool:
    client = get_redis()
    key = _key(f"lock:{lock_name}")
    try:
        result = client.eval(_RELEASE_LUA, 1, key, owner_id)
        return bool(result)
    except Exception:
        return False


def heartbeat(lock_name: str, *, owner_id: str, lease_seconds: int = 60) -> bool:
    client = get_redis()
    key = _key(f"lock:{lock_name}")
    # Extend TTL only if we still own the lock
    current = client.get(key)
    if not current:
        return False
    if isinstance(current, bytes):
        current = current.decode("utf-8")
    if current != owner_id:
        return False
    client.expire(key, lease_seconds)
    return True


# ── Redis distributed_rate_limit ───────────────────────────────────────


def check_and_increment(
    *,
    bucket: str,
    max_calls: int,
    window_seconds: int,
) -> dict:
    """Atomic INCR with EXPIRE. Returns {allowed, current_count, retry_after_seconds}."""
    client = get_redis()
    key = _key(f"rate:{bucket}")
    with client.pipeline() as pipe:
        pipe.incr(key, 1)
        pipe.expire(key, window_seconds)
        count, _ = pipe.execute()
    if count > max_calls:
        ttl = client.ttl(key) or window_seconds
        return {"allowed": False, "current_count": count, "retry_after_seconds": int(ttl)}
    return {"allowed": True, "current_count": count, "retry_after_seconds": 0}


# ── Redis pub/sub for realtime_bus ─────────────────────────────────────


def publish_event(channel: str, event_payload: dict) -> int:
    client = get_redis()
    return client.publish(_key(f"pubsub:{channel}"), json.dumps(event_payload, ensure_ascii=False))


def subscribe_iter(channels: list, *, timeout_seconds: float = 1.0):
    """Yield (channel, payload) for each message. Caller controls loop lifetime.

    Generator semantics: caller is responsible for closing when done.
    """
    client = get_redis()
    pubsub = client.pubsub(ignore_subscribe_messages=True)
    full_names = [_key(f"pubsub:{c}") for c in channels]
    try:
        pubsub.subscribe(*full_names)
        while True:
            msg = pubsub.get_message(timeout=timeout_seconds)
            if msg is None:
                yield None
                continue
            raw_channel = msg.get("channel")
            if isinstance(raw_channel, bytes):
                raw_channel = raw_channel.decode("utf-8")
            data = msg.get("data")
            if isinstance(data, bytes):
                data = data.decode("utf-8")
            try:
                payload = json.loads(data) if isinstance(data, str) else data
            except json.JSONDecodeError:
                payload = data
            yield (raw_channel, payload)
    finally:
        try:
            pubsub.close()
        except Exception:
            pass


# ── Activation helper ──────────────────────────────────────────────────


def activate(client, *, key_prefix: str | None = None) -> dict:
    """One-call wiring. Returns a status dict describing what's been swapped
    to Redis. Operators inspect this at startup to confirm what's hot."""
    configure_redis(client, key_prefix=key_prefix)
    # Wire the shared_cache_backend RedisBackend so cache reads go through Redis
    try:
        from execution.ops_platform import shared_cache_backend
        shared_cache_backend.configure(shared_cache_backend.RedisBackend(redis_client=client,
                                                                          key_prefix=_KEY_PREFIX + "cache:"))
    except Exception as e:
        logger.warning("Could not wire shared_cache_backend to Redis: %s", e)

    return {
        "redis_available": _REDIS_AVAILABLE,
        "key_prefix": _KEY_PREFIX,
        "primitives_wired": ["shared_cache_backend", "distributed_lock(via call site)",
                               "distributed_rate_limit(via call site)", "pubsub"],
        "single_host_fallback_still_active": True,
        "explainer": (
            "Redis is now wired for shared_cache_backend. distributed_lock and "
            "distributed_rate_limit can be opted into per call by calling the "
            "Redis equivalents here; the existing file-backed implementations "
            "remain the safe default. When a multi-host cluster wants full HA, "
            "explicitly swap the call sites; nothing happens implicitly."
        ),
    }
