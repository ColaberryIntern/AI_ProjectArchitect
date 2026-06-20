"""Agent registry — declaration of governed autonomous operators.

Each agent has:
  - identity (service identity hash)
  - scope (which capabilities / workspaces it acts on)
  - autonomy_policy (recommend_only | approval_required | autonomous_low_risk_only | autonomous_full)
  - confidence_threshold (0..1) — actions below this never auto-execute
  - permitted_actions (whitelist of action kinds)
  - rollback_required (bool) — refuses actions without a rollback plan
  - paused (bool) — human override

Persisted under ``output/ops_platform/agents/{agent_id}.json``.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution.ops_platform import audit_log

logger = logging.getLogger(__name__)

_AGENTS_DIR = OUTPUT_DIR / "ops_platform" / "agents"


VALID_AUTONOMY_POLICIES = (
    "recommend_only",            # never auto-executes; surfaces recommendations
    "approval_required",         # auto-creates approval requests; humans approve
    "autonomous_low_risk_only",  # auto-executes when risk == low AND confidence >= threshold
    "autonomous_full",           # auto-executes any allowed action above threshold
)


@dataclass
class Agent:
    agent_id: str
    name: str
    description: str
    autonomy_policy: str
    confidence_threshold: float
    permitted_actions: list                    # list of action kinds
    scope: dict                                 # {"workspace_ids":[...], "capability_ids":[...]}
    rollback_required: bool
    paused: bool
    created_at: str
    created_by: dict
    revision_id: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def register_agent(
    *,
    name: str,
    description: str,
    autonomy_policy: str,
    confidence_threshold: float,
    permitted_actions: list,
    scope: dict | None = None,
    rollback_required: bool = True,
    created_by: dict | str = "anonymous",
) -> Agent:
    if autonomy_policy not in VALID_AUTONOMY_POLICIES:
        raise ValueError(f"autonomy_policy must be one of {VALID_AUTONOMY_POLICIES}")
    if not (0.0 <= confidence_threshold <= 1.0):
        raise ValueError("confidence_threshold must be between 0 and 1")
    actor = created_by if isinstance(created_by, dict) else {"name": str(created_by)}
    agent = Agent(
        agent_id=f"agent_{uuid.uuid4().hex[:12]}",
        name=name, description=description,
        autonomy_policy=autonomy_policy,
        confidence_threshold=float(confidence_threshold),
        permitted_actions=list(permitted_actions),
        scope=dict(scope or {}),
        rollback_required=bool(rollback_required),
        paused=False,
        created_at=datetime.now(timezone.utc).isoformat(),
        created_by=actor,
    )
    _persist(agent)
    audit_log.record(
        action="agent.registered", entity_type="agent",
        entity_id=agent.agent_id, actor=actor,
        new_state={"name": name, "autonomy_policy": autonomy_policy,
                   "permitted_actions": permitted_actions},
    )
    return agent


def upsert_agent(
    *,
    agent_id: str,
    name: str,
    description: str,
    autonomy_policy: str,
    confidence_threshold: float,
    permitted_actions: list,
    scope: dict | None = None,
    rollback_required: bool = True,
    created_by: dict | str = "anonymous",
) -> Agent:
    """Idempotent register-or-update keyed on a STABLE caller-supplied agent_id.

    ``register_agent`` mints a random id, so calling it repeatedly (e.g. from a
    deploy-time bootstrap) duplicates agents. ``upsert_agent`` lets declarative
    callers — like ``runtime_agents.upsert_runtime_agents`` — converge the
    registry to the committed declaration. Preserves ``paused`` and creation
    provenance across updates so a human pause is never silently reset.
    """
    if autonomy_policy not in VALID_AUTONOMY_POLICIES:
        raise ValueError(f"autonomy_policy must be one of {VALID_AUTONOMY_POLICIES}")
    if not (0.0 <= confidence_threshold <= 1.0):
        raise ValueError("confidence_threshold must be between 0 and 1")
    actor = created_by if isinstance(created_by, dict) else {"name": str(created_by)}
    existing = get(agent_id)
    agent = Agent(
        agent_id=agent_id,
        name=name, description=description,
        autonomy_policy=autonomy_policy,
        confidence_threshold=float(confidence_threshold),
        permitted_actions=list(permitted_actions),
        scope=dict(scope or {}),
        rollback_required=bool(rollback_required),
        paused=existing.paused if existing else False,
        created_at=existing.created_at if existing else datetime.now(timezone.utc).isoformat(),
        created_by=existing.created_by if existing else actor,
    )
    _persist(agent)
    audit_log.record(
        action="agent.updated" if existing else "agent.registered",
        entity_type="agent", entity_id=agent_id, actor=actor,
        previous_state=({"autonomy_policy": existing.autonomy_policy} if existing else None),
        new_state={"name": name, "autonomy_policy": autonomy_policy,
                   "permitted_actions": permitted_actions},
    )
    return agent


def pause(agent_id: str, *, actor: dict | str = "anonymous",
            reason: str = "operator pause") -> Agent | None:
    a = get(agent_id)
    if a is None or a.paused:
        return a
    a.paused = True
    _persist(a)
    audit_log.record(
        action="agent.paused", entity_type="agent", entity_id=agent_id,
        actor=actor if isinstance(actor, dict) else {"name": str(actor)},
        previous_state={"paused": False}, new_state={"paused": True},
        metadata={"reason": reason},
    )
    return a


def resume(agent_id: str, *, actor: dict | str = "anonymous",
             reason: str = "operator resume") -> Agent | None:
    a = get(agent_id)
    if a is None or not a.paused:
        return a
    a.paused = False
    _persist(a)
    audit_log.record(
        action="agent.resumed", entity_type="agent", entity_id=agent_id,
        actor=actor if isinstance(actor, dict) else {"name": str(actor)},
        previous_state={"paused": True}, new_state={"paused": False},
        metadata={"reason": reason},
    )
    return a


def get(agent_id: str) -> Agent | None:
    path = _AGENTS_DIR / f"{agent_id}.json"
    if not path.exists():
        return None
    try:
        return Agent(**json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def list_agents(*, only_active: bool = False) -> list[Agent]:
    if not _AGENTS_DIR.exists():
        return []
    out: list[Agent] = []
    for p in _AGENTS_DIR.glob("agent_*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            a = Agent(**data)
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        if only_active and a.paused:
            continue
        out.append(a)
    return out


def _persist(agent: Agent) -> None:
    from execution.ops_platform import optimistic_concurrency
    agent.revision_id = optimistic_concurrency.new_revision()
    _AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    (_AGENTS_DIR / f"{agent.agent_id}.json").write_text(
        json.dumps(agent.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
