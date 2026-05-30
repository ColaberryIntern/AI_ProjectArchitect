"""Experiment engine — measurable A/B / multi-arm routing on top of Phase 5
runtime_router.

Determinism guarantee
---------------------
Assignment uses ``hash(session_id + experiment_id) % 100``, identical
shape to Phase 5 routing. The same session always lands on the same arm
for the same experiment, even after process restart. Experiment
reproducibility survives replay because the bucket and the assignment
table are persisted.

Experiment lifecycle
--------------------
   draft → running → paused
                  → completed
                  → aborted
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution.ops_platform import audit_log

logger = logging.getLogger(__name__)

_EXPERIMENTS_DIR = OUTPUT_DIR / "ops_platform" / "experiments"


@dataclass
class Arm:
    arm_id: str
    label: str
    capability_version_id: str | None = None
    pipeline_id: str | None = None
    weight: int = 50              # percent. Sum across arms should be 100 (validated).
    is_holdout: bool = False      # holdout arms get the original/control behavior
    is_shadow: bool = False       # shadow arms execute silently; results not user-facing

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Experiment:
    experiment_id: str
    name: str
    capability_id: str
    state: str                    # draft | running | paused | completed | aborted
    arms: list                    # list[Arm dicts]
    sticky_session: bool = True
    created_at: str = ""
    created_by: dict = field(default_factory=dict)
    started_at: str | None = None
    ended_at: str | None = None
    description: str = ""
    workspace_id: str | None = None
    revision_id: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


# ── Public API ─────────────────────────────────────────────────────────


def create_experiment(
    *,
    name: str,
    capability_id: str,
    arms: list[dict],
    sticky_session: bool = True,
    description: str = "",
    workspace_id: str | None = None,
    created_by: dict | str = "anonymous",
) -> Experiment:
    """Create a draft experiment. Validate weights sum to 100 across non-shadow arms."""
    non_shadow_weight = sum(a.get("weight", 0) for a in arms if not a.get("is_shadow"))
    if non_shadow_weight != 100:
        raise ValueError(f"non-shadow arm weights must sum to 100, got {non_shadow_weight}")
    _EXPERIMENTS_DIR.mkdir(parents=True, exist_ok=True)
    actor = created_by if isinstance(created_by, dict) else {"name": str(created_by)}
    exp = Experiment(
        experiment_id=f"exp_{uuid.uuid4().hex[:12]}",
        name=name, capability_id=capability_id,
        state="draft", arms=list(arms),
        sticky_session=sticky_session, created_at=datetime.now(timezone.utc).isoformat(),
        created_by=actor, description=description, workspace_id=workspace_id,
    )
    _persist(exp)
    audit_log.record(
        action="experiment.created", entity_type="experiment",
        entity_id=exp.experiment_id, actor=actor,
        new_state={"name": name, "capability_id": capability_id,
                   "arm_count": len(arms)},
    )
    return exp


def transition(experiment_id: str, *, to_state: str,
                 actor: dict | str = "anonymous") -> Experiment | None:
    valid = ("draft", "running", "paused", "completed", "aborted")
    if to_state not in valid:
        raise ValueError(f"to_state must be one of {valid}")
    exp = get(experiment_id)
    if exp is None:
        return None
    previous = exp.state
    exp.state = to_state
    now_iso = datetime.now(timezone.utc).isoformat()
    if to_state == "running" and not exp.started_at:
        exp.started_at = now_iso
    if to_state in ("completed", "aborted"):
        exp.ended_at = now_iso
    _persist(exp)
    audit_log.record(
        action=f"experiment.{to_state}", entity_type="experiment",
        entity_id=experiment_id,
        actor=actor if isinstance(actor, dict) else {"name": str(actor)},
        previous_state={"state": previous}, new_state={"state": to_state},
    )
    return exp


def assign(experiment_id: str, *, session_id: str) -> dict:
    """Pick an arm for a session. Deterministic, sticky, replayable."""
    exp = get(experiment_id)
    if exp is None or exp.state != "running":
        return {"experiment_id": experiment_id, "assignment": None,
                "reason": "experiment not running"}
    bucket = _bucket(session_id, experiment_id)
    cumulative = 0
    chosen = None
    for arm in exp.arms:
        if arm.get("is_shadow"):
            continue
        cumulative += int(arm.get("weight", 0))
        if bucket < cumulative:
            chosen = arm
            break
    if chosen is None:
        chosen = next((a for a in exp.arms if not a.get("is_shadow")), None)
    shadows = [a for a in exp.arms if a.get("is_shadow")]
    audit_log.record(
        action="experiment.assigned", entity_type="experiment",
        entity_id=experiment_id,
        actor={"name": "runtime_router", "system": True},
        correlation_id=f"experiment:{experiment_id}",
        metadata={"session_id": session_id, "bucket": bucket,
                  "chosen_arm_id": chosen.get("arm_id") if chosen else None,
                  "shadow_arm_count": len(shadows)},
    )
    return {
        "experiment_id": experiment_id, "session_id": session_id,
        "bucket": bucket, "assignment": chosen,
        "shadow_arms": shadows,
    }


def get(experiment_id: str) -> Experiment | None:
    path = _EXPERIMENTS_DIR / f"{experiment_id}.json"
    if not path.exists():
        return None
    try:
        return Experiment(**json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def list_experiments(*, state: str | None = None) -> list[Experiment]:
    if not _EXPERIMENTS_DIR.exists():
        return []
    out: list[Experiment] = []
    for p in _EXPERIMENTS_DIR.glob("exp_*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            out.append(Experiment(**data))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    if state:
        out = [e for e in out if e.state == state]
    out.sort(key=lambda e: e.created_at, reverse=True)
    return out


# ── Internal ───────────────────────────────────────────────────────────


def _bucket(session_id: str, experiment_id: str) -> int:
    seed = f"{session_id or 'anonymous'}:{experiment_id}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(seed).digest()[:4], "big") % 100


def _persist(exp: Experiment) -> None:
    from execution.ops_platform import optimistic_concurrency
    exp.revision_id = optimistic_concurrency.new_revision()
    _EXPERIMENTS_DIR.mkdir(parents=True, exist_ok=True)
    (_EXPERIMENTS_DIR / f"{exp.experiment_id}.json").write_text(
        json.dumps(exp.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8",
    )


def save_with_revision_check(exp: Experiment, *, observed_revision: str | None,
                                actor: dict | str | None = None) -> Experiment:
    from execution.ops_platform import optimistic_concurrency
    current = get(exp.experiment_id)
    optimistic_concurrency.compare(
        entity_type="experiment", entity_id=exp.experiment_id,
        observed_revision=observed_revision,
        current_revision=current.revision_id if current else None,
        actor=actor,
    )
    _persist(exp)
    return exp
