"""Distributed presence — Redis-backed operator + subscription presence.

Scope honesty
-------------
- Multi-host when Redis is wired. Each presence row is one Redis hash
  with TTL; heartbeat extends TTL; expiration removes the row.
- Without Redis: returns ``[]`` and the caller's ``mode()`` reports
  ``per-process-only``. No silent fallback to file-based presence.

Resource keys
-------------
``{prefix}presence:{workspace_id}:{user_id}``   — hash with ``last_seen_at``,
                                                  ``currently_viewing``, etc.
``{prefix}ws:subscribers``                       — set of active WS sessions
                                                  (cross-host visibility)
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass

from execution.ops_platform import redis_backends

logger = logging.getLogger(__name__)

DEFAULT_PRESENCE_TTL_SECONDS = 90


@dataclass
class PresenceRow:
    workspace_id: str
    user_id: str
    display_name: str
    last_seen_at: str
    currently_viewing: str | None = None
    currently_editing: str | None = None
    host_id: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def mode() -> dict:
    if redis_backends.is_available() and redis_backends._CLIENT is not None:
        return {"scope": "redis-distributed-multi-host", "active": True}
    return {"scope": "per-process-only",
              "active": False,
              "explainer": "wire redis_backends.activate(client) for cross-host presence"}


def heartbeat(
    *,
    workspace_id: str,
    user_id: str,
    display_name: str = "",
    currently_viewing: str | None = None,
    currently_editing: str | None = None,
    host_id: str | None = None,
    ttl_seconds: int = DEFAULT_PRESENCE_TTL_SECONDS,
) -> PresenceRow:
    if not (redis_backends.is_available() and redis_backends._CLIENT is not None):
        raise redis_backends.RedisNotConfigured(
            "distributed_presence requires a wired Redis client"
        )
    client = redis_backends.get_redis()
    key = _key(workspace_id, user_id)
    row = PresenceRow(
        workspace_id=workspace_id, user_id=user_id,
        display_name=display_name or user_id,
        last_seen_at=_now_iso(),
        currently_viewing=currently_viewing,
        currently_editing=currently_editing,
        host_id=host_id,
    )
    mapping = {k: (v if v is not None else "") for k, v in row.to_dict().items()}
    try:
        # HSET is variadic in redis-py 4.x; pass mapping kwarg
        client.hset(key, mapping=mapping)
        client.expire(key, ttl_seconds)
    except Exception:
        logger.warning("presence hset failed", exc_info=True)
    return row


def leave(*, workspace_id: str, user_id: str) -> bool:
    if not (redis_backends.is_available() and redis_backends._CLIENT is not None):
        raise redis_backends.RedisNotConfigured("distributed_presence requires Redis")
    client = redis_backends.get_redis()
    try:
        n = client.delete(_key(workspace_id, user_id))
        return int(n) > 0
    except Exception:
        return False


def list_active(workspace_id: str) -> list[PresenceRow]:
    if not (redis_backends.is_available() and redis_backends._CLIENT is not None):
        raise redis_backends.RedisNotConfigured("distributed_presence requires Redis")
    client = redis_backends.get_redis()
    prefix = redis_backends._KEY_PREFIX
    pattern = f"{prefix}presence:{workspace_id}:*"
    out: list[PresenceRow] = []
    try:
        for k in client.scan_iter(match=pattern, count=200):
            if isinstance(k, bytes):
                k = k.decode("utf-8")
            row = client.hgetall(k)
            decoded = {
                (kk.decode("utf-8") if isinstance(kk, bytes) else kk):
                  (vv.decode("utf-8") if isinstance(vv, bytes) else vv)
                for kk, vv in (row.items() if isinstance(row, dict) else row)
            }
            if not decoded:
                continue
            decoded.setdefault("currently_viewing", None) or None
            decoded.setdefault("currently_editing", None) or None
            decoded.setdefault("host_id", None) or None
            try:
                out.append(PresenceRow(
                    workspace_id=decoded.get("workspace_id", workspace_id),
                    user_id=decoded.get("user_id", ""),
                    display_name=decoded.get("display_name", ""),
                    last_seen_at=decoded.get("last_seen_at", ""),
                    currently_viewing=decoded.get("currently_viewing") or None,
                    currently_editing=decoded.get("currently_editing") or None,
                    host_id=decoded.get("host_id") or None,
                ))
            except TypeError:
                continue
    except Exception:
        logger.warning("presence scan failed", exc_info=True)
    return out


def register_ws_subscriber(*, subscriber_id: str, host_id: str,
                              workspace_id: str | None = None,
                              ttl_seconds: int = 60) -> None:
    """Track a WebSocket session in a Redis set so cross-host topology
    queries see who is connected where."""
    if not (redis_backends.is_available() and redis_backends._CLIENT is not None):
        raise redis_backends.RedisNotConfigured(
            "register_ws_subscriber requires a wired Redis client"
        )
    client = redis_backends.get_redis()
    key = f"{redis_backends._KEY_PREFIX}ws:subscribers"
    member = f"{subscriber_id}|{host_id}|{workspace_id or ''}"
    try:
        client.sadd(key, member)
        client.expire(key, ttl_seconds * 4)  # set TTL on the set as a backstop
    except Exception:
        logger.warning("register_ws_subscriber failed", exc_info=True)


def unregister_ws_subscriber(*, subscriber_id: str, host_id: str,
                                 workspace_id: str | None = None) -> None:
    if not (redis_backends.is_available() and redis_backends._CLIENT is not None):
        return
    client = redis_backends.get_redis()
    key = f"{redis_backends._KEY_PREFIX}ws:subscribers"
    member = f"{subscriber_id}|{host_id}|{workspace_id or ''}"
    try:
        client.srem(key, member)
    except Exception:
        pass


def ws_topology() -> dict:
    if not (redis_backends.is_available() and redis_backends._CLIENT is not None):
        return {"scope": "per-process-only", "subscribers": []}
    client = redis_backends.get_redis()
    key = f"{redis_backends._KEY_PREFIX}ws:subscribers"
    try:
        members = client.smembers(key)
    except Exception:
        return {"scope": "redis-distributed-multi-host", "subscribers": []}
    out = []
    for m in members or []:
        if isinstance(m, bytes):
            m = m.decode("utf-8")
        parts = m.split("|", 2)
        if len(parts) == 3:
            out.append({"subscriber_id": parts[0], "host_id": parts[1],
                          "workspace_id": parts[2] or None})
    return {"scope": "redis-distributed-multi-host", "subscribers": out,
              "subscriber_count": len(out)}


# ── Internal ───────────────────────────────────────────────────────────


def _key(workspace_id: str, user_id: str) -> str:
    return f"{redis_backends._KEY_PREFIX}presence:{workspace_id}:{user_id}"


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
