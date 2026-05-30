"""Long-running workflow recovery: checkpoints, retry DSL, crash recovery,
operator timeline reconstruction.

Layered on top of Phase 8E ``orchestration_engine`` + Phase 9D
``orchestration_runtime``.

Checkpoints
-----------
Step records can carry a free-form ``checkpoint`` dict. The engine writes
it via ``save_checkpoint(orchestration_id, step_id, payload)``. Crash
recovery reads the most recent checkpoint and resumes from there rather
than restarting the step from scratch.

Retry policy DSL
----------------
Per-step ``retry_policy``:
  {"max_retries": N, "base_backoff_seconds": S, "jitter": "uniform|none",
   "compensation_max_retries": M}

Stored on the step definition; ``compute_next_attempt(...)`` returns the
next attempt timestamp.
"""

from __future__ import annotations

import json
import logging
import random
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution.ops_platform import (
    audit_log, event_fabric, orchestration_engine, orchestration_runtime,
)

logger = logging.getLogger(__name__)

_CHECKPOINTS_DIR = OUTPUT_DIR / "ops_platform" / "orchestration_checkpoints"
_HEARTBEATS_DIR = OUTPUT_DIR / "ops_platform" / "orchestration_heartbeats"


@dataclass
class Checkpoint:
    orchestration_id: str
    step_id: str
    payload: dict
    saved_at: str

    def to_dict(self) -> dict:
        return asdict(self)


# ── Checkpoints ───────────────────────────────────────────────────────


def save_checkpoint(*, orchestration_id: str, step_id: str,
                       payload: dict) -> Checkpoint:
    ckpt = Checkpoint(
        orchestration_id=orchestration_id, step_id=step_id,
        payload=dict(payload),
        saved_at=datetime.now(timezone.utc).isoformat(),
    )
    _CHECKPOINTS_DIR.mkdir(parents=True, exist_ok=True)
    (_CHECKPOINTS_DIR / _ckpt_filename(orchestration_id, step_id)).write_text(
        json.dumps(ckpt.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    audit_log.record(
        action="orchestration.checkpoint_saved",
        entity_type="orchestration_step",
        entity_id=f"{orchestration_id}:{step_id}",
        actor={"name": "orchestration_recovery", "system": True},
        correlation_id=orchestration_id,
        metadata={"keys": sorted(list(payload.keys()))},
    )
    event_fabric.emit(
        "orchestration.checkpoint_saved",
        correlation_id=orchestration_id,
        payload={"step_id": step_id, "keys": sorted(list(payload.keys()))},
        durability_scope="single-host",
        consistency_scope="at-least-once",
    )
    return ckpt


def load_checkpoint(*, orchestration_id: str,
                       step_id: str) -> Checkpoint | None:
    path = _CHECKPOINTS_DIR / _ckpt_filename(orchestration_id, step_id)
    if not path.exists():
        return None
    try:
        return Checkpoint(**json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, TypeError):
        return None


# ── Heartbeat journal ─────────────────────────────────────────────────


def write_heartbeat(*, orchestration_id: str, worker_id: str,
                       step_id: str | None = None) -> None:
    _HEARTBEATS_DIR.mkdir(parents=True, exist_ok=True)
    path = _HEARTBEATS_DIR / f"{orchestration_id}.json"
    payload = {
        "orchestration_id": orchestration_id, "worker_id": worker_id,
        "step_id": step_id,
        "heartbeat_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def last_heartbeat(*, orchestration_id: str) -> dict | None:
    path = _HEARTBEATS_DIR / f"{orchestration_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def recover_after_crash(*, age_minutes: int = 5) -> dict:
    """Find orchestrations whose heartbeat lapsed and trigger recovery for
    each. Recovery is non-destructive: it just releases the step claim so
    the next worker can take it."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    recovered = []
    if not _HEARTBEATS_DIR.exists():
        return {"recovered": []}
    for p in _HEARTBEATS_DIR.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        try:
            hb = datetime.fromisoformat(data.get("heartbeat_at", ""))
        except ValueError:
            continue
        if hb >= cutoff:
            continue
        # Heartbeat too old → release claims for this orchestration
        for claim in orchestration_runtime.list_active_claims():
            if claim.orchestration_id != data["orchestration_id"]:
                continue
            orchestration_runtime.release_claim(claim,
                                                   worker_id=claim.owner_token)
        recovered.append({"orchestration_id": data["orchestration_id"],
                            "stale_heartbeat_at": data.get("heartbeat_at"),
                            "worker_id": data.get("worker_id")})
        audit_log.record(
            action="orchestration.crash_recovered",
            entity_type="orchestration",
            entity_id=data["orchestration_id"],
            actor={"name": "orchestration_recovery", "system": True},
            metadata={"stale_heartbeat_at": data.get("heartbeat_at"),
                      "worker_id": data.get("worker_id")},
        )
    return {"recovered": recovered}


# ── Retry policy DSL ──────────────────────────────────────────────────


DEFAULT_RETRY_POLICY = {
    "max_retries": 3,
    "base_backoff_seconds": 2.0,
    "max_backoff_seconds": 60.0,
    "jitter": "uniform",
    "compensation_max_retries": 2,
}


def compute_next_attempt(
    *,
    attempts: int,
    policy: dict | None = None,
) -> dict:
    """Return {next_attempt_at, should_dead_letter, applied_policy}."""
    policy = {**DEFAULT_RETRY_POLICY, **(policy or {})}
    should_dead = attempts >= int(policy["max_retries"])
    base = float(policy["base_backoff_seconds"])
    cap = float(policy["max_backoff_seconds"])
    backoff = min(cap, base * (2 ** max(0, attempts - 1)))
    if policy.get("jitter") == "uniform":
        backoff *= (1 + random.uniform(-0.2, 0.2))
    return {
        "next_attempt_at": (datetime.now(timezone.utc)
                              + timedelta(seconds=backoff)).isoformat(),
        "backoff_seconds": round(backoff, 1),
        "should_dead_letter": should_dead,
        "applied_policy": policy,
    }


# ── Operator timeline reconstruction ──────────────────────────────────


def operator_timeline(orchestration_id: str) -> dict:
    """Synthesize a timeline of every transition for an orchestration:
    audit_log + heartbeats + checkpoints + event_fabric, ordered."""
    rows: list[dict] = []
    for r in audit_log.list_entries(entity_id=orchestration_id, days=90, limit=500):
        rows.append({"at": r.get("timestamp"),
                       "source": "audit",
                       "kind": r.get("action"),
                       "metadata": r.get("metadata")})
    rows.extend({
        "at": ckpt_data.get("saved_at"),
        "source": "checkpoint",
        "kind": "checkpoint_saved",
        "metadata": {"step_id": ckpt_data.get("step_id"),
                       "keys": sorted(list((ckpt_data.get("payload") or {}).keys()))},
    } for ckpt_data in _checkpoints_for(orchestration_id))
    hb = last_heartbeat(orchestration_id=orchestration_id)
    if hb:
        rows.append({"at": hb.get("heartbeat_at"),
                       "source": "heartbeat",
                       "kind": "last_known_heartbeat",
                       "metadata": hb})
    for ev in event_fabric.replay(correlation_id=orchestration_id, limit=500):
        rows.append({"at": ev.timestamp, "source": "fabric",
                       "kind": ev.event_type,
                       "metadata": ev.payload})
    rows.sort(key=lambda r: r.get("at", ""))
    return {"orchestration_id": orchestration_id, "entries": rows}


# ── Internal ───────────────────────────────────────────────────────────


def _ckpt_filename(orchestration_id: str, step_id: str) -> str:
    safe = "".join(c if c.isalnum() or c in "_-" else "_"
                     for c in f"{orchestration_id}_{step_id}")
    return f"ckpt_{safe}.json"


def _checkpoints_for(orchestration_id: str) -> list[dict]:
    if not _CHECKPOINTS_DIR.exists():
        return []
    out: list[dict] = []
    for p in _CHECKPOINTS_DIR.glob("ckpt_*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("orchestration_id") == orchestration_id:
            out.append(data)
    return out
