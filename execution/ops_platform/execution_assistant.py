"""Execution Assistant — operational helper that wraps a capability execution
with guidance, default-filling, and failure-anticipation.

What the assistant produces (read-only, no LLM by default):

  prepare(capability_id, partial_inputs, role)
    → {required_inputs, suggested_defaults, contextual_hints,
       likely_failures, recommended_followups, prior_execution_evidence}

  explain_output(run_id)
    → {summary_paragraph, key_findings, suggested_next_actions}

  intent_to_capabilities(intent)  → uses recommendation_engine.recommend()
    → ranked options the user can click into

Design choices
--------------
- Deterministic by default — pulls from capability inputs metadata, prior
  feedback notes, prior run patterns. Cheap and predictable.
- Optional LLM polish for explain_output() when llm_client.is_available(),
  falls back to a templated summary.
- The assistant never runs a capability itself; it only stages and explains.
  Execution stays inside workflow_runner.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import asdict, dataclass, field

from execution.ops_platform import (
    feedback_store,
    recommendation_engine,
    semantic_analyzer,
    workflow_runner,
)
from execution.ops_platform.capability_registry import CapabilityRegistry, default_registry

logger = logging.getLogger(__name__)


@dataclass
class PrepareResult:
    capability_id: str
    required_inputs: list[dict] = field(default_factory=list)
    optional_inputs: list[dict] = field(default_factory=list)
    suggested_defaults: dict = field(default_factory=dict)
    contextual_hints: list[str] = field(default_factory=list)
    likely_failures: list[str] = field(default_factory=list)
    recommended_followups: list[str] = field(default_factory=list)
    prior_execution_evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ExplainResult:
    run_id: str
    capability_id: str
    summary_paragraph: str
    key_findings: list[str] = field(default_factory=list)
    suggested_next_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ── Public API ─────────────────────────────────────────────────────────


def prepare(
    capability_id: str,
    *,
    partial_inputs: dict | None = None,
    role: str | None = None,
    registry: CapabilityRegistry | None = None,
) -> PrepareResult:
    reg = registry or default_registry()
    cap = reg.get(capability_id)
    if cap is None:
        raise ValueError(f"capability '{capability_id}' not found")

    partial_inputs = partial_inputs or {}
    declared = cap.get("inputs") or []
    required = [i for i in declared if i.get("required")]
    optional = [i for i in declared if not i.get("required")]

    # 1. Suggested defaults — combine manifest defaults with last-known values
    #    pulled from this user's most recent successful run.
    suggested = _defaults_from_manifest(declared)
    recent = _recent_successful_inputs(capability_id)
    for name in [i["name"] for i in declared]:
        if name in partial_inputs:
            continue
        if name in recent:
            suggested[name] = recent[name]

    # 2. Contextual hints — built from enrichment + feedback wisdom.
    hints = _build_hints(cap, role=role)

    # 3. Likely failures — look at prior failed runs for this capability and
    #    surface the most common error message themes.
    likely = _likely_failures(capability_id)

    # 4. Recommended follow-ups — from operational graph followed_by edges.
    followups = _followup_capability_names(capability_id, reg)

    # 5. Prior execution evidence — small slice of recent stats.
    runs = workflow_runner.list_runs(capability_id=capability_id, limit=20)
    success = sum(1 for r in runs if r.status == "succeeded")
    evidence = {
        "recent_runs": len(runs),
        "recent_successes": success,
        "reliability_pct": round(success / len(runs) * 100, 1) if runs else None,
        "last_run_status": runs[0].status if runs else None,
        "last_run_at": runs[0].started_at if runs else None,
    }

    return PrepareResult(
        capability_id=capability_id,
        required_inputs=required,
        optional_inputs=optional,
        suggested_defaults=suggested,
        contextual_hints=hints,
        likely_failures=likely,
        recommended_followups=followups,
        prior_execution_evidence=evidence,
    )


def explain_output(
    run_id: str,
    *,
    registry: CapabilityRegistry | None = None,
) -> ExplainResult | None:
    reg = registry or default_registry()
    run = workflow_runner.get_run(run_id)
    if run is None:
        return None
    cap = reg.get(run.capability_id)

    response = run.response or {}
    summary = response.get("summary") or run.error_message or "Run completed."
    findings: list[str] = []
    if response.get("files_created"):
        findings.append(f"Created {len(response['files_created'])} file(s)")
    if response.get("components_added"):
        findings.append(f"Added {len(response['components_added'])} component(s)")
    if response.get("routes_added"):
        findings.append(f"Added {len(response['routes_added'])} route(s)")
    if response.get("known_issues"):
        sev = Counter(i.get("severity", "medium") for i in response["known_issues"])
        worst = "blocker" if sev.get("blocker") else "high" if sev.get("high") else "medium"
        findings.append(
            f"{len(response['known_issues'])} known issue(s), worst severity: {worst}"
        )

    next_actions: list[str] = []
    if response.get("next_recommended_tasks"):
        for t in response["next_recommended_tasks"][:3]:
            if isinstance(t, dict):
                next_actions.append(t.get("title") or t.get("description") or "Follow-up")
            elif isinstance(t, str):
                next_actions.append(t)
    if cap:
        for nbr in _followup_capability_names(run.capability_id, reg)[:2]:
            next_actions.append(f"Consider running: {nbr}")
    if not next_actions:
        next_actions.append("Submit feedback on this run so the platform learns from it")

    return ExplainResult(
        run_id=run_id,
        capability_id=run.capability_id,
        summary_paragraph=summary,
        key_findings=findings,
        suggested_next_actions=next_actions,
    )


def intent_to_capabilities(
    intent: str,
    *,
    role: str | None = None,
    department: str | None = None,
    top_k: int = 5,
) -> list[dict]:
    """One-shot adapter to the recommendation engine for intent-driven UX."""
    recs = recommendation_engine.recommend(
        query=intent, role=role, department=department, top_k=top_k
    )
    return [r.to_dict() for r in recs]


# ── Internal ───────────────────────────────────────────────────────────


def _defaults_from_manifest(declared: list[dict]) -> dict:
    out: dict = {}
    for spec in declared:
        if "default" in spec and spec["default"] is not None:
            out[spec["name"]] = spec["default"]
    return out


def _recent_successful_inputs(capability_id: str, *, lookback: int = 25) -> dict:
    """Pull the most-recent successful run's inputs as a default-fill template."""
    runs = workflow_runner.list_runs(capability_id=capability_id, limit=lookback)
    for r in runs:
        if r.status == "succeeded" and isinstance(r.inputs, dict):
            # Strip internal-only keys (start with __)
            return {k: v for k, v in r.inputs.items() if not k.startswith("__")}
    return {}


def _build_hints(cap: dict, *, role: str | None) -> list[str]:
    hints: list[str] = []
    enrichment = semantic_analyzer.load_enrichment(cap["id"]) or {}
    if enrichment.get("operational_intent"):
        hints.append(f"What this does: {enrichment['operational_intent']}")
    if enrichment.get("estimated_roi"):
        hints.append(f"Typical time savings: {enrichment['estimated_roi']}")
    if role and role in (enrichment.get("recommended_user_personas") or []):
        hints.append(f"This is a good fit for your role ({role}).")
    # Pull a recent operator note as wisdom-from-the-trenches.
    for record in feedback_store.list_feedback(cap["id"])[:3]:
        notes = record.get("operational_notes") or {}
        if isinstance(notes, dict):
            tip = notes.get("how_used") or notes.get("improvements_discovered")
            if tip:
                hints.append(f"Operator tip: {tip[:160]}")
                break
    return hints


def _likely_failures(capability_id: str) -> list[str]:
    runs = workflow_runner.list_runs(capability_id=capability_id, limit=50)
    failure_msgs: list[str] = []
    for r in runs:
        if r.status in ("error", "contract_failed", "llm_unavailable") and r.error_message:
            failure_msgs.append(r.error_message[:200])
    if not failure_msgs:
        return []
    # Cluster by first 60 chars — quick & dirty grouping
    buckets: Counter[str] = Counter(msg[:60] for msg in failure_msgs)
    out: list[str] = []
    for prefix, count in buckets.most_common(3):
        out.append(f"Seen {count}x: {prefix}{'...' if len(prefix) >= 60 else ''}")
    return out


def _followup_capability_names(capability_id: str, registry: CapabilityRegistry) -> list[str]:
    try:
        g = recommendation_engine._cached_graph()
    except Exception:
        return []
    by_id = registry.snapshot().by_id()
    out: list[str] = []
    for cid, _w in g.top_followed_by(capability_id, top_k=3):
        cap = by_id.get(cid)
        if cap:
            out.append(cap.get("name", cid))
    return out
