"""Coordination diagnostics — topology, lag, lock contention, replay backlog.

Surfaces consolidated views for the operator dashboard. Read-only.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from execution.ops_platform import (
    distributed_event_bus, distributed_lock, distributed_presence,
    event_fabric, orchestration_engine, orchestration_runtime,
    redis_backends, runtime_queue, ws_gateway,
)

logger = logging.getLogger(__name__)


def coordination_topology() -> dict:
    """Single dict that names every coordination boundary the operator
    currently has, plus the active scope."""
    redis_ready = redis_backends.is_available() and redis_backends._CLIENT is not None
    presence_mode = distributed_presence.mode() if redis_ready else {
        "scope": "per-process-only", "active": False}
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "redis_py_installed": redis_backends.is_available(),
        "redis_client_wired": redis_backends._CLIENT is not None,
        "event_fabric": {
            "scope": "single-host",
            "redis_mirror": redis_ready,
        },
        "ws_gateway": ws_gateway.mode(),
        "presence": presence_mode,
        "orchestration_runtime": orchestration_runtime.coordination_mode(),
        "fabric_consistency_report": event_fabric.consistency_report(),
    }


def lock_inspector(*, lock_names: list | None = None) -> list[dict]:
    """Inspect file-based locks AND (when Redis is wired) Redis locks.
    Returns one row per inspected lock."""
    out: list[dict] = []
    for name in lock_names or []:
        local = distributed_lock.is_held(name)
        if local:
            out.append({"lock_name": name, "scope": "file-based",
                          "state": local})
        if redis_backends.is_available() and redis_backends._CLIENT is not None:
            try:
                from execution.ops_platform import distributed_lock_v2
                redis_state = distributed_lock_v2.is_held(name)
                if redis_state:
                    out.append({"lock_name": name, "scope": "redis-distributed",
                                  "state": redis_state})
            except Exception:
                pass
    if not out and not lock_names:
        # Without explicit names, show all file-based active locks
        for entry in distributed_lock.list_active():
            out.append({"lock_name": entry.get("lock_name"),
                          "scope": "file-based", "state": entry})
    return out


def stream_lag_report(*, group: str = "ops_consumer",
                          event_types: list | None = None) -> dict:
    """Per-stream lag for the configured consumer group."""
    if not (redis_backends.is_available() and redis_backends._CLIENT is not None):
        return {
            "scope": "no-redis",
            "explainer": "stream lag requires a wired Redis client",
            "streams": [],
        }
    prefix = redis_backends._KEY_PREFIX
    targets = (event_types or [])
    if not targets:
        targets = ["*"]
    streams = []
    for t in targets:
        stream_key = f"{prefix}fabric:{t}"
        streams.append(distributed_event_bus.stream_lag(group=group,
                                                            stream_key=stream_key))
    return {"scope": "redis-distributed", "streams": streams, "group": group}


def replay_backlog() -> dict:
    """Aggregate: queue depth + active orchestrations + active claims."""
    qd = runtime_queue.queue_depth(queue="default")
    orchs = orchestration_engine.list_orchestrations()
    active = [o for o in orchs if o.state in ("running", "awaiting_approval",
                                                  "paused", "compensating")]
    return {
        "queue_total": qd.get("total", 0),
        "queue_counts": qd.get("counts", {}),
        "active_orchestrations": len(active),
        "active_orchestration_ids": [o.orchestration_id for o in active][:25],
        "active_step_claims": len(orchestration_runtime.list_active_claims()),
    }


def orphan_orchestrations(*, age_minutes: int = 30) -> list[dict]:
    return orchestration_runtime.stuck_orchestrations(age_minutes=age_minutes)


def cluster_health() -> dict:
    """One-shot readiness probe."""
    topology = coordination_topology()
    backlog = replay_backlog()
    issues: list[str] = []
    if not topology["redis_client_wired"]:
        issues.append("Redis client not wired — multi-host coordination disabled")
    if backlog["queue_total"] > 1000:
        issues.append(f"queue depth {backlog['queue_total']} is high")
    if backlog["active_orchestrations"] > 50:
        issues.append("many concurrent orchestrations — verify worker count")
    return {
        "ready": not bool(issues) or all("Redis client not wired" in i for i in issues),
        "issues": issues,
        "topology": topology,
        "backlog": backlog,
    }
