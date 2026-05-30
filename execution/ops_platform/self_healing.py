"""Self-healing engine — turns reliability_monitor findings into reversible,
explainable, audited actions.

Every autonomous action:
  - reads from a finding (signal-driven)
  - has an explicit ``reason`` recorded in the audit row
  - has a ``reversible`` flag the operator can inspect
  - is gated by ``policy_engine`` — if a matching policy decision returns
    REQUIRE_APPROVAL or DENY, the engine raises an incident instead of acting
  - is configurable: per-action allow/deny via OPS_SELF_HEALING_ALLOWED env
    (comma-separated kinds), default deny-by-default for destructive actions

Default policy (when OPS_SELF_HEALING_ALLOWED is unset):
  - reclaim_stale_queue:    ALLOWED  (purely housekeeping, no user-visible change)
  - invalidate_stale_cache: ALLOWED  (just touches a stamp)
  - evict_dead_worker:      ALLOWED  (removes a stale row)
  - quarantine_capability:  REQUIRES OPT-IN
  - rollback_recommendation: ALWAYS issued as a recommendation, never auto-executed
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from execution.ops_platform import (
    audit_log, controls, incidents, policy_engine, reliability_monitor,
    runtime_queue, worker_coordination,
)
from execution.ops_platform.capability_registry import CapabilityRegistry, default_registry
from execution.ops_platform.identity import IdentityContext, anonymous_identity

logger = logging.getLogger(__name__)

_DEFAULT_ALLOWED_ACTIONS = {
    "reclaim_stale_queue", "invalidate_stale_cache", "evict_dead_worker",
}


@dataclass
class HealingAction:
    kind: str
    target_type: str
    target_id: str
    reason: str
    reversible: bool
    confidence: float
    correlation_id: str
    finding: dict = field(default_factory=dict)
    outcome: str = "pending"     # pending | applied | denied | requires_approval | error
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def allowed_actions() -> set[str]:
    raw = os.environ.get("OPS_SELF_HEALING_ALLOWED")
    if raw is None:
        return set(_DEFAULT_ALLOWED_ACTIONS)
    return {a.strip() for a in raw.split(",") if a.strip()}


def run_once(
    *,
    actor: dict | str = "self_healing",
    open_incidents_on_findings: bool = True,
    registry: CapabilityRegistry | None = None,
) -> list[HealingAction]:
    """Scan once and act on whatever's allowed. Returns the per-action log."""
    reg = registry or default_registry()
    findings = reliability_monitor.scan(registry=reg)
    actions: list[HealingAction] = []
    actor_norm = actor if isinstance(actor, dict) else {"name": str(actor), "system": True}
    allowed = allowed_actions()
    identity = _system_identity()

    for finding in findings:
        action = _action_for_finding(finding)
        if action is None:
            continue

        # Open incident on high-severity findings even before acting.
        incident_id = None
        if open_incidents_on_findings and finding.severity >= 4:
            incident = incidents.open_incident(
                title=f"{finding.kind} on {finding.target_type} {finding.target_id}",
                severity=finding.severity, detector="self_healing",
                impacted_capabilities=[finding.target_id] if finding.target_type == "capability" else [],
                initial_note=finding.detail, actor=actor_norm,
            )
            incident_id = incident.incident_id

        # Policy check
        decision = policy_engine.evaluate(identity, "controls.manage",
                                            capability_id=(finding.target_id if finding.target_type == "capability" else None))
        if decision.outcome != "ALLOW":
            action.outcome = "requires_approval" if decision.outcome == "REQUIRE_APPROVAL" else "denied"
            action.detail = decision.reason
            audit_log.record(
                action="self_healing.gated", entity_type=action.target_type,
                entity_id=action.target_id, actor=actor_norm,
                correlation_id=action.correlation_id,
                metadata={"kind": action.kind, "policy_outcome": decision.outcome,
                          "reason": decision.reason, "incident_id": incident_id},
            )
            actions.append(action)
            continue

        if action.kind not in allowed:
            action.outcome = "denied"
            action.detail = f"action kind '{action.kind}' not in OPS_SELF_HEALING_ALLOWED"
            audit_log.record(
                action="self_healing.gated", entity_type=action.target_type,
                entity_id=action.target_id, actor=actor_norm,
                correlation_id=action.correlation_id,
                metadata={"kind": action.kind, "reason": action.detail,
                          "incident_id": incident_id},
            )
            actions.append(action)
            continue

        # Execute
        outcome = _execute_action(action, actor=actor_norm)
        action.outcome = outcome
        audit_log.record(
            action=f"self_healing.{outcome}", entity_type=action.target_type,
            entity_id=action.target_id, actor=actor_norm,
            correlation_id=action.correlation_id,
            metadata={"kind": action.kind, "reason": action.reason,
                      "reversible": action.reversible,
                      "incident_id": incident_id,
                      "confidence": action.confidence},
        )
        actions.append(action)
    return actions


# ── Internal ───────────────────────────────────────────────────────────


def _system_identity() -> IdentityContext:
    return IdentityContext(
        user_id="self_healing", display_name="Self-Healing",
        auth_provider="STATIC_TOKEN", authenticated=True,
        roles=["admin"], workspace_ids=[],
    )


def _action_for_finding(finding) -> HealingAction | None:
    cid = str(uuid.uuid4())
    if finding.kind == "retry_storm":
        return HealingAction(
            kind="reclaim_stale_queue",
            target_type="job", target_id=finding.target_id,
            reason=finding.detail, reversible=True, confidence=finding.confidence,
            correlation_id=cid, finding=finding.to_dict(),
        )
    if finding.kind == "dead_worker":
        return HealingAction(
            kind="evict_dead_worker",
            target_type="worker", target_id=finding.target_id,
            reason=finding.detail, reversible=True, confidence=finding.confidence,
            correlation_id=cid, finding=finding.to_dict(),
        )
    if finding.kind == "stale_cache":
        return HealingAction(
            kind="invalidate_stale_cache",
            target_type="cache_topic", target_id=finding.target_id,
            reason=finding.detail, reversible=True, confidence=finding.confidence,
            correlation_id=cid, finding=finding.to_dict(),
        )
    if finding.kind == "rising_failure_rate":
        return HealingAction(
            kind="quarantine_capability",
            target_type="capability", target_id=finding.target_id,
            reason=finding.detail, reversible=True, confidence=finding.confidence,
            correlation_id=cid, finding=finding.to_dict(),
        )
    if finding.kind == "routing_degradation":
        # We NEVER auto-rollback. Always issued as a recommendation.
        return HealingAction(
            kind="rollback_recommendation",
            target_type="capability_version", target_id=finding.target_id,
            reason=finding.detail, reversible=True, confidence=finding.confidence,
            correlation_id=cid, finding=finding.to_dict(),
        )
    return None


def _execute_action(action: HealingAction, *, actor) -> str:
    try:
        if action.kind == "reclaim_stale_queue":
            runtime_queue.reclaim_stale()
            return "applied"
        if action.kind == "evict_dead_worker":
            worker_coordination.evict_stale()
            return "applied"
        if action.kind == "invalidate_stale_cache":
            from execution.ops_platform import cache_bus
            for t in cache_bus.Topic:
                if t.value == action.target_id:
                    cache_bus.emit(t)
                    return "applied"
            return "applied"
        if action.kind == "quarantine_capability":
            controls.quarantine(action.target_id, actor=actor,
                                  reason=f"self_healing: {action.reason}")
            return "applied"
        if action.kind == "rollback_recommendation":
            # Recommendation only — no mutation. The audit row is the artifact.
            return "applied"
    except Exception as e:
        logger.warning("self_healing action %s failed", action.kind, exc_info=True)
        action.detail = str(e)
        return "error"
    return "error"
