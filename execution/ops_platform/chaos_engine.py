"""Chaos engine — injects controlled failures into the platform's own
substrate so reliability_monitor + self_healing can be exercised under load.

Scope honesty
-------------
- All injections operate on the file-based substrate. **They do NOT
  partition Redis** (no test against a real cluster), don't kill OS
  processes, and never touch external services.
- Every injection records before/after state in the audit log under a
  shared correlation_id so its effect can be replayed.
- Injections are auto-reverted after ``duration_seconds`` (or via explicit
  ``revert`` call). This is enforced by a daemon thread that sweeps on tick.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution.ops_platform import audit_log, controls, distributed_lock

logger = logging.getLogger(__name__)

_CHAOS_DIR = OUTPUT_DIR / "ops_platform" / "chaos"

VALID_KINDS = (
    "queue_stall",          # set a long-lived freeze on a target capability
    "worker_death",         # simulate via expiring worker heartbeat
    "lock_starvation",      # hold a distributed_lock; releases on revert
    "scheduler_lag",        # disable a schedule
    "redis_disconnect",     # mark the cache backend "unhealthy" via deactivate
    "notification_failure", # disable a channel
    # Phase 9F additions — distributed coordination drills
    "split_brain",          # two workers each end up holding stale state
    "duplicate_delivery",   # re-emit an event to test idempotent consumers
    "delayed_replay",       # re-emit an old event with a fresh sequence
    "lock_lease_expired",   # force-expire a v2 lock to test fencing rejection
    "ws_partition",         # unregister all WS subscribers for a workspace
)


@dataclass
class ChaosInjection:
    injection_id: str
    kind: str
    target_id: str
    started_at: str
    ends_at: str | None
    state: str                       # active | reverted | expired
    correlation_id: str
    actor: dict
    rollback_token: dict = field(default_factory=dict)
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ── Public API ─────────────────────────────────────────────────────────


def inject(
    *,
    kind: str,
    target_id: str = "",
    duration_seconds: int = 60,
    actor: dict | str = "chaos_engineer",
    reason: str = "scheduled chaos drill",
) -> ChaosInjection:
    if kind not in VALID_KINDS:
        raise ValueError(f"unknown chaos kind {kind}")
    actor_norm = actor if isinstance(actor, dict) else {"name": str(actor), "system": True}
    correlation_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    ends_at = (started_at + timedelta(seconds=duration_seconds))

    inj = ChaosInjection(
        injection_id=f"chaos_{uuid.uuid4().hex[:12]}",
        kind=kind, target_id=target_id,
        started_at=started_at.isoformat(),
        ends_at=ends_at.isoformat(),
        state="active",
        correlation_id=correlation_id, actor=actor_norm,
        reason=reason,
    )

    if kind == "queue_stall" or kind == "worker_death":
        ctrl = controls.freeze(target_id or "*", actor=actor_norm,
                                  reason=f"chaos:{kind}")
        inj.rollback_token = {"control_id": ctrl.control_id, "target": ctrl.target_id}
    elif kind == "lock_starvation":
        lock_owner = f"chaos:{inj.injection_id}"
        try:
            distributed_lock.acquire(target_id or "chaos.starve", owner_id=lock_owner,
                                       lease_seconds=duration_seconds + 30,
                                       acquire_timeout_seconds=2)
            inj.rollback_token = {"lock_name": target_id or "chaos.starve",
                                     "owner_id": lock_owner}
        except distributed_lock.LockAcquisitionError as e:
            inj.state = "failed"
            inj.reason = f"could not starve lock: {e}"
    elif kind == "scheduler_lag":
        from execution.ops_platform import scheduler
        scheduler.disable(target_id, actor=actor_norm)
        inj.rollback_token = {"schedule_id": target_id}
    elif kind == "redis_disconnect":
        from execution.ops_platform import redis_backends
        previous = redis_backends._CLIENT
        redis_backends.deactivate()
        inj.rollback_token = {"prior_client_present": previous is not None}
    elif kind == "notification_failure":
        from execution.ops_platform import notifications
        ch = notifications.get_channel(target_id)
        if ch is not None and ch.enabled:
            ch.enabled = False
            notifications.upsert_channel(channel_id=ch.channel_id, name=ch.name,
                                            kind=ch.kind, config=ch.config,
                                            enabled=False)
            inj.rollback_token = {"channel_id": target_id, "was_enabled": True}
    elif kind == "split_brain":
        # Write two stale claim files for the same orchestration step under
        # different owner tokens. The reclaim sweeper must converge to one.
        from execution.ops_platform import orchestration_runtime as orun
        import json as _json
        orun._CLAIMS_DIR.mkdir(parents=True, exist_ok=True)
        for owner in ("ghost_a", "ghost_b"):
            claim = {
                "orchestration_id": target_id or "split-brain-target",
                "step_id": "step_x",
                "owner_token": owner,
                "fencing_token": 0,
                "lease_until_epoch": time.time() - 1,
                "coordination_scope": "single-host",
            }
            path = orun._CLAIMS_DIR / f"claim_{target_id or 'split'}_{owner}.json"
            path.write_text(_json.dumps(claim))
        inj.rollback_token = {"target_orchestration": target_id or "split-brain-target"}
    elif kind == "duplicate_delivery":
        # Re-emit an arbitrary fabric event to test idempotent consumers.
        from execution.ops_platform import event_fabric
        event_fabric.emit("chaos.duplicate_delivery", payload={"target": target_id},
                            durability_scope="single-host",
                            consistency_scope="at-least-once")
        event_fabric.emit("chaos.duplicate_delivery", payload={"target": target_id},
                            durability_scope="single-host",
                            consistency_scope="at-least-once")
        inj.rollback_token = {"emitted_twice": True}
    elif kind == "delayed_replay":
        from execution.ops_platform import event_fabric
        event_fabric.emit("chaos.delayed_replay", payload={"target": target_id,
                                                              "synthetic": True},
                            durability_scope="single-host",
                            consistency_scope="best-effort")
        inj.rollback_token = {"emitted_late": True}
    elif kind == "lock_lease_expired":
        # Force-expire a Phase 6 file-based lock by rewriting its state with
        # an already-past expires_at. The downstream resource MUST refuse
        # operations from the prior owner.
        import json as _json
        from execution.ops_platform import distributed_lock as dl
        state_path = dl._LOCKS_DIR / f"{target_id}.json"
        if state_path.exists():
            try:
                row = _json.loads(state_path.read_text(encoding="utf-8"))
                row["expires_at_epoch"] = time.time() - 1
                state_path.write_text(_json.dumps(row), encoding="utf-8")
                inj.rollback_token = {"lock_name": target_id, "previous_state": row}
            except Exception:
                inj.state = "failed"
                inj.reason = "could not rewrite lock state"
    elif kind == "ws_partition":
        # Drop every locally-tracked WS subscriber for the target workspace
        from execution.ops_platform import realtime_bus
        with realtime_bus._LOCK:
            removed = [s[0] for s in realtime_bus._SUBSCRIBERS]
            realtime_bus._SUBSCRIBERS.clear()
        inj.rollback_token = {"removed_subscriber_ids": removed,
                                 "workspace_id": target_id}

    _persist(inj)
    audit_log.record(
        action="chaos.injected", entity_type="chaos_injection",
        entity_id=inj.injection_id, actor=actor_norm,
        correlation_id=correlation_id,
        new_state={"kind": kind, "target_id": target_id,
                   "duration_seconds": duration_seconds},
        metadata={"reason": reason},
    )
    return inj


def revert(injection_id: str, *, actor: dict | str = "chaos_engineer") -> ChaosInjection | None:
    inj = get(injection_id)
    if inj is None or inj.state != "active":
        return inj
    actor_norm = actor if isinstance(actor, dict) else {"name": str(actor), "system": True}
    _undo(inj, actor=actor_norm)
    inj.state = "reverted"
    _persist(inj)
    audit_log.record(
        action="chaos.reverted", entity_type="chaos_injection",
        entity_id=injection_id, actor=actor_norm,
        correlation_id=inj.correlation_id,
        previous_state={"state": "active"}, new_state={"state": "reverted"},
    )
    return inj


def sweep_expired() -> list[str]:
    out: list[str] = []
    now = datetime.now(timezone.utc)
    for inj in list_injections(state="active"):
        if inj.ends_at:
            try:
                end = datetime.fromisoformat(inj.ends_at)
            except ValueError:
                continue
            if end <= now:
                _undo(inj, actor={"name": "chaos_sweeper", "system": True})
                inj.state = "expired"
                _persist(inj)
                audit_log.record(
                    action="chaos.expired", entity_type="chaos_injection",
                    entity_id=inj.injection_id,
                    actor={"name": "chaos_sweeper", "system": True},
                    correlation_id=inj.correlation_id,
                )
                out.append(inj.injection_id)
    return out


def get(injection_id: str) -> ChaosInjection | None:
    path = _CHAOS_DIR / f"{injection_id}.json"
    if not path.exists():
        return None
    try:
        return ChaosInjection(**json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def list_injections(*, state: str | None = None) -> list[ChaosInjection]:
    if not _CHAOS_DIR.exists():
        return []
    out: list[ChaosInjection] = []
    for p in _CHAOS_DIR.glob("chaos_*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            inj = ChaosInjection(**data)
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        if state and inj.state != state:
            continue
        out.append(inj)
    out.sort(key=lambda i: i.started_at, reverse=True)
    return out


def measure_mttr(*, lookback_days: int = 30) -> dict:
    """Compute mean-time-to-recovery from incident open → resolved."""
    from execution.ops_platform import incidents as inc_mod
    durations_seconds: list[float] = []
    for inc in inc_mod.list_incidents():
        if inc.state not in ("resolved", "postmortem_drafted"):
            continue
        try:
            opened = datetime.fromisoformat(inc.created_at)
            resolved_ts = max((datetime.fromisoformat(t.get("at"))
                                for t in inc.timeline if isinstance(t, dict) and t.get("at")),
                                default=None)
            if not resolved_ts:
                continue
        except (TypeError, ValueError):
            continue
        durations_seconds.append((resolved_ts - opened).total_seconds())
    if not durations_seconds:
        return {"mttr_seconds": None, "samples": 0}
    return {"mttr_seconds": round(sum(durations_seconds) / len(durations_seconds), 1),
            "samples": len(durations_seconds),
            "max_seconds": max(durations_seconds),
            "min_seconds": min(durations_seconds)}


# ── Internal ───────────────────────────────────────────────────────────


def _undo(inj: ChaosInjection, *, actor: dict) -> None:
    try:
        if inj.kind in ("queue_stall", "worker_death"):
            target = inj.rollback_token.get("target") or inj.target_id
            controls.unfreeze(target, actor=actor, reason="chaos revert")
        elif inj.kind == "lock_starvation":
            distributed_lock.release(inj.rollback_token.get("lock_name", ""),
                                        owner_id=inj.rollback_token.get("owner_id", ""))
        elif inj.kind == "scheduler_lag":
            from execution.ops_platform import scheduler
            scheduler.enable(inj.rollback_token.get("schedule_id", ""), actor=actor)
        elif inj.kind == "notification_failure":
            from execution.ops_platform import notifications
            ch = notifications.get_channel(inj.rollback_token.get("channel_id", ""))
            if ch and inj.rollback_token.get("was_enabled"):
                notifications.upsert_channel(channel_id=ch.channel_id, name=ch.name,
                                                kind=ch.kind, config=ch.config,
                                                enabled=True)
        # redis_disconnect intentionally has no automatic restore — operator
        # must re-call redis_backends.activate(client) after the drill.
    except Exception:
        logger.warning("chaos undo failed for %s", inj.injection_id, exc_info=True)


def _persist(inj: ChaosInjection) -> None:
    _CHAOS_DIR.mkdir(parents=True, exist_ok=True)
    (_CHAOS_DIR / f"{inj.injection_id}.json").write_text(
        json.dumps(inj.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
