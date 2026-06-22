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
        "cost_7d": _safe(cost_summary, {"status": "unavailable"}),
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
            "cost": _safe(cost_summary, {}),
            "availability": _safe(availability, {}),
            "lexicon": _safe(lexicon_summary, {}),
        },
    }


def page_data() -> dict:
    """Everything the HTML page needs for first paint."""
    return {
        "overview": overview(),
        "layers": layers(),
        "controls": controls_state(),
        "availability": availability(),
        "lexicon": _safe(lexicon_summary, {}),
    }


# ── Drill-downs (read-only detail, all _safe-wrapped) ──

# Audit actions that "flow through" a given layer (for the layer drill-down).
_LAYER_AUDIT_ACTIONS = {
    5: ("approval.requested", "approval.approved", "approval.rejected",
        "enforcement.denied", "controls.frozen", "controls.unfrozen",
        "controls.quarantined", "controls.rollback", "agent.paused",
        "agent.resumed", "runtime_agent.paused", "runtime_agent.resumed",
        "runtime.global_paused", "runtime.global_resumed"),
    6: ("tbi.evaluated", "trust.calculated"),
}

_AGENT_HEARTBEAT = {
    "cb_mention_responder": "output/ops/_cb_mentions/heartbeat.json",
    "autopickup_worker": "output/ops/_autopickup/heartbeat.json",
}


def _row(r: dict) -> dict:
    return {"timestamp": r.get("timestamp"), "actor": (r.get("actor") or {}).get("name"),
            "action": r.get("action"), "entity_type": r.get("entity_type"),
            "entity_id": r.get("entity_id"), "correlation_id": r.get("correlation_id")}


def _recent_audit(*, actions=None, entity_id=None, days=21, limit=15) -> list:
    from execution.ops_platform import audit_log
    rows: list = []
    if actions:
        for a in actions:
            rows.extend(audit_log.list_entries(action=a, days=days, limit=limit))
        rows.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    else:
        rows = audit_log.list_entries(entity_id=entity_id, days=days, limit=limit)
    return [_row(r) for r in rows[:limit]]


def _layer7_runs() -> list:
    from execution.ops_platform import workflow_runner
    runs = workflow_runner.list_runs(limit=15)
    return [{"run_id": getattr(r, "run_id", None), "capability_id": getattr(r, "capability_id", None),
             "status": getattr(r, "status", None), "started_at": getattr(r, "started_at", None),
             "duration_ms": getattr(r, "duration_ms", None)} for r in runs]


def layer_detail(n) -> dict:
    n = int(n)
    by_num = {L["layer"]: L for L in layers()["layers"]}
    base = by_num.get(n)
    if not base:
        return {"error": f"unknown layer {n}"}
    out = {"layer": n, "name": base["name"], "tag": base["tag"], "members": base["members"],
           "tech": base["tech"], "reference": base.get("reference", []), "metric": base["metric"]}
    if n == 7:
        out["recent_events"] = _safe(_layer7_runs, [])
        out["events_kind"] = "workflow_runs"
    else:
        out["recent_events"] = _safe(
            lambda: _recent_audit(actions=_LAYER_AUDIT_ACTIONS.get(n), days=21, limit=15), [])
        out["events_kind"] = "audit"
    return out


def _agent_attestation(decl) -> dict | None:
    if not decl or not decl.get("entrypoint"):
        return None
    from execution.ops_platform import tbi_compliance
    att = json.loads((PROJECT_ROOT / (decl["entrypoint"] + ".tbi.json")).read_text(encoding="utf-8"))
    v = tbi_compliance.evaluate_attestation(att)
    return {"verdict": v.verdict, "risk_level": att.get("risk_level"),
            "inpact_satisfied": v.inpact_satisfied, "goals_satisfied": v.goals_satisfied,
            "framework_version": v.framework_version}


def _agent_heartbeat(agent_id) -> dict | None:
    rel = _AGENT_HEARTBEAT.get(agent_id)
    if not rel:
        return None
    data = json.loads((PROJECT_ROOT / rel).read_text(encoding="utf-8"))
    keep = ("started_at", "finished_at", "status", "reason", "total_responded",
            "total_mentions_found", "total_failed", "drafted", "skipped")
    return {k: data.get(k) for k in keep if k in data}


def agent_detail(agent_id: str) -> dict:
    from execution.ops_platform import runtime_agents, runtime_controls
    decl = next((d for d in _safe(runtime_agents.load_declarations, []) if d.get("id") == agent_id), None)
    state = _safe(runtime_controls.get_state, {}) or {}
    cell = (state.get("agents", {}) or {}).get(agent_id) or {}
    return {
        "agent_id": agent_id,
        "declared": decl is not None,
        "name": (decl or {}).get("name"),
        "autonomy_policy": (decl or {}).get("autonomy_policy"),
        "entrypoint": (decl or {}).get("entrypoint"),
        "status": (decl or {}).get("status"),
        "rollback_plan": (decl or {}).get("rollback_plan"),
        "notes": (decl or {}).get("notes"),
        "paused": bool(cell.get("paused")) or bool(state.get("global_paused")),
        "global_paused": bool(state.get("global_paused")),
        "pause_meta": cell or None,
        "attestation": _safe(lambda: _agent_attestation(decl), None),
        "trust": _safe(lambda: runtime_trust(agent_id, (decl or {}).get("name")), None),
        "heartbeat": _safe(lambda: _agent_heartbeat(agent_id), None),
        "recent_audit": _safe(lambda: _recent_audit(entity_id=agent_id, days=30, limit=20), []),
    }


def compliance_detail() -> dict:
    from execution.ops_platform import tbi_compliance
    items, counts = [], {"compliant": 0, "conditional": 0, "non_compliant": 0}
    for path in sorted(PROJECT_ROOT.rglob("*.tbi.json")):
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        if rel.startswith(".claude/") or "/.claude/" in f"/{rel}":
            continue
        try:
            att = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        v = tbi_compliance.evaluate_attestation(att)
        counts[v.verdict] = counts.get(v.verdict, 0) + 1
        items.append({
            "artifact_id": v.artifact_id, "kind": att.get("artifact_kind"), "path": rel,
            "verdict": v.verdict, "risk_level": att.get("risk_level"),
            "inpact_satisfied": v.inpact_satisfied, "goals_satisfied": v.goals_satisfied,
            "layers": [l.get("layer") for l in att.get("layers", [])],
            "approver": (att.get("approver") or {}).get("name"), "notes": att.get("notes"),
        })
    _risk_rank = {"CRITICAL": 0, "HIGH": 1, "MODERATE": 2, "LOW": 3, None: 4}
    items.sort(key=lambda x: (x["verdict"] != "non_compliant", _risk_rank.get(x["risk_level"], 4),
                              x["artifact_id"]))
    return {"framework_version": tbi_compliance.CURRENT_FRAMEWORK_VERSION,
            "total": len(items), "counts": counts, "items": items}


def audit_detail(*, days: int = 7, action=None, actor=None, entity_id=None, limit: int = 100) -> dict:
    from execution.ops_platform import audit_log
    rows = _safe(lambda: audit_log.list_entries(days=days, action=action, actor_name=actor,
                                                entity_id=entity_id, limit=limit), [])
    return {"filters": {"days": days, "action": action, "actor": actor, "entity_id": entity_id},
            "stats": _safe(lambda: audit_log.stats(days=days), {}),
            "recent": [_row(r) for r in (rows or [])]}


def audit_replay(correlation_id: str) -> dict:
    from execution.ops_platform import audit_log
    rows = _safe(lambda: audit_log.replay(correlation_id, days=90), [])
    chain = [_row(r) for r in (rows or [])]
    chain.sort(key=lambda r: r.get("timestamp") or "")
    return {"correlation_id": correlation_id, "chain": chain}


# ── Cost explorer (from the forward cost ledger) ──


def cost_summary() -> dict:
    from execution.ops_platform import cost_ledger
    s7 = cost_ledger.summary(7)
    s1 = cost_ledger.summary(1)
    return {
        "usd_7d": s7["total_usd"], "calls_7d": s7["calls"], "tokens_7d": s7["total_tokens"],
        "usd_24h": s1["total_usd"], "calls_24h": s1["calls"],
        "by_model": s7["by_model"], "by_source": s7["by_source"],
        "instrumented_since": s7["instrumented_since"],
    }


def cost_detail() -> dict:
    from execution.ops_platform import cost_ledger
    return cost_ledger.summary(30, recent=50)


# ── Lexicon enforcement signal (GOALS-Lexicon) ──
# Live drift/forbidden scan of the AI fleet against the canonical glossary
# (config/lexicon.json). Makes "Lexicon: satisfied" a real signal, not prose.


def lexicon_summary() -> dict:
    """Glossary stats + a live forbidden/drift scan over the AI artifacts."""
    from execution.ops_platform import lexicon
    return lexicon.summary()


def lexicon_detail() -> dict:
    """Full glossary + every live violation, for the Lexicon drill-down."""
    from execution.ops_platform import lexicon
    scan = _safe(lexicon.scan_artifacts, {}) or {}
    violations = [v for vs in scan.values() for v in vs]
    return {
        "summary": _safe(lexicon.summary, {}),
        "terms": _safe(lexicon.canonical_terms, []),
        "forbidden": _safe(lexicon.forbidden_terms, []),
        "violations": violations,
    }


# ── Availability / health signal (GOALS-Availability, INPACT-Instant) ──

# Stale threshold per scheduled agent (generous multiple of its cadence).
_EXPECTED_MAX_AGE_SEC = {
    "cb_mention_responder": 25 * 60,    # 10-min poll
    "autopickup_worker": 35 * 60,       # 15-min interval (when enabled)
    "productivity_report": 36 * 3600,   # daily report
}


def _age_seconds(iso_ts):
    if not iso_ts:
        return None
    from datetime import datetime, timezone
    try:
        ts = datetime.fromisoformat(str(iso_ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - ts).total_seconds())


def _status_for(age, expected):
    if age is None:
        return "unknown"
    if age <= expected:
        return "healthy"
    if age <= expected * 4:
        return "stale"
    return "down"


def _productivity_last_age():
    import time
    d = PROJECT_ROOT / "output" / "ops" / "_productivity"
    files = list(d.glob("*.json")) if d.exists() else []
    if not files:
        return None
    return max(0.0, time.time() - max(f.stat().st_mtime for f in files))


def _agent_health(agent_id: str, name: str | None) -> dict:
    import os
    name = name or agent_id
    if agent_id == "autopickup_worker" and \
            os.environ.get("OPS_AUTOPICKUP_ENABLED", "false").strip().lower() != "true":
        return {"id": agent_id, "name": name, "status": "disabled",
                "detail": "OPS_AUTOPICKUP_ENABLED not set"}
    if agent_id == "advisory_pipeline":
        return {"id": agent_id, "name": name, "status": "on_demand",
                "detail": "request-driven; no heartbeat"}
    expected = _EXPECTED_MAX_AGE_SEC.get(agent_id, 30 * 60)
    last = None
    if agent_id == "productivity_report":
        age = _safe(_productivity_last_age, None)
    else:
        hb = _safe(lambda: _agent_heartbeat(agent_id), None) or {}
        last = hb.get("finished_at") or hb.get("started_at")
        age = _age_seconds(last)
    status = _status_for(age, expected)
    return {"id": agent_id, "name": name, "status": status,
            "age_seconds": round(age) if age is not None else None,
            "expected_max_age_seconds": expected, "last_run": last,
            "detail": "up to date" if status == "healthy"
                      else ("never run yet" if status == "unknown" else "overdue")}


def _app_health_brief() -> dict:
    from execution.ops_platform import telemetry
    h = telemetry.health_summary().to_dict()
    return {"runs_24h": h.get("total_runs_24h"),
            "failure_rate_24h_pct": h.get("failure_rate_24h_pct"),
            "generated_at": h.get("generated_at")}


def availability() -> dict:
    """Per-agent health from heartbeats + app health → an overall availability %.
    Monitored = agents with a heartbeat-based status (excludes disabled/on_demand/
    not-yet-run). Addresses the GOALS-Availability / INPACT-Instant gap."""
    meta = (_safe(runtime_agents_summary, {}) or {}).get("agents", [])
    agents = [_safe(lambda a=a: _agent_health(a.get("id"), a.get("name")),
                    {"id": a.get("id"), "name": a.get("name"), "status": "unknown"})
              for a in meta]
    monitored = [a for a in agents if a.get("status") in ("healthy", "stale", "down")]
    healthy = sum(1 for a in monitored if a.get("status") == "healthy")
    overall = round(100 * healthy / len(monitored)) if monitored else None
    return {"overall_pct": overall, "monitored": len(monitored), "healthy": healthy,
            "agents": agents, "app": _safe(_app_health_brief, {})}


# ── Runtime trust score (reputation -> attestation wiring) ──
# A per-runtime-agent trust score derived from REAL operational signals (the
# runtime agents aren't ops_platform capabilities, so trust_engine/reputation_
# scorer don't apply directly — this is the equivalent for them).

_TRUST_WEIGHTS = {"availability": 0.30, "reliability": 0.30, "governance": 0.20, "compliance": 0.20}


def runtime_trust(agent_id: str, name: str | None = None) -> dict:
    from execution.ops_platform import audit_log, runtime_agents
    decl = next((d for d in _safe(runtime_agents.load_declarations, []) if d.get("id") == agent_id), None)
    name = name or (decl or {}).get("name") or agent_id
    comps: dict = {}

    # Availability (from the health signal)
    st = (_safe(lambda: _agent_health(agent_id, name), {}) or {}).get("status")
    comps["availability"] = {"healthy": 1.0, "stale": 0.5, "down": 0.0,
                             "on_demand": 0.85, "disabled": 0.85, "unknown": 0.6}.get(st, 0.6)

    # Reliability (from heartbeat success counts where available)
    hb = _safe(lambda: _agent_heartbeat(agent_id), None) or {}
    resp, fail = hb.get("total_responded"), hb.get("total_failed")
    if isinstance(resp, (int, float)) or isinstance(fail, (int, float)):
        tot = (resp or 0) + (fail or 0)
        comps["reliability"] = ((resp or 0) / tot) if tot else 1.0
    else:
        comps["reliability"] = 1.0 if st in ("healthy", "on_demand", "disabled") else 0.6

    # Governance (pauses / enforcement denials over 30d — fewer is better)
    rows = _safe(lambda: audit_log.list_entries(entity_id=agent_id, days=30, limit=100), []) or []
    incidents = sum(1 for r in rows
                    if r.get("action") in ("runtime_agent.paused", "agent.paused", "enforcement.denied"))
    comps["governance"] = max(0.0, 1.0 - 0.15 * incidents)

    # Compliance (attestation verdict)
    att = _safe(lambda: _agent_attestation(decl), None) or {}
    comps["compliance"] = {"compliant": 1.0, "conditional": 0.8,
                           "non_compliant": 0.2}.get(att.get("verdict"), 0.6)

    score = round(100 * sum(comps[k] * _TRUST_WEIGHTS[k] for k in _TRUST_WEIGHTS), 1)
    band = "STRONG" if score >= 85 else "GOOD" if score >= 70 else "FAIR" if score >= 50 else "WEAK"
    return {"agent_id": agent_id, "name": name, "trust_score": score, "band": band,
            "components": {k: round(v, 3) for k, v in comps.items()},
            "incidents_30d": incidents}
