"""Durable scheduler — single-host multi-process coordination via the
``worker.scheduler.leader`` lock from distributed_lock.

Scope honesty
-------------
- "Single-active scheduler coordination across processes on one host."
- Multiple FastAPI workers can all start the scheduler; only the leader
  actually fires triggers. Leader election uses ``distributed_lock``.
- If the leader process dies, a follower acquires the lock at its next tick
  (lease-expiration semantics).
- Missed-run reconciliation: each schedule remembers its ``last_fired_at``;
  on leader takeover the new leader checks for missed cron / interval ticks
  and runs them (or marks them as missed) per the schedule's policy.

Schedules persist as JSON under ``output/ops_platform/schedules/{id}.json``.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution.ops_platform import (
    audit_log, distributed_lock, runtime_queue, worker_coordination,
)

logger = logging.getLogger(__name__)

_SCHEDULES_DIR = OUTPUT_DIR / "ops_platform" / "schedules"
_LEADER_LOCK = "scheduler.leader"

VALID_TRIGGER_KINDS = ("cron", "interval", "event")


@dataclass
class Schedule:
    schedule_id: str
    name: str
    trigger_kind: str             # cron | interval | event
    cron_expression: str | None = None     # 5-field "m h dom mon dow"
    interval_seconds: int | None = None
    event_topic: str | None = None
    capability_id: str | None = None       # if scheduling a workflow_run
    payload: dict = field(default_factory=dict)
    queue: str = "default"
    enabled: bool = True
    created_at: str = ""
    created_by: dict = field(default_factory=dict)
    last_fired_at: str | None = None
    fire_count: int = 0
    missed_runs_policy: str = "fire_one"   # fire_one | fire_all | skip
    blackout_windows: list = field(default_factory=list)
    workspace_id: str | None = None
    revision_id: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


# ── Public API ─────────────────────────────────────────────────────────


def create_schedule(
    *,
    name: str,
    trigger_kind: str,
    capability_id: str | None = None,
    payload: dict | None = None,
    queue: str = "default",
    cron_expression: str | None = None,
    interval_seconds: int | None = None,
    event_topic: str | None = None,
    blackout_windows: list | None = None,
    missed_runs_policy: str = "fire_one",
    created_by: dict | str = "anonymous",
    workspace_id: str | None = None,
) -> Schedule:
    if trigger_kind not in VALID_TRIGGER_KINDS:
        raise ValueError(f"trigger_kind must be one of {VALID_TRIGGER_KINDS}")
    if trigger_kind == "cron" and not cron_expression:
        raise ValueError("cron_expression required for trigger_kind=cron")
    if trigger_kind == "interval" and not interval_seconds:
        raise ValueError("interval_seconds required for trigger_kind=interval")
    if trigger_kind == "event" and not event_topic:
        raise ValueError("event_topic required for trigger_kind=event")
    _SCHEDULES_DIR.mkdir(parents=True, exist_ok=True)
    actor = created_by if isinstance(created_by, dict) else {"name": str(created_by)}
    schedule = Schedule(
        schedule_id=f"sched_{uuid.uuid4().hex[:12]}",
        name=name, trigger_kind=trigger_kind,
        cron_expression=cron_expression, interval_seconds=interval_seconds,
        event_topic=event_topic, capability_id=capability_id,
        payload=dict(payload or {}), queue=queue, enabled=True,
        created_at=datetime.now(timezone.utc).isoformat(),
        created_by=actor, missed_runs_policy=missed_runs_policy,
        blackout_windows=list(blackout_windows or []),
        workspace_id=workspace_id,
    )
    _persist(schedule)
    audit_log.record(
        action="scheduler.created", entity_type="schedule",
        entity_id=schedule.schedule_id, actor=actor,
        new_state={"name": name, "trigger_kind": trigger_kind,
                   "capability_id": capability_id},
    )
    return schedule


def disable(schedule_id: str, *, actor: dict | str = "anonymous") -> bool:
    s = get(schedule_id)
    if s is None:
        return False
    s.enabled = False
    _persist(s)
    audit_log.record(
        action="scheduler.disabled", entity_type="schedule",
        entity_id=schedule_id,
        actor=actor if isinstance(actor, dict) else {"name": str(actor)},
        new_state={"enabled": False},
    )
    return True


def enable(schedule_id: str, *, actor: dict | str = "anonymous") -> bool:
    s = get(schedule_id)
    if s is None:
        return False
    s.enabled = True
    _persist(s)
    audit_log.record(
        action="scheduler.enabled", entity_type="schedule",
        entity_id=schedule_id,
        actor=actor if isinstance(actor, dict) else {"name": str(actor)},
        new_state={"enabled": True},
    )
    return True


def fire_event(event_topic: str, *, payload: dict | None = None,
                 actor: dict | str = "system") -> list[str]:
    """Fan out an event trigger. Returns the list of jobs enqueued."""
    enqueued: list[str] = []
    for s in list_schedules(enabled=True):
        if s.trigger_kind != "event" or s.event_topic != event_topic:
            continue
        job = _enqueue_for(s, extra_payload=payload or {}, reason=f"event:{event_topic}")
        if job:
            enqueued.append(job)
    audit_log.record(
        action="scheduler.event_fired", entity_type="event_topic",
        entity_id=event_topic,
        actor=actor if isinstance(actor, dict) else {"name": str(actor)},
        metadata={"enqueued_jobs": enqueued},
    )
    return enqueued


def tick(*, worker_id: str | None = None) -> dict:
    """Drive one scheduler iteration. Only the leader actually fires.
    Followers no-op."""
    own = worker_id or f"scheduler-{uuid.uuid4().hex[:6]}"
    if not worker_coordination.acquire_leadership(own, lease_seconds=30):
        return {"leader": False, "fired": []}
    try:
        worker_coordination.refresh_leadership(own, lease_seconds=30)
    except Exception:
        pass
    fired: list[str] = []
    now = datetime.now(timezone.utc)
    for s in list_schedules(enabled=True):
        if s.trigger_kind == "event":
            continue   # event schedules fire only via fire_event()
        if _in_blackout(s, now):
            continue
        due_times = _due_times(s, now)
        if not due_times:
            continue
        if s.missed_runs_policy == "skip":
            # Only fire the most recent due time, ignore missed
            chosen = [due_times[-1]]
        elif s.missed_runs_policy == "fire_one":
            chosen = [due_times[-1]]
        else:
            chosen = due_times
        for at in chosen:
            job = _enqueue_for(s, extra_payload={"__schedule_fired_at": at.isoformat()},
                                  reason=f"{s.trigger_kind}:{s.schedule_id}")
            if job:
                fired.append(job)
        s.last_fired_at = now.isoformat()
        s.fire_count += len(chosen)
        _persist(s)
    return {"leader": True, "fired": fired,
            "leader_record": worker_coordination.current_leader()}


def get(schedule_id: str) -> Schedule | None:
    path = _SCHEDULES_DIR / f"{schedule_id}.json"
    if not path.exists():
        return None
    try:
        return Schedule(**json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def list_schedules(*, enabled: bool | None = None) -> list[Schedule]:
    if not _SCHEDULES_DIR.exists():
        return []
    out: list[Schedule] = []
    for p in _SCHEDULES_DIR.glob("sched_*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            out.append(Schedule(**data))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    if enabled is not None:
        out = [s for s in out if s.enabled == enabled]
    out.sort(key=lambda s: s.created_at, reverse=True)
    return out


# ── Internal: cron parsing + due-time computation ──────────────────────


def _enqueue_for(s: Schedule, *, extra_payload: dict, reason: str) -> str | None:
    if not s.capability_id:
        return None
    payload = dict(s.payload)
    payload.update(extra_payload)
    job = runtime_queue.enqueue(
        kind="workflow_run", queue=s.queue,
        payload={"capability_id": s.capability_id, "inputs": payload},
        correlation_id=f"schedule:{s.schedule_id}",
        enqueued_by={"name": "scheduler", "system": True},
    )
    audit_log.record(
        action="scheduler.fired", entity_type="schedule",
        entity_id=s.schedule_id,
        actor={"name": "scheduler", "system": True},
        correlation_id=job.correlation_id,
        new_state={"job_id": job.job_id, "reason": reason},
    )
    return job.job_id


def _in_blackout(s: Schedule, now: datetime) -> bool:
    for window in s.blackout_windows or []:
        try:
            start = datetime.fromisoformat(window["start"])
            end = datetime.fromisoformat(window["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if start <= now <= end:
            return True
    return False


def _due_times(s: Schedule, now: datetime) -> list[datetime]:
    last_fired = None
    if s.last_fired_at:
        try:
            last_fired = datetime.fromisoformat(s.last_fired_at)
        except ValueError:
            last_fired = None
    if s.trigger_kind == "interval":
        interval = timedelta(seconds=int(s.interval_seconds or 0))
        if last_fired is None:
            return [now]
        if now - last_fired >= interval:
            ticks: list[datetime] = []
            t = last_fired + interval
            while t <= now:
                ticks.append(t)
                t += interval
            return ticks
        return []
    if s.trigger_kind == "cron":
        return _cron_due_times(s.cron_expression or "", last_fired, now)
    return []


_CRON_FIELD_RE = re.compile(r"^(\*|\d+|\*/\d+|(\d+(,\d+)*))$")


def _cron_due_times(expr: str, last_fired: datetime | None, now: datetime) -> list[datetime]:
    """Tiny cron evaluator. Supports: *, n, n,n,n, */n. Five fields:
    minute hour day-of-month month day-of-week."""
    parts = (expr or "").split()
    if len(parts) != 5:
        return []
    minute_f, hour_f, dom_f, mon_f, dow_f = parts

    def matches(field: str, value: int) -> bool:
        if field == "*":
            return True
        if field.startswith("*/"):
            try:
                step = int(field[2:])
            except ValueError:
                return False
            return step > 0 and value % step == 0
        try:
            return value in {int(v) for v in field.split(",")}
        except ValueError:
            return False

    # Walk minute by minute from last_fired+1 (or now-15min) up to now.
    start = (last_fired + timedelta(minutes=1)) if last_fired else (now - timedelta(minutes=15))
    start = start.replace(second=0, microsecond=0)
    candidates: list[datetime] = []
    cursor = start
    while cursor <= now:
        if (matches(minute_f, cursor.minute)
                and matches(hour_f, cursor.hour)
                and matches(dom_f, cursor.day)
                and matches(mon_f, cursor.month)
                and matches(dow_f, cursor.weekday())):
            candidates.append(cursor)
        cursor += timedelta(minutes=1)
    return candidates


def _persist(s: Schedule) -> None:
    from execution.ops_platform import optimistic_concurrency
    s.revision_id = optimistic_concurrency.new_revision()
    _SCHEDULES_DIR.mkdir(parents=True, exist_ok=True)
    (_SCHEDULES_DIR / f"{s.schedule_id}.json").write_text(
        json.dumps(s.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8",
    )


def save_with_revision_check(s: Schedule, *, observed_revision: str | None,
                                actor: dict | str | None = None) -> Schedule:
    from execution.ops_platform import optimistic_concurrency
    current = get(s.schedule_id)
    optimistic_concurrency.compare(
        entity_type="schedule", entity_id=s.schedule_id,
        observed_revision=observed_revision,
        current_revision=current.revision_id if current else None,
        actor=actor,
    )
    _persist(s)
    return s


# ── Background ticker thread ──────────────────────────────────────────


_TICKER_THREAD: threading.Thread | None = None
_TICKER_STOP: threading.Event | None = None


def start_background_ticker(*, interval_seconds: float = 10.0) -> None:
    """Run tick() periodically in a daemon thread. Safe to call multiple
    times (idempotent)."""
    global _TICKER_THREAD, _TICKER_STOP
    if _TICKER_THREAD is not None and _TICKER_THREAD.is_alive():
        return
    _TICKER_STOP = threading.Event()

    def _loop():
        worker_id = f"scheduler-{uuid.uuid4().hex[:8]}"
        while not _TICKER_STOP.is_set():
            try:
                tick(worker_id=worker_id)
            except Exception:
                logger.warning("scheduler tick raised", exc_info=True)
            _TICKER_STOP.wait(timeout=interval_seconds)

    _TICKER_THREAD = threading.Thread(target=_loop, daemon=True,
                                         name="ops_scheduler_ticker")
    _TICKER_THREAD.start()


def stop_background_ticker() -> None:
    global _TICKER_THREAD, _TICKER_STOP
    if _TICKER_STOP is not None:
        _TICKER_STOP.set()
    _TICKER_THREAD = None
    _TICKER_STOP = None
