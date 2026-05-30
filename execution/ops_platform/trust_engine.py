"""Trust engine — derives a unified TrustProfile per capability.

Inputs (cheap reads, no LLM):
  - reputation_scorer        (reliability, business impact, feedback)
  - reputation_history       (volatility = stddev of recent scores)
  - audit_log                (rollback events, deprecations)
  - capability_versions      (version age, deprecated count, rollback count)
  - feedback_store           (feedback quantity + quality)
  - workflow_runner          (execution consistency)

Outputs:
  TrustProfile {
    trust_score: 0..100,
    risk_level: LOW | MODERATE | HIGH | CRITICAL,
    operational_maturity: 1..5,
    deployment_recommendation: SAFE_FOR_PRODUCTION | LIMITED_ROLLOUT
                                | REQUIRES_REVIEW | DO_NOT_DEPLOY,
    blocking_issues: list[str],
    confidence: 0..1,
    components: {individual sub-scores for explainability},
  }
"""

from __future__ import annotations

import logging
import math
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from execution.ops_platform import (
    audit_log, capability_versions, feedback_store,
    reputation_scorer, workflow_runner,
)
from execution.ops_platform.capability_registry import CapabilityRegistry, default_registry

logger = logging.getLogger(__name__)


@dataclass
class TrustProfile:
    capability_id: str
    trust_score: float
    risk_level: str
    operational_maturity: int
    deployment_recommendation: str
    blocking_issues: list = field(default_factory=list)
    confidence: float = 0.0
    components: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# Weights for the trust composite (sum = 1.0)
_WEIGHTS = {
    "reliability": 0.25,
    "audit_cleanliness": 0.15,
    "rollback_stability": 0.10,
    "prompt_stability": 0.10,
    "execution_consistency": 0.10,
    "feedback_quality": 0.10,
    "operator_approval": 0.10,
    "version_maturity": 0.05,
    "rollout_success": 0.05,
}


def score(
    capability_id: str,
    *,
    registry: CapabilityRegistry | None = None,
    record_audit: bool = True,
) -> TrustProfile:
    reg = registry or default_registry()
    if reg.get(capability_id) is None:
        return TrustProfile(
            capability_id=capability_id, trust_score=0.0,
            risk_level="CRITICAL", operational_maturity=1,
            deployment_recommendation="DO_NOT_DEPLOY",
            blocking_issues=["capability not registered"],
            confidence=1.0,
        )

    runs = workflow_runner.list_runs(capability_id=capability_id, limit=500)
    total = len(runs)
    succ = sum(1 for r in runs if r.status == "succeeded")
    reliability = (succ / total) if total else 0.0
    consistency_durations = [r.duration_ms for r in runs
                              if r.status == "succeeded" and r.duration_ms]
    if len(consistency_durations) >= 3:
        try:
            cv = (statistics.stdev(consistency_durations)
                  / max(1, statistics.mean(consistency_durations)))
            execution_consistency = max(0.0, 1.0 - min(1.0, cv))
        except statistics.StatisticsError:
            execution_consistency = 0.5
    else:
        execution_consistency = 0.3

    rep_score = (reputation_scorer.load_score(capability_id) or {}).get("reputation_score", 0)
    rep_history = reputation_scorer.load_history(capability_id) if hasattr(reputation_scorer, "load_history") else []
    if len(rep_history) >= 3:
        try:
            volatility = statistics.stdev([h["reputation_score"] for h in rep_history])
        except statistics.StatisticsError:
            volatility = 0.0
        prompt_stability = max(0.0, 1.0 - min(1.0, volatility / 30))
    else:
        prompt_stability = 0.5

    # Audit cleanliness: count enforcement.denied, optimizer.applied, rollback rows
    audit_rows = audit_log.list_entries(entity_id=capability_id, days=90, limit=500)
    enforcement_denials = sum(1 for r in audit_rows if r.get("action") == "enforcement.denied")
    rollback_events = sum(1 for r in audit_rows if r.get("action") == "rollback.executed")
    deprecations = sum(1 for r in audit_rows
                        if r.get("action") == "capability_version.deprecated")
    audit_cleanliness = max(0.0, 1.0 - 0.10 * enforcement_denials - 0.05 * deprecations)
    rollback_stability = max(0.0, 1.0 - 0.20 * rollback_events)

    # Feedback quality (avg rating + count)
    agg = feedback_store.get_aggregate(capability_id)
    feedback_count = agg.get("total_feedback", 0) or 0
    feedback_avg = agg.get("overall_average") or 0
    feedback_quality = min(1.0, (feedback_avg / 5.0) * 0.8 + min(1.0, feedback_count / 10) * 0.2)
    operator_approval = min(1.0, feedback_count / 5)

    # Version maturity: oldest approved version's age in days, capped at 90
    versions = capability_versions.list_versions(capability_id)
    approved = [v for v in versions if v.status == "approved"]
    if approved:
        try:
            oldest = min(datetime.fromisoformat(v.approval_timestamp)
                          for v in approved if v.approval_timestamp)
            age_days = (datetime.now(timezone.utc) - oldest).days
            version_maturity = min(1.0, age_days / 90)
        except (TypeError, ValueError):
            version_maturity = 0.3
    else:
        version_maturity = 0.0

    # Rollout success: succeeded experimental runs / total experimental runs
    rollout_success = _rollout_success(runs, versions)

    components = {
        "reliability": round(reliability, 3),
        "audit_cleanliness": round(audit_cleanliness, 3),
        "rollback_stability": round(rollback_stability, 3),
        "prompt_stability": round(prompt_stability, 3),
        "execution_consistency": round(execution_consistency, 3),
        "feedback_quality": round(feedback_quality, 3),
        "operator_approval": round(operator_approval, 3),
        "version_maturity": round(version_maturity, 3),
        "rollout_success": round(rollout_success, 3),
    }
    composite = sum(components[k] * _WEIGHTS[k] for k in _WEIGHTS) * 100
    composite = round(composite, 2)

    blocking_issues = _blocking_issues(reliability, total, audit_cleanliness, rollback_events)
    risk = _risk_level(composite, blocking_issues, rollback_events)
    maturity = _maturity_score(version_maturity, total, feedback_count)
    recommendation = _recommendation(composite, risk, blocking_issues)
    confidence = min(1.0, total / 50) if total else 0.0

    profile = TrustProfile(
        capability_id=capability_id, trust_score=composite,
        risk_level=risk, operational_maturity=maturity,
        deployment_recommendation=recommendation,
        blocking_issues=blocking_issues,
        confidence=round(confidence, 2),
        components=components,
    )
    if record_audit:
        audit_log.record(
            action="trust.calculated", entity_type="capability",
            entity_id=capability_id,
            actor={"name": "trust_engine", "system": True},
            metadata={
                "trust_score": composite, "risk_level": risk,
                "deployment_recommendation": recommendation,
            },
        )
    return profile


def trust_report(*, registry: CapabilityRegistry | None = None,
                  top_n: int | None = None) -> list[dict]:
    reg = registry or default_registry()
    out: list[dict] = []
    for cap in reg.snapshot().capabilities:
        profile = score(cap["id"], registry=reg, record_audit=False)
        out.append(profile.to_dict())
    out.sort(key=lambda r: r["trust_score"], reverse=True)
    return out[:top_n] if top_n else out


# ── Internal ───────────────────────────────────────────────────────────


def _rollout_success(runs, versions) -> float:
    by_id = {v.version_id: v for v in versions}
    exp_runs = [
        r for r in runs
        if isinstance(r.inputs, dict)
        and r.inputs.get("__capability_version_id") in by_id
        and by_id[r.inputs["__capability_version_id"]].status == "experimental"
    ]
    if not exp_runs:
        return 0.5  # neutral when no experimental rollout has happened
    succ = sum(1 for r in exp_runs if r.status == "succeeded")
    return succ / len(exp_runs)


def _blocking_issues(reliability: float, total: int, audit_cleanliness: float,
                       rollback_events: int) -> list[str]:
    issues: list[str] = []
    if total >= 10 and reliability < 0.5:
        issues.append(f"reliability {reliability * 100:.0f}% across {total} runs is below 50%")
    if audit_cleanliness < 0.4:
        issues.append("audit log shows multiple enforcement denials or deprecations")
    if rollback_events >= 2:
        issues.append(f"{rollback_events} rollback events on record")
    if total == 0:
        issues.append("no run history yet")
    return issues


def _risk_level(score: float, blocking_issues: list[str], rollbacks: int) -> str:
    if blocking_issues and score < 35:
        return "CRITICAL"
    if rollbacks >= 2 or score < 50:
        return "HIGH"
    if score < 70:
        return "MODERATE"
    return "LOW"


def _maturity_score(version_maturity: float, total_runs: int, feedback_count: int) -> int:
    pts = 1
    if total_runs >= 5: pts += 1
    if total_runs >= 50: pts += 1
    if feedback_count >= 3: pts += 1
    if version_maturity >= 0.6: pts += 1
    return min(5, pts)


def _recommendation(score: float, risk: str, blocking_issues: list[str]) -> str:
    if blocking_issues and risk == "CRITICAL":
        return "DO_NOT_DEPLOY"
    if risk == "HIGH":
        return "REQUIRES_REVIEW"
    if score < 70:
        return "LIMITED_ROLLOUT"
    return "SAFE_FOR_PRODUCTION"
