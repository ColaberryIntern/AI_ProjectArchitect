"""Unified event fabric — single normalized event log every operational
subsystem publishes to.

Scope honesty
-------------
Every event carries TWO explicit scope tags that the operator can inspect:

  - ``durability_scope``  ∈ {"local-process", "single-host", "redis-distributed",
                              "operator-managed"}
  - ``consistency_scope`` ∈ {"strict-per-stream", "at-least-once",
                              "best-effort", "operator-managed"}

These are NOT inferred. The emitter declares them. If an emitter cannot honestly
declare strict-per-stream, it declares at-least-once and we live with the
duplicate-delivery semantics.

Persistence
-----------
- Single-host log: ``output/ops_platform/event_fabric/{date}.jsonl``
- Monotonic ``sequence`` via file-locked counter.
- Retention sweep on read (default 30 days).
- Redis Streams adapter (Phase 9B) reads from the same shape — emitters don't
  change when distribution is wired.

This module supersedes ``realtime_bus`` for new emitters. Existing
``realtime_bus.emit()`` calls keep working; an adapter forwards them into the
fabric so the unified log gets every event.

Causation
---------
Events carry ``correlation_id`` (logical activity grouping, as before) plus
``causation_id`` (the immediate parent event id). The combination lets
``projection_engine`` reconstruct causal chains, not just temporal proximity.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator

from filelock import FileLock

from config.settings import OUTPUT_DIR

logger = logging.getLogger(__name__)

_EVENTS_DIR = OUTPUT_DIR / "ops_platform" / "event_fabric"
_SEQUENCE_PATH = OUTPUT_DIR / "ops_platform" / "event_fabric_sequence.json"
_LOCK = threading.Lock()

RETENTION_DAYS = 30
DEFAULT_HEARTBEAT_SECONDS = 15.0

# Scope vocabularies — closed sets so reports stay honest.
DURABILITY_SCOPES = ("local-process", "single-host", "redis-distributed",
                      "operator-managed")
CONSISTENCY_SCOPES = ("strict-per-stream", "at-least-once",
                       "best-effort", "operator-managed")


@dataclass
class FabricEvent:
    event_id: str
    event_type: str
    sequence: int
    timestamp: str
    workspace_id: str | None
    actor_id: str | None
    correlation_id: str | None
    causation_id: str | None
    payload: dict
    evidence_refs: list
    replayable: bool
    durability_scope: str
    consistency_scope: str

    def to_dict(self) -> dict:
        return asdict(self)

    def to_sse(self) -> str:
        lines = [
            f"id: {self.sequence}",
            f"event: {self.event_type}",
            f"data: {json.dumps(self.to_dict(), ensure_ascii=False)}",
        ]
        return "\n".join(lines) + "\n\n"


# ── Subscribers (in-process fanout) ───────────────────────────────────


_SUBSCRIBERS: list = []


def subscribe(
    *,
    workspace_id: str | None = None,
    event_types: list | None = None,
    capacity: int = 256,
) -> tuple[str, "deque[FabricEvent]", threading.Event]:
    sub_id = uuid.uuid4().hex[:12]
    q: deque[FabricEvent] = deque(maxlen=capacity)
    notify = threading.Event()
    type_filter = set(event_types) if event_types else None

    def matches(ev: FabricEvent) -> bool:
        if workspace_id and ev.workspace_id and ev.workspace_id != workspace_id:
            return False
        if type_filter and ev.event_type not in type_filter:
            return False
        return True

    with _LOCK:
        _SUBSCRIBERS.append((sub_id, q, notify, matches))
    return sub_id, q, notify


def unsubscribe(subscriber_id: str) -> None:
    with _LOCK:
        for i, entry in enumerate(list(_SUBSCRIBERS)):
            if entry[0] == subscriber_id:
                _SUBSCRIBERS.pop(i)
                return


# ── Emit ──────────────────────────────────────────────────────────────


def emit(
    event_type: str,
    *,
    payload: dict | None = None,
    actor_id: str | None = None,
    workspace_id: str | None = None,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    evidence_refs: list | None = None,
    replayable: bool = True,
    durability_scope: str = "single-host",
    consistency_scope: str = "at-least-once",
) -> FabricEvent:
    """Append one event. The emitter declares the scopes — the fabric does
    NOT silently upgrade them. Raises ValueError if the scopes are outside
    the closed vocabulary."""
    if durability_scope not in DURABILITY_SCOPES:
        raise ValueError(f"durability_scope must be one of {DURABILITY_SCOPES}")
    if consistency_scope not in CONSISTENCY_SCOPES:
        raise ValueError(f"consistency_scope must be one of {CONSISTENCY_SCOPES}")
    seq = _next_sequence()
    ev = FabricEvent(
        event_id=uuid.uuid4().hex,
        event_type=event_type,
        sequence=seq,
        timestamp=datetime.now(timezone.utc).isoformat(),
        workspace_id=workspace_id,
        actor_id=actor_id,
        correlation_id=correlation_id,
        causation_id=causation_id,
        payload=dict(payload or {}),
        evidence_refs=list(evidence_refs or []),
        replayable=replayable,
        durability_scope=durability_scope,
        consistency_scope=consistency_scope,
    )
    _persist(ev)
    _fanout_local(ev)
    _maybe_publish_redis(ev)
    return ev


def replay(
    *,
    since_sequence: int = 0,
    workspace_id: str | None = None,
    event_types: list | None = None,
    causation_id: str | None = None,
    correlation_id: str | None = None,
    limit: int = 500,
) -> list[FabricEvent]:
    out: list[FabricEvent] = []
    type_filter = set(event_types) if event_types else None
    if not _EVENTS_DIR.exists():
        return []
    _sweep_old_files()
    for p in sorted(_EVENTS_DIR.glob("*.jsonl")):
        try:
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if data.get("sequence", 0) <= since_sequence:
                        continue
                    if workspace_id and data.get("workspace_id") and data["workspace_id"] != workspace_id:
                        continue
                    if type_filter and data.get("event_type") not in type_filter:
                        continue
                    if causation_id and data.get("causation_id") != causation_id:
                        continue
                    if correlation_id and data.get("correlation_id") != correlation_id:
                        continue
                    try:
                        out.append(FabricEvent(**data))
                    except TypeError:
                        continue
                    if len(out) >= limit:
                        return out
        except OSError:
            continue
    return out


def stream(
    *,
    workspace_id: str | None = None,
    event_types: list | None = None,
    since_sequence: int = 0,
    heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
    stop_after_seconds: float | None = None,
) -> Iterator[str]:
    for ev in replay(since_sequence=since_sequence, workspace_id=workspace_id,
                       event_types=event_types):
        yield ev.to_sse()
        since_sequence = ev.sequence

    sub_id, q, notify = subscribe(workspace_id=workspace_id,
                                     event_types=event_types)
    started = time.time()
    try:
        while True:
            if stop_after_seconds and (time.time() - started) >= stop_after_seconds:
                return
            triggered = notify.wait(timeout=heartbeat_seconds)
            if triggered:
                notify.clear()
                while q:
                    ev = q.popleft()
                    if ev.sequence > since_sequence:
                        yield ev.to_sse()
                        since_sequence = ev.sequence
            else:
                yield ": heartbeat\n\n"
    finally:
        unsubscribe(sub_id)


def consistency_report() -> dict:
    """Tally the durability/consistency declarations actually present in the
    log. Useful for posture reviews — operators see what scopes are actually
    in use."""
    from collections import Counter
    durability: Counter = Counter()
    consistency: Counter = Counter()
    for ev in replay(limit=2000):
        durability[ev.durability_scope] += 1
        consistency[ev.consistency_scope] += 1
    return {
        "samples": sum(durability.values()),
        "by_durability_scope": dict(durability),
        "by_consistency_scope": dict(consistency),
        "vocabularies": {
            "durability": list(DURABILITY_SCOPES),
            "consistency": list(CONSISTENCY_SCOPES),
        },
    }


def reset_for_tests() -> None:
    with _LOCK:
        _SUBSCRIBERS.clear()
    try:
        if _SEQUENCE_PATH.exists():
            _SEQUENCE_PATH.unlink()
    except OSError:
        pass


# ── Adapter: realtime_bus → fabric ─────────────────────────────────────


def install_realtime_bus_adapter() -> None:
    """Wire existing ``realtime_bus.emit`` to also append to the fabric.
    Idempotent. Single source of truth without breaking the older API."""
    from execution.ops_platform import realtime_bus
    if getattr(realtime_bus, "_fabric_adapter_installed", False):
        return
    original_emit = realtime_bus.emit

    def shim(event_type: str, *, actor=None, workspace_id=None,
              correlation_id=None, payload=None, mirror_to_audit=True):
        result = original_emit(event_type, actor=actor, workspace_id=workspace_id,
                                  correlation_id=correlation_id,
                                  payload=payload, mirror_to_audit=mirror_to_audit)
        try:
            actor_id = (actor or {}).get("name") if isinstance(actor, dict) else (actor or "anonymous")
            emit(event_type, payload=payload or {}, actor_id=actor_id,
                   workspace_id=workspace_id, correlation_id=correlation_id,
                   durability_scope="single-host",
                   consistency_scope="at-least-once")
        except Exception:
            logger.debug("event_fabric adapter shim failed", exc_info=True)
        return result

    realtime_bus.emit = shim
    realtime_bus._fabric_adapter_installed = True


# ── Internal ───────────────────────────────────────────────────────────


def _persist(ev: FabricEvent) -> None:
    _EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.fromisoformat(ev.timestamp).date()
    path = _EVENTS_DIR / f"{day.isoformat()}.jsonl"
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(ev.to_dict(), ensure_ascii=False) + "\n")
    except OSError:
        logger.warning("event_fabric append failed", exc_info=True)


def _next_sequence() -> int:
    _SEQUENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    guard = FileLock(str(_SEQUENCE_PATH) + ".lock", timeout=5)
    with guard:
        current = 0
        if _SEQUENCE_PATH.exists():
            try:
                current = int(json.loads(_SEQUENCE_PATH.read_text(encoding="utf-8")).get("next", 0))
            except (OSError, json.JSONDecodeError, TypeError):
                current = 0
        next_seq = current + 1
        try:
            _SEQUENCE_PATH.write_text(json.dumps({"next": next_seq}), encoding="utf-8")
        except OSError:
            pass
        return next_seq


def _fanout_local(ev: FabricEvent) -> None:
    with _LOCK:
        subscribers = list(_SUBSCRIBERS)
    for sub_id, q, notify, matches in subscribers:
        try:
            if matches(ev):
                q.append(ev)
                notify.set()
        except Exception:
            logger.debug("fabric local fanout failed", exc_info=True)


def _maybe_publish_redis(ev: FabricEvent) -> None:
    """Forward to ``distributed_event_bus`` when Redis is wired. Best-effort —
    failures never block the local persist."""
    try:
        from execution.ops_platform import distributed_event_bus, redis_backends
        if not redis_backends.is_available() or redis_backends._CLIENT is None:
            return
        distributed_event_bus.publish(ev)
    except Exception:
        logger.debug("fabric redis publish failed", exc_info=True)


def _sweep_old_files() -> None:
    if not _EVENTS_DIR.exists():
        return
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=RETENTION_DAYS)
    for p in _EVENTS_DIR.glob("*.jsonl"):
        try:
            day = datetime.fromisoformat(p.stem).date()
        except ValueError:
            continue
        if day < cutoff:
            try:
                p.unlink()
            except OSError:
                pass
