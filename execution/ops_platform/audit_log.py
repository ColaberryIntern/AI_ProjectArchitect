"""Audit log — append-only record of every mutating action in the ops platform.

Why
---
By Phase 3 the platform tracked enough state that "who changed what when"
became hard to reconstruct. The discovery queue had reviewer fields,
reputation had history, but no single source of truth across actors.
Phase 4 makes that explicit: every mutation calls ``record()`` once, and
the audit log is the operational history of record.

Storage
-------
One JSONL file per UTC date under ``output/ops_platform/audit/{YYYY-MM-DD}.jsonl``.
Append-only. Files are immutable once written; the only safe operation is
to append a new line. There is no in-place rewrite path.

Schema
------
Validated against ``config/schemas/ops/audit_entry.schema.json``. Required
fields: entry_id, timestamp, actor, action, entity_type, entity_id. Optional:
previous_state, new_state, correlation_id, metadata.

Reading
-------
``list_entries(...)`` reads the relevant day files and applies filters in
memory. With ~10K rows/day this stays sub-100ms. If/when the catalog
crosses millions of rows/year, swap the storage backend without changing
``record()`` callers.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jsonschema

from config.settings import OUTPUT_DIR, SCHEMAS_DIR

logger = logging.getLogger(__name__)

_AUDIT_DIR = OUTPUT_DIR / "ops_platform" / "audit"
_SCHEMA_PATH = SCHEMAS_DIR / "ops" / "audit_entry.schema.json"


@dataclass
class AuditEntry:
    entry_id: str
    timestamp: str
    actor: dict
    action: str
    entity_type: str
    entity_id: str
    previous_state: dict | None = None
    new_state: dict | None = None
    correlation_id: str | None = None
    metadata: dict | None = None

    def to_dict(self) -> dict:
        out = asdict(self)
        # Drop nulls so the JSONL line stays tight
        return {k: v for k, v in out.items() if v is not None}


# ── Public API ─────────────────────────────────────────────────────────


def record(
    *,
    action: str,
    entity_type: str,
    entity_id: str,
    actor: dict | str | None = None,
    previous_state: dict | None = None,
    new_state: dict | None = None,
    correlation_id: str | None = None,
    metadata: dict | None = None,
) -> AuditEntry:
    """Append one audit entry. Never raises out — invalid payloads land in the
    log file with an `_invalid_reason` marker so investigation is still
    possible, but the calling code keeps moving."""
    actor_dict = _normalize_actor(actor)
    entry = AuditEntry(
        entry_id=str(uuid.uuid4()),
        timestamp=datetime.now(timezone.utc).isoformat(),
        actor=actor_dict,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        previous_state=previous_state,
        new_state=new_state,
        correlation_id=correlation_id,
        metadata=metadata,
    )
    payload = entry.to_dict()
    errors = _validate(payload)
    if errors:
        logger.warning("audit_log payload failed schema: %s", errors[:2])
        payload["_invalid_reason"] = errors[:2]
    _append(payload)
    return entry


def list_entries(
    *,
    days: int = 7,
    entity_id: str | None = None,
    entity_type: str | None = None,
    action: str | None = None,
    actor_name: str | None = None,
    correlation_id: str | None = None,
    limit: int = 500,
) -> list[dict]:
    """Read recent entries across the last ``days`` files. Returns newest-first."""
    out: list[dict] = []
    today = datetime.now(timezone.utc).date()
    for delta in range(days):
        day = today - timedelta(days=delta)
        path = _AUDIT_DIR / f"{day.isoformat()}.jsonl"
        if not path.exists():
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if entity_id and row.get("entity_id") != entity_id:
                        continue
                    if entity_type and row.get("entity_type") != entity_type:
                        continue
                    if action and row.get("action") != action:
                        continue
                    if actor_name and (row.get("actor") or {}).get("name") != actor_name:
                        continue
                    if correlation_id and row.get("correlation_id") != correlation_id:
                        continue
                    out.append(row)
                    if len(out) >= limit * 2:
                        # Read a bit past the limit so the newest-first sort still has options
                        break
        except OSError:
            continue
    out.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    return out[:limit]


def entity_history(entity_id: str, *, days: int = 90, limit: int = 200) -> list[dict]:
    """Return every audit row touching this entity_id across the lookback."""
    return list_entries(days=days, entity_id=entity_id, limit=limit)


def replay(correlation_id: str, *, days: int = 90) -> list[dict]:
    """Return every row tied to the same correlation_id — for incident replay."""
    return list_entries(days=days, correlation_id=correlation_id, limit=1000)


def stats(*, days: int = 7) -> dict:
    """Counts by action and entity_type over the lookback. Cheap dashboard read."""
    rows = list_entries(days=days, limit=10000)
    from collections import Counter
    by_action: Counter = Counter()
    by_entity_type: Counter = Counter()
    by_actor: Counter = Counter()
    for r in rows:
        by_action[r.get("action", "unknown")] += 1
        by_entity_type[r.get("entity_type", "unknown")] += 1
        by_actor[(r.get("actor") or {}).get("name", "unknown")] += 1
    return {
        "total": len(rows),
        "by_action": dict(by_action.most_common()),
        "by_entity_type": dict(by_entity_type.most_common()),
        "by_actor": dict(by_actor.most_common(20)),
        "lookback_days": days,
    }


# ── Internal ───────────────────────────────────────────────────────────


def _normalize_actor(actor) -> dict:
    if isinstance(actor, dict):
        out = dict(actor)
        out.setdefault("name", "anonymous")
        return out
    if isinstance(actor, str):
        return {"name": actor}
    return {"name": "anonymous", "system": True}


_SCHEMA_CACHE: dict | None = None


def _load_schema() -> dict:
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is None:
        with open(_SCHEMA_PATH, "r", encoding="utf-8") as f:
            _SCHEMA_CACHE = json.load(f)
    return _SCHEMA_CACHE


def _validate(payload: dict) -> list[str]:
    try:
        schema = _load_schema()
    except OSError:
        return []
    validator = jsonschema.Draft202012Validator(schema)
    return [
        f"{'.'.join(str(p) for p in err.absolute_path) or '<root>'}: {err.message}"
        for err in sorted(validator.iter_errors(payload), key=lambda e: e.absolute_path)
    ]


def _append(payload: dict) -> None:
    _AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.fromisoformat(payload["timestamp"]).date()
    path = _AUDIT_DIR / f"{day.isoformat()}.jsonl"
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        logger.warning("audit_log append failed for %s", path, exc_info=True)
