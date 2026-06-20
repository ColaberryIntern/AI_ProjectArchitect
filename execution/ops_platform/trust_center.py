"""Trust Command Center — read-only aggregator (Phase 10 v1).

Powers /admin/trust. Pure reads of EXISTING stores; no new writes, no mutation.
Every section is defensively wrapped: a missing/erroring source degrades to
``{"status": "unavailable", ...}`` rather than raising, so the dashboard (and app
startup) never breaks on partial data. Metrics that have no source yet return
``{"status": "not_instrumented", ...}`` so the UI can label them honestly
(no fabricated numbers — see docs/trust-audit/dashboard-design.md).

Heavy ops modules are imported lazily inside functions to keep this import-safe
in any environment.
"""

from __future__ import annotations

import json
import logging

from config.settings import PROJECT_ROOT

logger = logging.getLogger(__name__)

# Static scorecard from the 2026-06-20 TBI audit (docs/trust-audit/trust-scorecard.md).
# v1 surfaces these labeled as a point-in-time audit; live computation is a later
# phase (dashboard-design.md v3). Not fabricated — sourced from the audit.
_AUDIT_SCORECARD = {
    "overall": 74,
    "pillars": {
        "governance": 86, "auditability": 80, "explainability": 78,
        "reliability": 78, "observability": 72, "security": 70,
        "business_impact": 68, "privacy": 62,
    },
    "source": "static_audit_2026-06-20",
    "note": "Point-in-time audit values; live computation is a later phase.",
}


def _safe(fn, default):
    try:
        return fn()
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("trust_center section failed: %s", type(e).__name__, exc_info=True)
        return {"status": "unavailable", "reason": type(e).__name__} if default is None else default


# ── TBI compliance (real: scans committed *.tbi.json and scores them) ──


def tbi_compliance_summary() -> dict:
    from execution.ops_platform import tbi_compliance
    counts = {"compliant": 0, "conditional": 0, "non_compliant": 0}
    artifacts = []
    for path in sorted(PROJECT_ROOT.rglob("*.tbi.json")):
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        if "/.claude/" in f"/{rel}" or rel.startswith(".claude/"):
            continue  # skip worktree copies
        try:
            att = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        v = tbi_compliance.evaluate_attestation(att)
        counts[v.verdict] = counts.get(v.verdict, 0) + 1
        artifacts.append({
            "artifact_id": v.artifact_id, "verdict": v.verdict,
            "risk_level": att.get("risk_level"), "path": rel,
        })
    return {
        "framework_version": tbi_compliance.CURRENT_FRAMEWORK_VERSION,
        "total": len(artifacts), "counts": counts, "artifacts": artifacts,
    }


def runtime_agents_summary() -> dict:
    from execution.ops_platform import runtime_agents
    decls = runtime_agents.load_declarations()
    return {
        "total": len(decls),
        "agents": [
            {"id": d.get("id"), "name": d.get("name"),
             "autonomy_policy": d.get("autonomy_policy"),
             "status": d.get("status"), "entrypoint": d.get("entrypoint")}
            for d in decls
        ],
    }


def _audit_stats(days: int = 7) -> dict:
    from execution.ops_platform import audit_log
    return audit_log.stats(days=days)


def _registered_agents() -> dict:
    from execution.ops_platform import agent_registry
    agents = agent_registry.list_agents()
    return {
        "total": len(agents),
        "paused": sum(1 for a in agents if getattr(a, "paused", False)),
        "agents": [
            {"agent_id": a.agent_id, "name": a.name,
             "autonomy_policy": a.autonomy_policy, "paused": a.paused}
            for a in agents
        ],
    }


def _telemetry_health() -> dict:
    from execution.ops_platform import telemetry
    return telemetry.health_summary().to_dict()


# ── View aggregators ──


def overview() -> dict:
    """Executive view."""
    return {
        "view": "executive",
        "trust_score": _AUDIT_SCORECARD,
        "compliance": _safe(tbi_compliance_summary, None),
        "runtime_agents": _safe(runtime_agents_summary, None),
        "audit_7d": _safe(lambda: _audit_stats(7), None),
        "cost_7d": {"status": "not_instrumented",
                    "reason": "no cost ledger yet (gap OBS-5 / C4)"},
    }


def operations() -> dict:
    """Operations view."""
    return {
        "view": "operations",
        "health": _safe(_telemetry_health, None),
        "agents": _safe(_registered_agents, None),
        "runtime_agents": _safe(runtime_agents_summary, None),
    }


def governance() -> dict:
    """Governance view: autonomy postures + governance-relevant audit actions."""
    def gov_actions():
        from execution.ops_platform import audit_log
        stats = audit_log.stats(days=30)
        by_action = stats.get("by_action", {})
        keys = ("approval.requested", "approval.approved", "approval.rejected",
                "enforcement.denied", "agent.paused", "controls.frozen",
                "controls.quarantined", "rollback.executed", "tbi.evaluated")
        return {k: by_action.get(k, 0) for k in keys}
    return {
        "view": "governance",
        "runtime_agents": _safe(runtime_agents_summary, None),
        "registered_agents": _safe(_registered_agents, None),
        "governance_actions_30d": _safe(gov_actions, None),
        "decision_explorer": {"status": "not_instrumented",
                              "reason": "decision confidence/evidence stream pending (EVT-1)"},
    }


def audit_explorer(*, days: int = 7, limit: int = 50, action: str | None = None) -> dict:
    """Audit view: recent entries + rollups."""
    def recent():
        from execution.ops_platform import audit_log
        rows = audit_log.list_entries(days=days, action=action, limit=limit)
        return [
            {"timestamp": r.get("timestamp"),
             "actor": (r.get("actor") or {}).get("name"),
             "action": r.get("action"),
             "entity_type": r.get("entity_type"),
             "entity_id": r.get("entity_id"),
             "correlation_id": r.get("correlation_id")}
            for r in rows
        ]
    return {
        "view": "audit",
        "stats": _safe(lambda: _audit_stats(days), None),
        "recent": _safe(recent, []),
    }


def snapshot() -> dict:
    """Everything, for the JSON twin / smoke checks."""
    return {
        "generated_for": "trust_command_center_v1",
        "executive": overview(),
        "operations": operations(),
        "governance": governance(),
        "audit": audit_explorer(),
    }
