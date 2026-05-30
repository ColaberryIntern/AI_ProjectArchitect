"""Recovery coordinator — observes recovery-relevant signals and proposes
(or, when allowed, applies) safe autonomous actions.

Scope honesty
-------------
- This module ONLY proposes/applies recovery for actions whose rollback path
  is bounded and observable. It does NOT auto-merge split-brain state, does
  NOT auto-rollback application code, does NOT auto-mutate orchestration
  state without operator approval.
- Every autonomous action runs through Phase 8 ``agent_runtime`` with an
  agent the operator pre-registers (default agent name:
  ``recovery_coordinator``). Without that agent registered, the coordinator
  only produces RECOMMENDATIONS — no execution.
- Every action carries reasoning_chain + evidence_refs + rollback path,
  same contract as any other autonomous action.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from execution.ops_platform import (
    agent_registry, agent_runtime, audit_log, coordination_diagnostics,
    event_fabric, orchestration_runtime, poison_handler, projection_engine,
    redis_backends, transactional_outbox,
)

logger = logging.getLogger(__name__)

RECOVERY_AGENT_NAME = "recovery_coordinator"


@dataclass
class RecoveryRecommendation:
    kind: str
    title: str
    confidence: float
    rollback_path: str
    reasoning_chain: list
    evidence_refs: list
    auto_executable: bool
    risk: str
    correlation_id: str
    action: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ── Detectors → Recommendations ───────────────────────────────────────


def scan() -> list[RecoveryRecommendation]:
    """Read every signal and produce recommendations. Pure read; nothing
    mutates."""
    out: list[RecoveryRecommendation] = []
    out.extend(_outbox_backlog_recs())
    out.extend(_expired_claim_recs())
    out.extend(_redis_disconnect_recs())
    out.extend(_dlq_pending_recs())
    out.extend(_projection_drift_recs())
    return out


def execute_one(
    recommendation: RecoveryRecommendation,
    *,
    actor: dict | str = "recovery_coordinator",
) -> dict:
    """Execute a single recommendation if an agent with the right policy is
    registered and the action is auto_executable. Otherwise leaves it as a
    proposal and emits an audit row.
    """
    if not recommendation.auto_executable:
        return _surface_as_proposal(recommendation, actor=actor)
    agent = _find_recovery_agent()
    if agent is None:
        return _surface_as_proposal(recommendation, actor=actor,
                                       reason="no recovery agent registered")
    if recommendation.kind == "outbox_drain":
        return _execute_outbox_drain(recommendation, agent=agent)
    if recommendation.kind == "reclaim_expired_claims":
        return _execute_reclaim_claims(recommendation, agent=agent)
    if recommendation.kind == "projection_rebuild":
        return _execute_projection_rebuild(recommendation, agent=agent)
    return _surface_as_proposal(recommendation, actor=actor,
                                   reason="no handler for recommendation kind")


def execute_all_autoexecutable(*, actor: dict | str = "recovery_coordinator") -> dict:
    """One-shot drain: scan, execute every auto-executable recommendation."""
    recs = scan()
    out = []
    for rec in recs:
        if rec.auto_executable:
            out.append(execute_one(rec, actor=actor))
    return {"scanned": len(recs), "auto_executed": len(out), "results": out}


# ── Detectors ─────────────────────────────────────────────────────────


def _outbox_backlog_recs() -> list[RecoveryRecommendation]:
    metrics = transactional_outbox.metrics()
    pending = metrics["by_state"].get("pending", 0) + metrics["by_state"].get("failed", 0)
    if pending == 0:
        return []
    return [RecoveryRecommendation(
        kind="outbox_drain",
        title=f"Drain {pending} pending outbox entries",
        confidence=0.9,
        rollback_path="outbox entries can be re-enqueued from DLQ",
        reasoning_chain=[
            "outbox has pending or failed entries past their next_attempt_at",
            "drain is idempotent; entries retry with their existing attempt counter",
        ],
        evidence_refs=[{"source": "transactional_outbox.metrics", "data": metrics}],
        auto_executable=True, risk="low",
        correlation_id=str(uuid.uuid4()),
        action={"max_batch": min(100, pending)},
    )]


def _expired_claim_recs() -> list[RecoveryRecommendation]:
    expired = orchestration_runtime.reclaim_expired() if False else []
    # Use a dry-run check: list active claims with lease_until_epoch in the past
    import time
    active = orchestration_runtime.list_active_claims()
    candidates = [c for c in active if c.lease_until_epoch <= time.time()]
    if not candidates:
        return []
    return [RecoveryRecommendation(
        kind="reclaim_expired_claims",
        title=f"Reclaim {len(candidates)} expired orchestration step claim(s)",
        confidence=0.95,
        rollback_path="claims can be re-acquired by the next worker on its next poll",
        reasoning_chain=[
            "expired claims block other workers from picking up steps",
            "reclaim does not delete state — it only releases the lock",
        ],
        evidence_refs=[{"source": "orchestration_runtime.list_active_claims",
                          "expired_count": len(candidates)}],
        auto_executable=True, risk="low",
        correlation_id=str(uuid.uuid4()),
        action={"claim_count": len(candidates)},
    )]


def _redis_disconnect_recs() -> list[RecoveryRecommendation]:
    if redis_backends.is_available() and redis_backends._CLIENT is not None:
        return []
    return [RecoveryRecommendation(
        kind="redis_reconnect_required",
        title="Redis client not wired — operator action required",
        confidence=0.9,
        rollback_path="re-wire by calling redis_backends.activate(client) "
                        "and then transactional_outbox.reconcile_after_outage()",
        reasoning_chain=[
            "redis_backends._CLIENT is None",
            "downstream Redis-backed primitives raise RedisNotConfigured",
        ],
        evidence_refs=[{"source": "redis_backends._CLIENT", "value": None}],
        auto_executable=False,        # operator-managed by design
        risk="medium",
        correlation_id=str(uuid.uuid4()),
        action={},
    )]


def _dlq_pending_recs() -> list[RecoveryRecommendation]:
    dlq = transactional_outbox.list_dlq(limit=200)
    if not dlq:
        return []
    return [RecoveryRecommendation(
        kind="dlq_review_required",
        title=f"{len(dlq)} dead-letter entries awaiting operator review",
        confidence=0.7,
        rollback_path="entries remain in DLQ until operator calls "
                        "transactional_outbox.replay_dlq(outbox_id)",
        reasoning_chain=[
            "DLQ entries exhausted retries",
            "replay requires operator decision; no auto-replay",
        ],
        evidence_refs=[{"source": "transactional_outbox.list_dlq",
                          "count": len(dlq)}],
        auto_executable=False,
        risk="medium",
        correlation_id=str(uuid.uuid4()),
        action={"dlq_count": len(dlq)},
    )]


def _projection_drift_recs() -> list[RecoveryRecommendation]:
    """Check each registered projection's latest persisted state against a
    fresh rebuild. Drift suggests we should rebuild + persist."""
    drifted: list[dict] = []
    for meta in projection_engine.list_projections():
        try:
            compare = projection_engine.compare_with_latest(meta["name"])
        except KeyError:
            continue
        if compare.get("persisted") and not compare["states_match"]:
            drifted.append({"name": meta["name"],
                              "events_consumed": compare["rebuild"]["events_consumed"]})
    if not drifted:
        return []
    return [RecoveryRecommendation(
        kind="projection_rebuild",
        title=f"Rebuild {len(drifted)} projection(s) with state drift",
        confidence=0.85,
        rollback_path="rebuilds are deterministic; re-running matches the "
                        "current event log",
        reasoning_chain=[
            "compare_with_latest detected state drift in persisted projection",
            "rebuild re-derives state from the event log; idempotent",
        ],
        evidence_refs=[{"source": "projection_engine.compare_with_latest",
                          "drifted": drifted}],
        auto_executable=True, risk="low",
        correlation_id=str(uuid.uuid4()),
        action={"projections": [d["name"] for d in drifted]},
    )]


# ── Executors (autonomy-gated) ────────────────────────────────────────


def _find_recovery_agent():
    for a in agent_registry.list_agents():
        if a.name == RECOVERY_AGENT_NAME:
            return a
    return None


def _surface_as_proposal(rec: RecoveryRecommendation, *, actor,
                            reason: str = "") -> dict:
    event_fabric.emit(
        "recovery.proposal",
        payload={"kind": rec.kind, "title": rec.title,
                   "confidence": rec.confidence, "risk": rec.risk,
                   "auto_executable": rec.auto_executable,
                   "rollback_path": rec.rollback_path,
                   "reason_not_executed": reason or "operator-managed"},
        correlation_id=rec.correlation_id,
        durability_scope="single-host",
        consistency_scope="at-least-once",
    )
    audit_log.record(
        action="recovery.proposed", entity_type="recovery_recommendation",
        entity_id=rec.correlation_id,
        actor=actor if isinstance(actor, dict) else {"name": str(actor), "system": True},
        correlation_id=rec.correlation_id,
        metadata={"kind": rec.kind, "auto_executable": rec.auto_executable,
                  "reason_not_executed": reason},
    )
    return {"outcome": "PROPOSED", "kind": rec.kind, "reason": reason,
              "correlation_id": rec.correlation_id}


def _execute_outbox_drain(rec, agent) -> dict:
    try:
        ex = agent_runtime.execute(
            agent_id=agent.agent_id,
            action_kind="outbox_drain",
            target={"entity_type": "outbox"},
            inputs={"max_batch": rec.action.get("max_batch", 25)},
            reasoning_chain=rec.reasoning_chain,
            evidence_refs=rec.evidence_refs,
            confidence=rec.confidence,
            rollback_plan=rec.rollback_path,
            risk=rec.risk,
        )
    except agent_runtime.AutonomyViolation as e:
        return _surface_as_proposal(rec, actor=agent.name, reason=str(e))
    if ex.outcome in ("SUGGESTED", "DENIED", "PAUSED", "APPROVAL_REQUIRED"):
        return {"outcome": ex.outcome, "execution_id": ex.execution_id,
                  "kind": rec.kind, "correlation_id": rec.correlation_id}
    result = transactional_outbox.drain_once(max_batch=rec.action.get("max_batch", 25))
    audit_log.record(
        action="recovery.applied", entity_type="recovery_recommendation",
        entity_id=rec.correlation_id, actor={"name": agent.name, "system": True},
        correlation_id=rec.correlation_id,
        metadata={"kind": rec.kind, "result": result},
    )
    return {"outcome": "APPLIED", "kind": rec.kind, "result": result,
              "correlation_id": rec.correlation_id}


def _execute_reclaim_claims(rec, agent) -> dict:
    try:
        ex = agent_runtime.execute(
            agent_id=agent.agent_id,
            action_kind="reclaim_expired_claims",
            target={"entity_type": "orchestration_claims"},
            inputs={},
            reasoning_chain=rec.reasoning_chain,
            evidence_refs=rec.evidence_refs,
            confidence=rec.confidence,
            rollback_plan=rec.rollback_path,
            risk=rec.risk,
        )
    except agent_runtime.AutonomyViolation as e:
        return _surface_as_proposal(rec, actor=agent.name, reason=str(e))
    if ex.outcome in ("SUGGESTED", "DENIED", "PAUSED", "APPROVAL_REQUIRED"):
        return {"outcome": ex.outcome, "execution_id": ex.execution_id,
                  "kind": rec.kind, "correlation_id": rec.correlation_id}
    reclaimed = orchestration_runtime.reclaim_expired()
    audit_log.record(
        action="recovery.applied", entity_type="recovery_recommendation",
        entity_id=rec.correlation_id, actor={"name": agent.name, "system": True},
        correlation_id=rec.correlation_id,
        metadata={"kind": rec.kind, "reclaimed_count": len(reclaimed)},
    )
    return {"outcome": "APPLIED", "kind": rec.kind,
              "reclaimed_count": len(reclaimed),
              "correlation_id": rec.correlation_id}


def _execute_projection_rebuild(rec, agent) -> dict:
    try:
        ex = agent_runtime.execute(
            agent_id=agent.agent_id,
            action_kind="projection_rebuild",
            target={"entity_type": "projections"},
            inputs={"projections": rec.action.get("projections") or []},
            reasoning_chain=rec.reasoning_chain,
            evidence_refs=rec.evidence_refs,
            confidence=rec.confidence,
            rollback_plan=rec.rollback_path,
            risk=rec.risk,
        )
    except agent_runtime.AutonomyViolation as e:
        return _surface_as_proposal(rec, actor=agent.name, reason=str(e))
    if ex.outcome in ("SUGGESTED", "DENIED", "PAUSED", "APPROVAL_REQUIRED"):
        return {"outcome": ex.outcome, "execution_id": ex.execution_id,
                  "kind": rec.kind, "correlation_id": rec.correlation_id}
    rebuilt = []
    for name in rec.action.get("projections", []):
        try:
            r = projection_engine.rebuild(name)
            rebuilt.append({"name": name,
                              "events_consumed": r["events_consumed"]})
        except KeyError:
            rebuilt.append({"name": name, "error": "no such projection"})
    audit_log.record(
        action="recovery.applied", entity_type="recovery_recommendation",
        entity_id=rec.correlation_id, actor={"name": agent.name, "system": True},
        correlation_id=rec.correlation_id,
        metadata={"kind": rec.kind, "rebuilt": rebuilt},
    )
    return {"outcome": "APPLIED", "kind": rec.kind, "rebuilt": rebuilt,
              "correlation_id": rec.correlation_id}
