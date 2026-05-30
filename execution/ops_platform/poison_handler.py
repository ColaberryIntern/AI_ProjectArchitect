"""Poison event handling — detect malformed / poison events and quarantine
them so projection rebuilds and consumer loops don't get stuck on them.

A "poison" event is one that:
  - has a schema that breaks a consumer / projection reducer
  - has been retried beyond ``max_retries`` without acknowledgement
  - was flagged explicitly by an operator

Scope honesty
-------------
- Quarantine is a parallel JSONL file per day:
  ``output/ops_platform/poison_quarantine/{date}.jsonl``
- Projection rebuilds skip quarantined ``event_id``s (the engine reads the
  quarantine set on each rebuild).
- Replay tooling is operator-driven; nothing escapes quarantine silently.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution.ops_platform import audit_log, event_fabric

logger = logging.getLogger(__name__)

_QUARANTINE_DIR = OUTPUT_DIR / "ops_platform" / "poison_quarantine"
_RETRIES_DIR = OUTPUT_DIR / "ops_platform" / "poison_retries"


@dataclass
class PoisonRecord:
    event_id: str
    quarantine_id: str
    reason: str
    detected_by: str
    detected_at: str
    retry_attempts: int
    original_event_type: str | None
    operator_release_required: bool
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ── Public API ─────────────────────────────────────────────────────────


def quarantine_event(
    event_id: str,
    *,
    reason: str,
    detected_by: str,
    original_event_type: str | None = None,
    retry_attempts: int = 0,
    notes: str = "",
) -> PoisonRecord:
    if not event_id:
        raise ValueError("event_id is required")
    record = PoisonRecord(
        event_id=event_id,
        quarantine_id=f"poison_{uuid.uuid4().hex[:12]}",
        reason=reason[:280],
        detected_by=detected_by,
        detected_at=datetime.now(timezone.utc).isoformat(),
        retry_attempts=retry_attempts,
        original_event_type=original_event_type,
        operator_release_required=True,
        notes=notes,
    )
    _append(record)
    audit_log.record(
        action="poison.quarantined", entity_type="poison_record",
        entity_id=record.quarantine_id,
        actor={"name": detected_by, "system": True},
        metadata={"event_id": event_id, "reason": reason,
                  "retry_attempts": retry_attempts,
                  "original_event_type": original_event_type},
    )
    return record


def track_retry(event_id: str, *, error: str, max_retries: int = 5) -> bool:
    """Bump retry counter for an event. Returns True if the event has hit
    ``max_retries`` and should be quarantined; the caller does the
    quarantine_event() call to keep the side-effect explicit."""
    _RETRIES_DIR.mkdir(parents=True, exist_ok=True)
    path = _RETRIES_DIR / f"{event_id}.json"
    data = {"attempts": 0, "last_error": ""}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {"attempts": 0, "last_error": ""}
    data["attempts"] = int(data.get("attempts", 0)) + 1
    data["last_error"] = error[:280]
    data["last_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data["attempts"] >= max(1, int(max_retries))


def release(quarantine_id: str, *, actor: dict | str,
              reason: str = "operator release") -> PoisonRecord | None:
    record = find(quarantine_id)
    if record is None:
        return None
    record.operator_release_required = False
    record.notes = f"{record.notes}\nReleased: {reason}".strip()
    # Rewrite the day's file with the updated row
    _rewrite_with_update(record)
    audit_log.record(
        action="poison.released", entity_type="poison_record",
        entity_id=quarantine_id,
        actor=actor if isinstance(actor, dict) else {"name": str(actor)},
        metadata={"reason": reason},
    )
    return record


def find(quarantine_id: str) -> PoisonRecord | None:
    for r in list_quarantine(limit=10000):
        if r.quarantine_id == quarantine_id:
            return r
    return None


def list_quarantine(*, days: int = 30, limit: int = 200) -> list[PoisonRecord]:
    if not _QUARANTINE_DIR.exists():
        return []
    out: list[PoisonRecord] = []
    today = datetime.now(timezone.utc).date()
    for d in range(days):
        day = today - timedelta(days=d)
        path = _QUARANTINE_DIR / f"{day.isoformat()}.jsonl"
        if not path.exists():
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        out.append(PoisonRecord(**data))
                    except (json.JSONDecodeError, TypeError):
                        continue
        except OSError:
            continue
        if len(out) >= limit:
            return out[:limit]
    return out[:limit]


def is_quarantined(event_id: str) -> bool:
    """Cheap dedup helper for projection_engine.rebuild — skips poison events."""
    for r in list_quarantine(limit=10000):
        if r.event_id == event_id and r.operator_release_required:
            return True
    return False


def quarantined_event_ids() -> set[str]:
    return {r.event_id for r in list_quarantine(limit=10000)
              if r.operator_release_required}


def replay_released() -> dict:
    """For every released quarantine record, re-emit a marker event into the
    fabric so projections can re-consider the event. The original event
    stays in the log; this is a *signal* to consumers, not a re-publication."""
    released_event_ids = []
    for r in list_quarantine(limit=10000):
        if not r.operator_release_required:
            event_fabric.emit(
                "poison.released_marker",
                payload={"original_event_id": r.event_id,
                         "quarantine_id": r.quarantine_id},
                consistency_scope="best-effort",
            )
            released_event_ids.append(r.event_id)
    audit_log.record(
        action="poison.replay_released_batch", entity_type="poison_batch",
        entity_id="batch",
        actor={"name": "poison_handler", "system": True},
        metadata={"released_count": len(released_event_ids)},
    )
    return {"released_count": len(released_event_ids)}


# ── Internal ───────────────────────────────────────────────────────────


def _append(record: PoisonRecord) -> None:
    _QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.fromisoformat(record.detected_at).date().isoformat()
    path = _QUARANTINE_DIR / f"{day}.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")


def _rewrite_with_update(record: PoisonRecord) -> None:
    """Rewrite the per-day file replacing the record's row."""
    day = datetime.fromisoformat(record.detected_at).date().isoformat()
    path = _QUARANTINE_DIR / f"{day}.jsonl"
    if not path.exists():
        return
    new_lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            new_lines.append(line)
            continue
        if row.get("quarantine_id") == record.quarantine_id:
            new_lines.append(json.dumps(record.to_dict(), ensure_ascii=False))
        else:
            new_lines.append(line)
    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
