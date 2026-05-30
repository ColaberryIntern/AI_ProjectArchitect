"""WebSocket gateway helpers.

Scope honesty
-------------
- The WebSocket endpoint itself lives in app/routers/ops_platform.py (FastAPI
  ``@router.websocket("/realtime/ws")``). This module only ships the
  coordination helpers.
- **Local-only coordination by default.** A WebSocket client connected to
  worker A receives only events emitted from worker A's process (via
  ``realtime_bus`` in-process fanout).
- **Cross-host coordination ONLY when ``redis_backends.activate(client)``
  is called.** ``broadcast()`` then publishes to a Redis channel; every
  WebSocket gateway in the cluster picks the event up via ``subscribe_iter``
  and fans it out locally.
- ``mode()`` reports the active coordination scope so the dashboard can
  display "local-only" or "redis-pubsub" honestly.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Iterator

from execution.ops_platform import realtime_bus, redis_backends

logger = logging.getLogger(__name__)

WS_PUBSUB_CHANNEL = "ws.broadcast"


def mode() -> dict:
    """Report the WebSocket coordination scope."""
    redis_ready = redis_backends.is_available() and redis_backends._CLIENT is not None
    return {
        "redis_py_installed": redis_backends.is_available(),
        "redis_client_wired": redis_backends._CLIENT is not None,
        "coordination_scope": ("redis-pubsub-multi-host" if redis_ready
                                  else "local-only-single-process"),
        "explainer": (
            "Without a wired Redis client, WebSocket events are local to "
            "the process that emits them. Multi-host fanout requires "
            "redis_backends.activate(client) at startup."
        ),
    }


def broadcast(event_type: str, payload: dict, *,
                actor: dict | None = None,
                correlation_id: str | None = None) -> dict:
    """Emit an event for cross-WebSocket fanout. Always lands on the local
    realtime_bus. When Redis is wired, also publishes to Redis pub/sub for
    cross-host fanout. Returns the delivery status dict."""
    event = realtime_bus.emit(event_type, actor=actor, payload=payload,
                                 correlation_id=correlation_id,
                                 mirror_to_audit=False)
    delivery = {"local": True, "redis": False, "event_id": event.event_id}
    try:
        if redis_backends.is_available() and redis_backends._CLIENT is not None:
            redis_backends.publish_event(WS_PUBSUB_CHANNEL, event.to_dict())
            delivery["redis"] = True
    except Exception:
        logger.debug("ws_gateway redis publish failed", exc_info=True)
    return delivery


def cross_host_listener_loop(stop_event: threading.Event) -> Iterator[dict]:
    """Generator that bridges Redis pub/sub events into the local realtime_bus.

    Run in a daemon thread when Redis is active. Each Redis-delivered event
    is re-emitted locally so a connected WebSocket client on this host sees
    events from other hosts.
    """
    if not (redis_backends.is_available() and redis_backends._CLIENT is not None):
        return
    try:
        for item in redis_backends.subscribe_iter([WS_PUBSUB_CHANNEL]):
            if stop_event.is_set():
                return
            if item is None:
                continue
            _, payload = item
            if not isinstance(payload, dict):
                continue
            yield payload
            try:
                realtime_bus.emit(
                    payload.get("event_type", "unknown"),
                    actor=payload.get("actor"),
                    correlation_id=payload.get("correlation_id"),
                    payload=payload.get("payload") or {},
                    mirror_to_audit=False,
                )
            except Exception:
                logger.debug("ws_gateway cross-host re-emit failed", exc_info=True)
    except Exception:
        logger.warning("ws_gateway cross-host listener errored", exc_info=True)
