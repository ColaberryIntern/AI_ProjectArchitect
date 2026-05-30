"""Agent runtime — executes governed autonomous actions.

Every action ``execute(...)`` accepts:
  - agent_id          : whose authority is being used
  - action_kind       : verb (must be in agent.permitted_actions)
  - target            : {"entity_type":..., "entity_id":...}
  - inputs            : opaque payload describing the operation
  - reasoning_chain   : MUST be provided — every autonomous action explains itself
  - evidence_refs     : MUST be provided — citations to the data that drove the decision
  - confidence        : 0..1 — gated by agent.confidence_threshold
  - rollback_plan     : MUST be provided when agent.rollback_required

Outcomes:
  - SUGGESTED   : returned to caller, no platform mutation
  - APPROVAL_REQUIRED : an approval_request was created; awaits human
  - APPLIED     : a concrete mutation happened (recorded under the agent's identity)
  - DENIED      : policy or threshold blocked execution
  - PAUSED      : the agent is paused

Audit chain
-----------
Every execution writes:
  - audit row ``agent.execution`` with the AgentExecution record
  - if APPLIED, also the underlying mutation's own audit row (those already exist)
  - if APPROVAL_REQUIRED, the linked approval_request_id

Realtime
--------
Every execution emits ``agent.execution`` on the realtime bus so dashboards
can see autonomous activity live.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution.ops_platform import (
    agent_registry, approvals, audit_log, controls, realtime_bus, runtime_queue,
    worker_coordination,
)

logger = logging.getLogger(__name__)

_EXECUTIONS_DIR = OUTPUT_DIR / "ops_platform" / "agent_executions"


@dataclass
class AgentExecution:
    execution_id: str
    agent_id: str
    action_kind: str
    target: dict
    inputs: dict
    reasoning_chain: list
    evidence_refs: list
    confidence: float
    rollback_plan: str
    risk: str                         # "low" | "medium" | "high"
    outcome: str                       # SUGGESTED | APPROVAL_REQUIRED | APPLIED | DENIED | PAUSED | ERROR
    detail: str = ""
    approval_request_id: str | None = None
    applied_at: str | None = None
    created_at: str = ""
    correlation_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class AutonomyViolation(Exception):
    pass


def execute(
    *,
    agent_id: str,
    action_kind: str,
    target: dict,
    inputs: dict,
    reasoning_chain: list,
    evidence_refs: list,
    confidence: float,
    rollback_plan: str = "",
    risk: str = "medium",
) -> AgentExecution:
    """Run one agent action through the policy → approval → apply pipeline."""
    if not reasoning_chain:
        raise AutonomyViolation("reasoning_chain is required for every agent action")
    if not evidence_refs:
        raise AutonomyViolation("evidence_refs is required for every agent action")

    agent = agent_registry.get(agent_id)
    if agent is None:
        raise AutonomyViolation(f"agent '{agent_id}' is not registered")

    correlation_id = str(uuid.uuid4())
    ex = AgentExecution(
        execution_id=f"exec_{uuid.uuid4().hex[:12]}",
        agent_id=agent_id, action_kind=action_kind,
        target=dict(target), inputs=dict(inputs),
        reasoning_chain=list(reasoning_chain),
        evidence_refs=list(evidence_refs),
        confidence=float(confidence),
        rollback_plan=rollback_plan,
        risk=risk,
        outcome="SUGGESTED",
        created_at=datetime.now(timezone.utc).isoformat(),
        correlation_id=correlation_id,
    )

    # Gate 1: paused
    if agent.paused:
        ex.outcome = "PAUSED"
        ex.detail = "agent is paused"
        _record(ex, agent)
        return ex

    # Gate 2: action allowed
    if action_kind not in agent.permitted_actions:
        ex.outcome = "DENIED"
        ex.detail = f"action '{action_kind}' not in agent.permitted_actions"
        _record(ex, agent)
        return ex

    # Gate 3: confidence threshold
    if confidence < agent.confidence_threshold:
        ex.outcome = "DENIED"
        ex.detail = (f"confidence {confidence:.2f} below agent threshold "
                       f"{agent.confidence_threshold:.2f}")
        _record(ex, agent)
        return ex

    # Gate 4: rollback required
    if agent.rollback_required and not rollback_plan.strip():
        ex.outcome = "DENIED"
        ex.detail = "rollback_plan is required by this agent"
        _record(ex, agent)
        return ex

    # Gate 5: autonomy_policy
    policy = agent.autonomy_policy
    if policy == "recommend_only":
        ex.outcome = "SUGGESTED"
        _record(ex, agent)
        return ex

    if policy == "approval_required":
        appr = approvals.request_approval(
            action=f"agent.{action_kind}",
            entity_type=target.get("entity_type", "unknown"),
            entity_id=target.get("entity_id", "unknown"),
            requested_by={"name": agent.name, "system": True},
            single_approver_roles=["admin"],
            reason=f"agent {agent.name}: {' → '.join(reasoning_chain[:3])}",
            metadata={"agent_execution_id": ex.execution_id,
                      "confidence": confidence, "risk": risk,
                      "evidence_refs": evidence_refs[:5]},
            correlation_id=correlation_id,
        )
        ex.outcome = "APPROVAL_REQUIRED"
        ex.approval_request_id = appr.request_id
        _record(ex, agent)
        return ex

    if policy == "autonomous_low_risk_only" and risk != "low":
        ex.outcome = "DENIED"
        ex.detail = f"policy autonomous_low_risk_only blocked risk={risk}"
        _record(ex, agent)
        return ex

    # Gate 6: maintenance mode / capability frozen
    cap_id = target.get("entity_id") if target.get("entity_type") == "capability" else None
    if cap_id and controls.is_blocked(cap_id):
        ex.outcome = "DENIED"
        ex.detail = f"target capability is blocked: {controls.is_blocked(cap_id)}"
        _record(ex, agent)
        return ex

    # ── APPLY ──
    ex.outcome = "APPLIED"
    ex.applied_at = datetime.now(timezone.utc).isoformat()
    _apply_action(ex, agent)
    _record(ex, agent)
    return ex


def revoke(execution_id: str, *, actor: dict | str = "anonymous",
            reason: str = "operator revoke") -> AgentExecution | None:
    """Revoke a previously APPLIED action. The rollback_plan describes how;
    actual rollback execution is operator-driven (or via change_requests)."""
    ex = get_execution(execution_id)
    if ex is None or ex.outcome not in ("APPLIED",):
        return ex
    ex.outcome = "ERROR"
    ex.detail = f"revoked: {reason}"
    _persist(ex)
    audit_log.record(
        action="agent.execution_revoked", entity_type="agent_execution",
        entity_id=execution_id,
        actor=actor if isinstance(actor, dict) else {"name": str(actor)},
        correlation_id=ex.correlation_id,
        metadata={"reason": reason, "rollback_plan": ex.rollback_plan},
    )
    realtime_bus.emit("agent.revoked",
                        actor=actor if isinstance(actor, dict) else {"name": str(actor)},
                        correlation_id=ex.correlation_id,
                        payload={"execution_id": execution_id, "reason": reason},
                        mirror_to_audit=False)
    return ex


def get_execution(execution_id: str) -> AgentExecution | None:
    if not _EXECUTIONS_DIR.exists():
        return None
    path = _EXECUTIONS_DIR / f"{execution_id}.json"
    if not path.exists():
        return None
    try:
        return AgentExecution(**json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def list_executions(*, agent_id: str | None = None,
                      outcome: str | None = None,
                      limit: int = 200) -> list[AgentExecution]:
    if not _EXECUTIONS_DIR.exists():
        return []
    out: list[AgentExecution] = []
    for p in _EXECUTIONS_DIR.glob("exec_*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            ex = AgentExecution(**data)
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        if agent_id and ex.agent_id != agent_id:
            continue
        if outcome and ex.outcome != outcome:
            continue
        out.append(ex)
    out.sort(key=lambda e: e.created_at, reverse=True)
    return out[:limit]


# ── Internal ───────────────────────────────────────────────────────────


def _record(ex: AgentExecution, agent: agent_registry.Agent) -> None:
    _persist(ex)
    audit_log.record(
        action="agent.execution", entity_type="agent_execution",
        entity_id=ex.execution_id,
        actor={"name": agent.name, "system": True},
        correlation_id=ex.correlation_id,
        new_state={"outcome": ex.outcome, "action_kind": ex.action_kind,
                   "confidence": ex.confidence, "risk": ex.risk,
                   "target": ex.target,
                   "rollback_plan_present": bool(ex.rollback_plan),
                   "approval_request_id": ex.approval_request_id},
        metadata={"reasoning_chain": ex.reasoning_chain,
                  "evidence_refs": ex.evidence_refs, "detail": ex.detail},
    )
    realtime_bus.emit("agent.execution",
                        actor={"name": agent.name, "system": True},
                        correlation_id=ex.correlation_id,
                        payload={"execution_id": ex.execution_id,
                                   "action_kind": ex.action_kind,
                                   "outcome": ex.outcome,
                                   "confidence": ex.confidence},
                        mirror_to_audit=False)


def _persist(ex: AgentExecution) -> None:
    _EXECUTIONS_DIR.mkdir(parents=True, exist_ok=True)
    (_EXECUTIONS_DIR / f"{ex.execution_id}.json").write_text(
        json.dumps(ex.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ── Concrete action handlers (Phase 8B: autonomous workflows) ──────────


def _apply_action(ex: AgentExecution, agent: agent_registry.Agent) -> None:
    """Run the concrete mutation. Each action_kind has its own handler;
    new actions are added by extending the dispatcher below."""
    kind = ex.action_kind
    try:
        if kind == "reclaim_stale_queue":
            reclaimed = runtime_queue.reclaim_stale()
            ex.detail = f"reclaimed {len(reclaimed)} stale job(s)"
            return
        if kind == "evict_stale_workers":
            evicted = worker_coordination.evict_stale()
            ex.detail = f"evicted {len(evicted)} stale worker(s)"
            return
        if kind == "quarantine_capability":
            cap_id = ex.target.get("entity_id", "")
            ctrl = controls.quarantine(cap_id,
                                          actor={"name": agent.name, "system": True},
                                          reason=f"agent: {ex.detail or 'autonomous'}")
            ex.detail = f"quarantined capability via control {ctrl.control_id}"
            return
        if kind == "release_quarantine":
            cap_id = ex.target.get("entity_id", "")
            controls.release_quarantine(cap_id,
                                          actor={"name": agent.name, "system": True},
                                          reason="agent release")
            ex.detail = "released quarantine"
            return
        if kind == "disable_schedule":
            from execution.ops_platform import scheduler
            scheduler.disable(ex.target.get("entity_id", ""),
                                actor={"name": agent.name, "system": True})
            ex.detail = "disabled schedule"
            return
        ex.outcome = "ERROR"
        ex.detail = f"no handler registered for action_kind '{kind}'"
    except Exception as e:
        ex.outcome = "ERROR"
        ex.detail = f"action handler raised: {str(e)[:200]}"
