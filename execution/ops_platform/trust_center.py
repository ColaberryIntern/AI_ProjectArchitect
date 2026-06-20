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


# ── 7-Layer Architecture of Trust (with live metrics) ──

# Canonical layers (directives/compliance/trust-before-intelligence.md) mapped to
# their member modules (execution/ops_platform/__layers__.py). Bottom (L1) -> top (L7).
_LAYER_DEFS = [
    {"layer": 1, "name": "Multi-Modal Storage", "tag": "foundation",
     "members": ["session_store", "audit_log", "retention_policy"],
     "tech": ["JSON/JSONL (output/)", "Redis (optional)", "filesystem", "SQL Server (CCPP, ext)"]},
    {"layer": 2, "name": "Real-Time Data Fabric", "tag": "Instant",
     "members": ["event_fabric", "realtime_bus", "cache_bus", "redis_backends"],
     "tech": ["event_fabric", "Redis Streams", "SSE", "APScheduler"]},
    {"layer": 3, "name": "Unified Semantic Layer", "tag": "Natural",
     "members": ["semantic_analyzer", "search_index", "knowledge_graph"],
     "tech": ["OpenAI gpt-4o-mini", "keyword search_index", "knowledge_graph"]},
    {"layer": 4, "name": "Intelligent Retrieval (RAG)", "tag": "Contextual",
     "members": ["scoped_memory", "organizational_memory", "feedback_store"],
     "tech": ["keyword/TF retrieval", "file-backed memory", "feedback_store", "(no vector DB yet)"]},
    {"layer": 5, "name": "Agent-Aware Governance", "tag": "Permitted",
     "members": ["rbac", "enforcement", "controls", "agent_registry", "approvals"],
     "tech": ["RBAC", "controls", "approvals", "Google OAuth SSO", "AES-GCM vault", "TBI gate"]},
    {"layer": 6, "name": "Observability & Feedback", "tag": "Transparent",
     "members": ["audit_log", "telemetry", "prometheus_exporter", "reliability_monitor"],
     "tech": ["audit_log (JSONL)", "Prometheus", "telemetry", "heartbeats"]},
    {"layer": 7, "name": "Multi-Agent Orchestration", "tag": "orchestration",
     "members": ["workflow_runner", "pipeline_engine", "orchestration_engine", "agent_runtime"],
     "tech": ["FastAPI", "workflow_runner", "pipeline_engine", "OpenAI", "Docker/uvicorn"]},
]

# Reference stack from the Trust-Before-Intelligence book (Echo Health example),
# shown alongside our actual stack for contrast. Source: book repo codex.
_REFERENCE_TECH = {
    1: ["Databricks / Snowflake"], 2: ["Redis", "Trino", "Kafka"], 3: ["dbt"],
    4: ["Neo4j (knowledge graph)"], 5: ["Collibra (governance)"],
    6: ["observability suite"], 7: ["OpenAI + Anthropic (LLM/agents)"],
}

# Map a tech string in our stack -> the catalogued vendor whose framework
# INPACT/GOALS score applies (scores in config/tbi_stack_scores.json, scraped from
# trustbeforeintelligence.ai/tech-stack). Only items we genuinely run on a
# catalogued tool get a score; custom/internal modules stay unscored.
_TECH_VENDOR = {
    "Redis (optional)": "Redis",
    "Redis Streams": "Redis",
    "OpenAI gpt-4o-mini": "GPT-4o",
    "OpenAI": "GPT-4o",
    "Prometheus": "Prometheus",
}

_STACK_SCORES_PATH = PROJECT_ROOT / "config" / "tbi_stack_scores.json"
_STACK_SCORES_CACHE: dict | None = None


def _stack_scores() -> dict:
    global _STACK_SCORES_CACHE
    if _STACK_SCORES_CACHE is None:
        try:
            _STACK_SCORES_CACHE = json.loads(
                _STACK_SCORES_PATH.read_text(encoding="utf-8")).get("scores", {})
        except (OSError, json.JSONDecodeError):
            _STACK_SCORES_CACHE = {}
    return _STACK_SCORES_CACHE


def _score_tech(items) -> list:
    """Turn our per-layer tech strings into objects, annotated with the
    framework INPACT/GOALS score where the tech maps to a catalogued vendor."""
    scores = _stack_scores()
    out = []
    for t in items:
        vendor = _TECH_VENDOR.get(t)
        s = scores.get(vendor) if vendor else None
        out.append({"name": t, "vendor": vendor,
                    "inpact": (s or {}).get("inpact"), "goals": (s or {}).get("goals")})
    return out


def _signals() -> dict:
    """Fetch the shared live signals once (defensive)."""
    def _audit(days):
        from execution.ops_platform import audit_log
        return audit_log.stats(days=days)

    def _health():
        from execution.ops_platform import telemetry
        return telemetry.health_summary().to_dict()

    def _pending():
        from execution.ops_platform import approvals
        return len(approvals.list_requests(state="pending"))

    audit7 = _safe(lambda: _audit(7), {})
    audit1 = _safe(lambda: _audit(1), {})
    audit30 = _safe(lambda: _audit(30), {})
    health = _safe(_health, {})
    by_action30 = audit30.get("by_action", {}) if isinstance(audit30, dict) else {}
    return {
        "audit7_total": (audit7 or {}).get("total", 0) if isinstance(audit7, dict) else 0,
        "audit1_total": (audit1 or {}).get("total", 0) if isinstance(audit1, dict) else 0,
        "runs_24h": (health or {}).get("total_runs_24h", 0) if isinstance(health, dict) else 0,
        "fail_pct": (health or {}).get("failure_rate_24h_pct", 0) if isinstance(health, dict) else 0,
        "capabilities": (health or {}).get("capability_count", 0) if isinstance(health, dict) else 0,
        "approvals_pending": _safe(_pending, 0) if isinstance(_safe(_pending, 0), int) else 0,
        "denials_30d": by_action30.get("enforcement.denied", 0),
        "runtime_agents": len(_safe(lambda: runtime_agents_summary().get("agents", []), [])),
    }


def layers() -> dict:
    """The 7 trust layers, each with a live metric for the animated diagram."""
    s = _signals()
    metric_for = {
        1: ("audit records / 7d", s["audit7_total"], "ok"),
        2: ("runs / 24h", s["runs_24h"], "ok"),
        3: ("capabilities", s["capabilities"], "ok"),
        4: ("grounded agents", s["runtime_agents"], "ok"),
        5: (f"{s['approvals_pending']} pending · {s['denials_30d']} denials",
            s["approvals_pending"], "warn" if s["approvals_pending"] else "ok"),
        6: ("audit events / 24h", s["audit1_total"], "ok"),
        7: (f"{s['runs_24h']} runs · {s['fail_pct']}% fail", s["runs_24h"],
            "warn" if (isinstance(s["fail_pct"], (int, float)) and s["fail_pct"] >= 20) else "ok"),
    }
    out = []
    for d in _LAYER_DEFS:
        label, value, status = metric_for[d["layer"]]
        out.append({**d, "tech": _score_tech(d["tech"]),
                    "reference": _REFERENCE_TECH.get(d["layer"], []),
                    "metric": {"label": label, "value": value, "status": status}})
    return {"layers": out, "signals": s}


# ── Controls state (drives the levers) ──


def controls_state() -> dict:
    def _runtime():
        from execution.ops_platform import runtime_controls
        return runtime_controls.get_state()

    def _active_controls():
        from execution.ops_platform import controls
        return [c.to_dict() for c in controls.list_active()]

    def _pending_approvals():
        from execution.ops_platform import approvals
        rows = approvals.list_requests(state="pending")
        return [
            {"request_id": getattr(r, "request_id", None),
             "action": getattr(r, "action", None),
             "entity_type": getattr(r, "entity_type", None),
             "entity_id": getattr(r, "entity_id", None),
             "state": getattr(r, "state", None),
             "created_at": getattr(r, "created_at", None)}
            for r in rows
        ]

    return {
        "runtime": _safe(_runtime, None),
        "active_controls": _safe(_active_controls, []),
        "pending_approvals": _safe(_pending_approvals, []),
        "runtime_agents": _safe(runtime_agents_summary, None),
    }


# ── Compact live payload for the dashboard poller ──


def live() -> dict:
    comp = _safe(tbi_compliance_summary, None)
    counts = comp.get("counts", {}) if isinstance(comp, dict) else {}
    return {
        "layers": layers()["layers"],
        "controls": controls_state(),
        "counters": {
            "compliance": counts,
            "audit_24h": _signals()["audit1_total"],
        },
    }


def page_data() -> dict:
    """Everything the HTML page needs for first paint."""
    return {
        "overview": overview(),
        "layers": layers(),
        "controls": controls_state(),
    }
