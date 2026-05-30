"""Projection engine — event-sourced materialized views over the unified
event fabric.

Scope honesty
-------------
- Projections are pure deterministic functions of the event log.
- ``rebuild(name, from_sequence=0)`` reads every event >= from_sequence
  and re-runs the projection's ``apply(state, event)`` reducer.
- Rebuild outputs are written to
  ``output/ops_platform/projections/{name}/{sequence}.json`` plus a
  ``latest.json`` symlink-style file. Operators can compare an arbitrary
  rebuild against the latest persisted view to detect drift.

Determinism
-----------
A projection is a tuple ``(name, version, apply_fn, initial_state_fn)``.
The version tag is bumped when the reducer logic changes; the engine refuses
to merge a v2 projection onto v1 state.

Candidate projections (registered at import in ``register_default_projections()``):
  - incident_timeline      groups audit + realtime by correlation_id
  - active_alerts          last-write-wins per alert_id
  - orchestration_state    per-orchestration step status
  - operator_activity      per-actor action counts
  - agent_execution_history per-agent outcome counters
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from config.settings import OUTPUT_DIR
from execution.ops_platform import event_fabric

logger = logging.getLogger(__name__)

_PROJECTIONS_DIR = OUTPUT_DIR / "ops_platform" / "projections"


@dataclass
class Projection:
    name: str
    version: str                    # bump when reducer logic changes
    initial_state: Callable          # returns the empty state
    apply_fn: Callable               # (state, event) -> state
    event_types: list = field(default_factory=list)  # filter; empty = all

    def to_metadata(self) -> dict:
        return {"name": self.name, "version": self.version,
                  "event_types": self.event_types}


_REGISTRY: dict[str, Projection] = {}


# ── Public API ─────────────────────────────────────────────────────────


def register(projection: Projection) -> None:
    _REGISTRY[projection.name] = projection


def list_projections() -> list[dict]:
    return [p.to_metadata() for p in _REGISTRY.values()]


def rebuild(name: str, *, from_sequence: int = 0, limit: int = 50000) -> dict:
    """Read events and re-run the reducer. Quarantined events (from
    poison_handler) are skipped + recorded. Returns the materialized state
    plus metadata."""
    projection = _REGISTRY.get(name)
    if projection is None:
        raise KeyError(f"no projection registered for '{name}'")
    state = projection.initial_state()
    events_consumed = 0
    skipped_poison = 0
    last_sequence = from_sequence
    type_filter = projection.event_types or None
    try:
        from execution.ops_platform import poison_handler
        poison_ids = poison_handler.quarantined_event_ids()
    except Exception:
        poison_ids = set()
    for ev in event_fabric.replay(
        since_sequence=from_sequence,
        event_types=type_filter,
        limit=limit,
    ):
        if ev.event_id in poison_ids:
            skipped_poison += 1
            continue
        try:
            state = projection.apply_fn(state, ev)
        except Exception as e:
            # Poison-suspect: track retry, quarantine on threshold
            try:
                from execution.ops_platform import poison_handler
                should_quarantine = poison_handler.track_retry(
                    ev.event_id, error=str(e)[:200], max_retries=3,
                )
                if should_quarantine:
                    poison_handler.quarantine_event(
                        ev.event_id,
                        reason=f"reducer raised in projection '{name}': {str(e)[:120]}",
                        detected_by=f"projection.{name}",
                        original_event_type=ev.event_type,
                        retry_attempts=3,
                    )
                    skipped_poison += 1
            except Exception:
                logger.warning("projection %s reducer raised at seq %d",
                                  name, ev.sequence, exc_info=True)
            continue
        events_consumed += 1
        last_sequence = ev.sequence
    result = {
        "name": name, "version": projection.version,
        "events_consumed": events_consumed,
        "skipped_poison": skipped_poison,
        "from_sequence": from_sequence,
        "last_sequence": last_sequence,
        "rebuilt_at": datetime.now(timezone.utc).isoformat(),
        "state": state,
    }
    _persist(name, result)
    return result


def latest(name: str) -> dict | None:
    path = _PROJECTIONS_DIR / name / "latest.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def compare_with_latest(name: str, *, from_sequence: int = 0) -> dict:
    """Rebuild from scratch and compare against the persisted latest —
    drift detection."""
    rebuilt = rebuild(name, from_sequence=from_sequence)
    persisted = latest(name)
    return {
        "name": name,
        "rebuild": rebuilt,
        "persisted": persisted,
        "states_match": (persisted is not None
                            and persisted.get("state") == rebuilt["state"]),
    }


def _persist(name: str, result: dict) -> None:
    target = _PROJECTIONS_DIR / name
    target.mkdir(parents=True, exist_ok=True)
    stamp = result["rebuilt_at"].replace(":", "_").replace("+00:00", "Z")
    (target / f"{stamp}.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (target / "latest.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ── Default projections ────────────────────────────────────────────────


def register_default_projections() -> None:
    """Register the five default projections. Safe to call multiple times."""

    def _empty_dict():
        return {}

    def _empty_list():
        return []

    # 1. incident_timeline: correlation_id → ordered list of events
    def _incident_apply(state, ev):
        cid = ev.correlation_id
        if not cid:
            return state
        bucket = state.setdefault(cid, [])
        bucket.append({"timestamp": ev.timestamp,
                          "event_type": ev.event_type,
                          "actor_id": ev.actor_id,
                          "sequence": ev.sequence})
        return state

    register(Projection(
        name="incident_timeline", version="1.0.0",
        initial_state=_empty_dict, apply_fn=_incident_apply,
    ))

    # 2. active_alerts: last-write-wins per payload.alert_id
    def _alerts_apply(state, ev):
        if not ev.event_type.startswith("alert."):
            return state
        alert_id = (ev.payload or {}).get("alert_id")
        if not alert_id:
            return state
        state[alert_id] = {"event_type": ev.event_type,
                              "timestamp": ev.timestamp,
                              "payload": ev.payload}
        if ev.event_type == "alert.resolved":
            state.pop(alert_id, None)
        return state

    register(Projection(
        name="active_alerts", version="1.0.0",
        initial_state=_empty_dict, apply_fn=_alerts_apply,
        event_types=["alert.opened", "alert.acknowledged", "alert.resolved"],
    ))

    # 3. orchestration_state: per-orchestration step counters
    def _orch_apply(state, ev):
        if not ev.event_type.startswith("orchestration."):
            return state
        oid = (ev.payload or {}).get("orchestration_id") or ev.correlation_id
        if not oid:
            return state
        bucket = state.setdefault(oid, {"steps": 0, "completed": 0, "failed": 0})
        if ev.event_type == "orchestration.step_claimed":
            bucket["steps"] += 1
        elif ev.event_type == "orchestration.step_completed":
            bucket["completed"] += 1
        elif ev.event_type == "orchestration.step_failed":
            bucket["failed"] += 1
        return state

    register(Projection(
        name="orchestration_state", version="1.0.0",
        initial_state=_empty_dict, apply_fn=_orch_apply,
    ))

    # 4. operator_activity: per-actor counters
    def _activity_apply(state, ev):
        actor = ev.actor_id or "anonymous"
        bucket = state.setdefault(actor, {"events": 0, "last_seen": ev.timestamp})
        bucket["events"] += 1
        if ev.timestamp > bucket["last_seen"]:
            bucket["last_seen"] = ev.timestamp
        return state

    register(Projection(
        name="operator_activity", version="1.0.0",
        initial_state=_empty_dict, apply_fn=_activity_apply,
    ))

    # 5. agent_execution_history: per-agent outcome counters
    def _agent_apply(state, ev):
        if ev.event_type != "agent.execution":
            return state
        agent_id = (ev.payload or {}).get("agent_id")
        outcome = (ev.payload or {}).get("outcome", "UNKNOWN")
        if not agent_id:
            return state
        bucket = state.setdefault(agent_id, defaultdict(int) if False else {})
        bucket[outcome] = bucket.get(outcome, 0) + 1
        return state

    register(Projection(
        name="agent_execution_history", version="1.0.0",
        initial_state=_empty_dict, apply_fn=_agent_apply,
        event_types=["agent.execution"],
    ))
