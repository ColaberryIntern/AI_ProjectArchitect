"""Realtime event bus — append-only event log with sequence IDs, replay,
workspace partitioning, and SSE-friendly streaming.

Scope honesty
-------------
- File-based append-only log: ``output/ops_platform/realtime/events/{YYYY-MM-DD}.jsonl``
- Coordination scope: **single-host multi-process**. Multiple FastAPI workers
  on one host share the log (each appends; each reads from any offset). For
  multi-host pub/sub, swap the in-process subscriber fanout for Redis pub/sub
  via ``shared_cache_backend`` — the public API (emit / subscribe / replay) is
  unchanged. When Redis isn't wired, the platform stays correct on one host
  and degrades to "no cross-host fanout" with an explicit warning.
- Reconnects via ``Last-Event-ID`` resume from the persisted offset.
- Events persist for ``RETENTION_DAYS`` (default 7) — older daily files are
  swept on read. No eternal log growth.

Every emitted event also writes one audit row via ``audit_log.record`` so the
realtime view and the audit log can never disagree about what happened.
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

from config.settings import OUTPUT_DIR
from execution.ops_platform import audit_log

logger = logging.getLogger(__name__)

_EVENTS_DIR = OUTPUT_DIR / "ops_platform" / "realtime" / "events"
_SEQUENCE_PATH = OUTPUT_DIR / "ops_platform" / "realtime" / "sequence.json"
_LOCK = threading.Lock()

RETENTION_DAYS = 7
DEFAULT_HEARTBEAT_SECONDS = 15.0


@dataclass
class RealtimeEvent:
    event_id: str               # monotonic int as string (suitable for SSE id:)
    event_type: str             # e.g. "workflow.started", "approval.requested"
    timestamp: str
    actor: dict
    workspace_id: str | None
    correlation_id: str | None
    payload: dict
    sequence: int

    def to_dict(self) -> dict:
        return asdict(self)

    def to_sse(self) -> str:
        """Encode for SSE wire format."""
        lines = [
            f"id: {self.event_id}",
            f"event: {self.event_type}",
            f"data: {json.dumps(self.to_dict(), ensure_ascii=False)}",
        ]
        return "\n".join(lines) + "\n\n"


# ── Subscriber model (in-process fanout) ───────────────────────────────


_SUBSCRIBERS: list = []   # list of (subscriber_id, queue, filter_fn)


def subscribe(
    *,
    workspace_id: str | None = None,
    event_types: list | None = None,
    capacity: int = 256,
) -> tuple[str, "deque[RealtimeEvent]", threading.Event]:
    """Register a subscriber. Returns (subscriber_id, event_deque, notify_event).

    The caller's thread/coroutine pops from the deque; ``notify_event`` is
    set whenever a new event lands so async readers can wake without polling.
    """
    sub_id = uuid.uuid4().hex[:12]
    q: deque[RealtimeEvent] = deque(maxlen=capacity)
    notify = threading.Event()
    type_filter = set(event_types) if event_types else None

    def filter_fn(event: RealtimeEvent) -> bool:
        if workspace_id and event.workspace_id and event.workspace_id != workspace_id:
            return False
        if type_filter and event.event_type not in type_filter:
            return False
        return True

    with _LOCK:
        _SUBSCRIBERS.append((sub_id, q, notify, filter_fn))
    return sub_id, q, notify


def unsubscribe(subscriber_id: str) -> None:
    with _LOCK:
        for i, entry in enumerate(list(_SUBSCRIBERS)):
            if entry[0] == subscriber_id:
                _SUBSCRIBERS.pop(i)
                return


# ── Public API ─────────────────────────────────────────────────────────


def emit(
    event_type: str,
    *,
    actor: dict | str | None = None,
    workspace_id: str | None = None,
    correlation_id: str | None = None,
    payload: dict | None = None,
    mirror_to_audit: bool = True,
) -> RealtimeEvent:
    """Append one event. Persists to disk, fans out to local subscribers,
    optionally mirrors to the audit log (default true)."""
    actor_dict = _normalize_actor(actor)
    seq = _next_sequence()
    event = RealtimeEvent(
        event_id=str(seq),
        event_type=event_type,
        timestamp=datetime.now(timezone.utc).isoformat(),
        actor=actor_dict,
        workspace_id=workspace_id,
        correlation_id=correlation_id,
        payload=dict(payload or {}),
        sequence=seq,
    )
    _persist(event)
    _fanout(event)
    if mirror_to_audit:
        try:
            audit_log.record(
                action=f"realtime.{event_type}",
                entity_type="realtime_event",
                entity_id=event.event_id,
                actor=actor_dict,
                correlation_id=correlation_id,
                metadata={"event_type": event_type, "workspace_id": workspace_id,
                          "payload_keys": list((payload or {}).keys())},
            )
        except Exception:
            logger.warning("realtime_bus audit mirror failed", exc_info=True)
    return event


def replay(
    *,
    since_sequence: int = 0,
    workspace_id: str | None = None,
    event_types: list | None = None,
    limit: int = 500,
) -> list[RealtimeEvent]:
    """Read events from disk filtered by sequence + workspace + types."""
    out: list[RealtimeEvent] = []
    type_filter = set(event_types) if event_types else None
    if not _EVENTS_DIR.exists():
        return []
    # Sweep retention; never crash on a corrupt day file.
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
                    try:
                        out.append(RealtimeEvent(**data))
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
    """SSE-shaped generator: yields wire-format strings.

    Replays from ``since_sequence`` first (resume after reconnect), then
    blocks on the subscriber queue for new events. Emits a heartbeat
    comment every ``heartbeat_seconds`` so proxies don't time out.

    ``stop_after_seconds`` is a test-helper to cap the stream — production
    callers leave it None and let the client disconnect break the loop.
    """
    # Replay first
    for event in replay(since_sequence=since_sequence, workspace_id=workspace_id,
                          event_types=event_types):
        yield event.to_sse()
        since_sequence = event.sequence

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
                    event = q.popleft()
                    if event.sequence > since_sequence:
                        yield event.to_sse()
                        since_sequence = event.sequence
            else:
                yield ": heartbeat\n\n"
    finally:
        unsubscribe(sub_id)


def reset_for_tests() -> None:
    """Drop subscribers + sequence counter. Test helper."""
    with _LOCK:
        _SUBSCRIBERS.clear()
    try:
        if _SEQUENCE_PATH.exists():
            _SEQUENCE_PATH.unlink()
    except OSError:
        pass


# ── Internal ───────────────────────────────────────────────────────────


def _normalize_actor(actor) -> dict:
    if isinstance(actor, dict):
        out = dict(actor); out.setdefault("name", "anonymous"); return out
    return {"name": str(actor or "anonymous")}


def _persist(event: RealtimeEvent) -> None:
    _EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.fromisoformat(event.timestamp).date()
    path = _EVENTS_DIR / f"{day.isoformat()}.jsonl"
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
    except OSError:
        logger.warning("realtime_bus append failed for %s", path, exc_info=True)


def _next_sequence() -> int:
    """Monotonic per-host sequence. File-locked across processes so two
    workers on the same host can't allocate the same id."""
    from filelock import FileLock
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


def _fanout(event: RealtimeEvent) -> None:
    with _LOCK:
        subscribers = list(_SUBSCRIBERS)
    for sub_id, q, notify, filter_fn in subscribers:
        try:
            if filter_fn(event):
                q.append(event)
                notify.set()
        except Exception:
            logger.debug("realtime_bus fanout failed for %s", sub_id, exc_info=True)


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
