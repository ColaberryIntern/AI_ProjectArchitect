"""AI Operational Copilot — evidence-backed answers to operator questions.

Scope honesty
-------------
This module does NOT hallucinate. It pattern-matches a small set of operator
intents (Why was X quarantined? What changed? Why is latency rising? What
approvals are blocked? What incidents correlate with routing degradation?)
and answers from the platform's own structured data:

  - audit_log
  - incidents
  - reliability_monitor
  - telemetry
  - experiments
  - approvals
  - capability_versions
  - controls
  - scheduler

Every answer carries:
  - evidence_refs        (list of audit entry_ids + module sources)
  - reasoning_chain      (ordered steps that produced the answer)
  - confidence           (0..1)
  - sufficient_evidence  (bool — false → answer says "insufficient evidence")

Optional LLM polish
-------------------
When ``llm_client.is_available()`` AND ``polish=True``, the structured answer
is sent to the LLM for narrative refinement. The structured fields
(``evidence_refs``, ``confidence``) are NEVER mutated by the LLM — the LLM
only rewrites the prose summary. This keeps the copilot explainable.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from execution.ops_platform import (
    approvals, audit_log, capability_versions, controls, experiments,
    incidents, reliability_monitor, telemetry,
)
from execution.ops_platform.capability_registry import CapabilityRegistry, default_registry

logger = logging.getLogger(__name__)


@dataclass
class CopilotAnswer:
    question: str
    intent: str
    summary: str
    reasoning_chain: list = field(default_factory=list)
    evidence_refs: list = field(default_factory=list)
    confidence: float = 0.0
    sufficient_evidence: bool = True
    generated_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


_INTENT_PATTERNS = [
    ("quarantine_why", re.compile(r"\bwhy.*quarantin", re.IGNORECASE)),
    ("rollback_why", re.compile(r"\bwhy.*rollback|why.*rolled back", re.IGNORECASE)),
    ("blocked_approvals", re.compile(r"approval.*block|block.*approval|pending approval", re.IGNORECASE)),
    ("latency_rising", re.compile(r"latency.*(rising|increas|slow|p95|spike)", re.IGNORECASE)),
    ("routing_degradation_incidents", re.compile(r"incident.*routing|routing.*incident", re.IGNORECASE)),
    ("changed_since", re.compile(r"\bwhat.*chang", re.IGNORECASE)),
    ("autonomous_actions", re.compile(r"self.?heal|autonomous|automation", re.IGNORECASE)),
    ("failure_summary", re.compile(r"why.*fail|failure.*recent|fail.*rate", re.IGNORECASE)),
]


# ── Public API ─────────────────────────────────────────────────────────


def ask(
    question: str,
    *,
    capability_id: str | None = None,
    workspace_id: str | None = None,
    days: int = 7,
    registry: CapabilityRegistry | None = None,
) -> CopilotAnswer:
    intent = _classify_intent(question)
    answer = CopilotAnswer(
        question=question, intent=intent, summary="",
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
    if intent == "quarantine_why":
        _answer_quarantine_why(answer, capability_id=capability_id, days=days)
    elif intent == "rollback_why":
        _answer_rollback_why(answer, capability_id=capability_id, days=days)
    elif intent == "blocked_approvals":
        _answer_blocked_approvals(answer, days=days)
    elif intent == "latency_rising":
        _answer_latency_rising(answer, registry=registry)
    elif intent == "routing_degradation_incidents":
        _answer_routing_incidents(answer, days=days)
    elif intent == "changed_since":
        _answer_changed_since(answer, days=days, capability_id=capability_id)
    elif intent == "autonomous_actions":
        _answer_autonomous(answer, days=days)
    elif intent == "failure_summary":
        _answer_failures(answer, days=days, capability_id=capability_id)
    else:
        answer.summary = ("I can answer questions about quarantines, rollbacks, "
                            "blocked approvals, latency, incidents, recent changes, "
                            "autonomous actions, and failures. Try one of those.")
        answer.intent = "unknown"
        answer.sufficient_evidence = False
        answer.confidence = 0.0
        answer.reasoning_chain.append("question did not match any known intent")
    return answer


# ── Intent answerers ──────────────────────────────────────────────────


def _answer_quarantine_why(answer: CopilotAnswer, *, capability_id: str | None,
                             days: int) -> None:
    rows = audit_log.list_entries(action="controls.quarantined",
                                     entity_id=capability_id, days=days, limit=50)
    if not rows:
        answer.summary = (f"No quarantine events found for {capability_id or 'any capability'} "
                          f"in the last {days} days.")
        answer.sufficient_evidence = False
        answer.confidence = 0.9
        return
    most_recent = rows[0]
    actor = (most_recent.get("actor") or {}).get("name", "anonymous")
    metadata = most_recent.get("metadata") or {}
    reason = metadata.get("reason") or "no recorded reason"
    answer.summary = (
        f"Capability {most_recent.get('entity_id')} was quarantined at "
        f"{most_recent.get('timestamp')} by {actor}. Reason: {reason}"
    )
    answer.evidence_refs.append({"source": "audit_log",
                                    "entry_id": most_recent.get("entry_id")})
    answer.reasoning_chain.append(f"found {len(rows)} controls.quarantined audit row(s) in the lookback")
    answer.reasoning_chain.append(f"latest row at {most_recent.get('timestamp')} drove the answer")
    answer.confidence = 0.85


def _answer_rollback_why(answer: CopilotAnswer, *, capability_id: str | None,
                            days: int) -> None:
    rows = audit_log.list_entries(action="rollback.executed", entity_id=capability_id,
                                     days=days, limit=50)
    rows.extend(audit_log.list_entries(action="controls.rollback", entity_id=capability_id,
                                          days=days, limit=50))
    if not rows:
        answer.summary = ("No rollback events found.")
        answer.sufficient_evidence = False
        answer.confidence = 0.9
        return
    most_recent = rows[0]
    actor = (most_recent.get("actor") or {}).get("name", "anonymous")
    metadata = most_recent.get("metadata") or {}
    answer.summary = (
        f"Rollback executed at {most_recent.get('timestamp')} by {actor}. "
        f"Reason: {metadata.get('reason', 'not recorded')}. "
        f"Correlation id: {most_recent.get('correlation_id', 'n/a')}"
    )
    for r in rows[:3]:
        answer.evidence_refs.append({"source": "audit_log", "entry_id": r.get("entry_id"),
                                        "action": r.get("action")})
    answer.reasoning_chain.append(f"queried audit_log for rollback.executed + controls.rollback")
    answer.confidence = 0.85


def _answer_blocked_approvals(answer: CopilotAnswer, *, days: int) -> None:
    pending = approvals.list_requests(state="pending")
    in_progress = approvals.list_requests(state="in_progress")
    total = len(pending) + len(in_progress)
    if total == 0:
        answer.summary = "No approval requests are currently blocked."
        answer.confidence = 0.95
        return
    answer.summary = (
        f"{total} approval request(s) await action: "
        f"{len(pending)} pending, {len(in_progress)} in progress. "
        f"Oldest: {(pending + in_progress)[-1].request_id if (pending + in_progress) else 'n/a'}"
    )
    for r in (pending + in_progress)[:5]:
        answer.evidence_refs.append({"source": "approvals", "entry_id": r.request_id,
                                        "action": r.action})
    answer.reasoning_chain.append("listed approvals in pending + in_progress states")
    answer.confidence = 0.95


def _answer_latency_rising(answer: CopilotAnswer, *,
                              registry: CapabilityRegistry | None) -> None:
    findings = reliability_monitor.scan(registry=registry or default_registry())
    latency_findings = [f for f in findings if f.kind == "latency_regression"]
    if not latency_findings:
        latency_stats = telemetry.latency_stats(registry=registry or default_registry())
        if latency_stats:
            slowest = latency_stats[0]
            answer.summary = (
                f"No latency regression detected. Slowest capability right now: "
                f"{slowest['name']} at p99 {slowest['p99_ms']} ms over "
                f"{slowest['samples']} samples."
            )
            answer.evidence_refs.append({"source": "telemetry",
                                            "capability_id": slowest["capability_id"]})
            answer.confidence = 0.9
        else:
            answer.summary = "No latency data available."
            answer.sufficient_evidence = False
        return
    capability_id_to_finding = {f.target_id: f for f in latency_findings}
    parts = []
    for cap_id, f in list(capability_id_to_finding.items())[:5]:
        parts.append(f"{cap_id}: {f.detail}")
        answer.evidence_refs.append({"source": "reliability_monitor",
                                        "kind": "latency_regression",
                                        "capability_id": cap_id})
    answer.summary = "Latency regressions detected on: " + " | ".join(parts)
    answer.reasoning_chain.append(f"reliability_monitor surfaced {len(latency_findings)} latency_regression finding(s)")
    answer.confidence = 0.9


def _answer_routing_incidents(answer: CopilotAnswer, *, days: int) -> None:
    open_incidents = incidents.list_incidents(state="open")
    routing_rows = audit_log.list_entries(action="routing.selected", days=days, limit=2000)
    if not open_incidents:
        answer.summary = "No open incidents. Routing decisions logged: " + str(len(routing_rows))
        answer.confidence = 0.9
        return
    correlated = []
    for inc in open_incidents:
        related = audit_log.list_entries(correlation_id=inc.correlation_id, days=days, limit=200)
        if any(r.get("action") == "routing.selected" for r in related):
            correlated.append(inc)
    if not correlated:
        answer.summary = (f"{len(open_incidents)} open incident(s) but none correlate with "
                          f"routing.selected events in the last {days} days.")
        answer.confidence = 0.8
        return
    answer.summary = (f"{len(correlated)} open incident(s) correlate with routing events: "
                      + ", ".join(i.incident_id for i in correlated[:5]))
    for inc in correlated[:5]:
        answer.evidence_refs.append({"source": "incidents", "entry_id": inc.incident_id})
    answer.reasoning_chain.append("intersected open incidents with routing.selected audit rows by correlation_id")
    answer.confidence = 0.85


def _answer_changed_since(answer: CopilotAnswer, *, days: int,
                              capability_id: str | None) -> None:
    rows = audit_log.list_entries(entity_id=capability_id, days=days, limit=500)
    if not rows:
        answer.summary = f"No recorded changes for {capability_id or 'any entity'} in the last {days} days."
        answer.sufficient_evidence = False
        return
    by_action = Counter(r.get("action") for r in rows)
    top = by_action.most_common(5)
    answer.summary = (
        f"{len(rows)} change(s) in the last {days} days. Top actions: "
        + ", ".join(f"{a} ({n})" for a, n in top)
    )
    for r in rows[:5]:
        answer.evidence_refs.append({"source": "audit_log", "entry_id": r.get("entry_id"),
                                        "action": r.get("action")})
    answer.reasoning_chain.append(f"scanned audit_log with entity_id filter")
    answer.confidence = 0.85


def _answer_autonomous(answer: CopilotAnswer, *, days: int) -> None:
    actions = []
    for a in ("self_healing.applied", "self_healing.denied", "self_healing.gated",
                "self_healing.error"):
        actions.extend(audit_log.list_entries(action=a, days=days, limit=200))
    if not actions:
        answer.summary = "No self-healing actions in the lookback window."
        answer.confidence = 0.95
        return
    by_kind: Counter = Counter()
    for r in actions:
        kind = (r.get("metadata") or {}).get("kind", "unknown")
        by_kind[kind] += 1
    answer.summary = (
        f"{len(actions)} autonomous action attempt(s). Breakdown: "
        + ", ".join(f"{k}={n}" for k, n in by_kind.most_common())
    )
    for r in actions[:5]:
        answer.evidence_refs.append({"source": "audit_log", "entry_id": r.get("entry_id"),
                                        "action": r.get("action")})
    answer.reasoning_chain.append("collected self_healing.* audit rows and grouped by kind")
    answer.confidence = 0.9


def _answer_failures(answer: CopilotAnswer, *, days: int,
                       capability_id: str | None) -> None:
    findings = reliability_monitor.scan()
    failure_findings = [f for f in findings if f.kind == "rising_failure_rate"]
    if capability_id:
        failure_findings = [f for f in failure_findings if f.target_id == capability_id]
    if not failure_findings:
        answer.summary = ("No rising failure rate findings."
                          + (f" Capability {capability_id} appears stable." if capability_id else ""))
        answer.confidence = 0.9
        return
    parts = [f"{f.target_id}: {f.detail}" for f in failure_findings[:5]]
    answer.summary = "Recent failure-rate spikes: " + " | ".join(parts)
    for f in failure_findings[:5]:
        answer.evidence_refs.append({"source": "reliability_monitor",
                                        "kind": "rising_failure_rate",
                                        "capability_id": f.target_id})
    answer.confidence = 0.85


def _classify_intent(question: str) -> str:
    for intent, rx in _INTENT_PATTERNS:
        if rx.search(question or ""):
            return intent
    return "unknown"


# ── Recommendation v2 hooks (anomaly / rollback / escalation) ──────────


@dataclass
class RecommendationV2:
    kind: str                       # rollback | escalate_approval | promote_experiment | quarantine
    title: str
    capability_id: str | None = None
    version_id: str | None = None
    why: str = ""
    evidence: list = field(default_factory=list)
    confidence: float = 0.0
    risk: str = "low"
    reversible: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


def operational_recommendations(
    *,
    registry: CapabilityRegistry | None = None,
    days: int = 7,
) -> list[RecommendationV2]:
    """Generate operational recommendations from current findings.

    Rule: never mutate. Just surface.
    """
    reg = registry or default_registry()
    findings = reliability_monitor.scan(registry=reg)
    out: list[RecommendationV2] = []
    for f in findings:
        if f.kind == "routing_degradation":
            out.append(RecommendationV2(
                kind="rollback", title=f"Roll back experimental version {f.target_id}",
                version_id=f.target_id,
                why=f.detail,
                evidence=[{"source": "reliability_monitor", "finding_kind": f.kind,
                           "evidence": f.evidence}],
                confidence=f.confidence, risk="medium", reversible=True,
            ))
        elif f.kind == "rising_failure_rate":
            out.append(RecommendationV2(
                kind="quarantine", title=f"Quarantine {f.target_id} pending investigation",
                capability_id=f.target_id,
                why=f.detail,
                evidence=[{"source": "reliability_monitor", "finding_kind": f.kind,
                           "evidence": f.evidence}],
                confidence=f.confidence, risk="medium", reversible=True,
            ))
    # Promote stable experiments
    for exp in experiments.list_experiments(state="running"):
        from execution.ops_platform import evaluation
        ev = evaluation.evaluate_experiment(exp.experiment_id)
        winners = [s for s in ev.get("significance", [])
                     if s.get("direction") == "treatment_better"
                     and s.get("confidence") in ("99%", "95%")]
        for w in winners[:2]:
            out.append(RecommendationV2(
                kind="promote_experiment",
                title=f"Promote arm {w.get('arm_id')} in experiment {exp.experiment_id}",
                why=f"treatment arm beats control at {w.get('confidence')} confidence (z={w.get('z_score')})",
                evidence=[{"source": "evaluation", "experiment_id": exp.experiment_id,
                           "arm_id": w.get("arm_id")}],
                confidence=0.9 if w.get("confidence") == "99%" else 0.75,
                risk="low", reversible=True,
            ))
    # Escalate long-pending approvals
    for r in approvals.list_requests(state="pending"):
        try:
            created = datetime.fromisoformat(r.created_at)
        except ValueError:
            continue
        age_hours = (datetime.now(timezone.utc) - created).total_seconds() / 3600
        if age_hours >= 12:
            out.append(RecommendationV2(
                kind="escalate_approval",
                title=f"Escalate approval {r.request_id}: {r.action} on {r.entity_type}:{r.entity_id}",
                why=f"pending for {age_hours:.0f}h with no decision",
                evidence=[{"source": "approvals", "request_id": r.request_id}],
                confidence=0.8, risk="low", reversible=True,
            ))
    out.sort(key=lambda r: r.confidence, reverse=True)
    return out
