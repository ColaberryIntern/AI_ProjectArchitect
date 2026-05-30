"""Governance scorecards — per-workspace operational health snapshot.

Reads from approvals + incidents + experiments + controls + signed_audit.
Returns a single dict the dashboard renders. Read-only; no new persistence
beyond optional snapshot files.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution.ops_platform import (
    approvals, audit_log, change_requests, controls, experiments,
    incidents, signed_audit, workspaces,
)

logger = logging.getLogger(__name__)

_SCORECARDS_DIR = OUTPUT_DIR / "ops_platform" / "governance_scorecards"


@dataclass
class Scorecard:
    workspace_id: str
    generated_at: str
    approval_hygiene: dict
    rollback_readiness: dict
    policy_violations: dict
    unresolved_incidents: dict
    experiment_safety: dict
    audit_integrity: dict
    overall_score: float           # 0..100

    def to_dict(self) -> dict:
        return asdict(self)


def build(*, workspace_id: str, lookback_days: int = 30) -> Scorecard:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=lookback_days)

    # ── Approval hygiene ──
    all_appr = approvals.list_requests()
    in_window = [r for r in all_appr
                   if _safe_dt(r.created_at) >= cutoff]
    pending = [r for r in in_window if r.state in ("pending", "in_progress")]
    expired = [r for r in in_window if r.state == "expired"]
    approval_hygiene = {
        "in_window_total": len(in_window),
        "pending_total": len(pending),
        "expired_total": len(expired),
        "expiration_rate_pct": round(len(expired) / len(in_window) * 100, 1) if in_window else 0,
    }

    # ── Rollback readiness ──
    rollback_events = audit_log.list_entries(action="rollback.executed",
                                                 days=lookback_days, limit=200)
    rollback_count = len(rollback_events)
    cr_with_rollback_plan = sum(
        1 for c in change_requests.list_change_requests()
        if _safe_dt(c.created_at) >= cutoff and c.rollback_plan.strip()
    )
    cr_total_in_window = sum(
        1 for c in change_requests.list_change_requests()
        if _safe_dt(c.created_at) >= cutoff
    )
    rollback_readiness = {
        "rollback_executions": rollback_count,
        "change_requests_with_rollback_plan": cr_with_rollback_plan,
        "change_requests_total": cr_total_in_window,
        "rollback_plan_coverage_pct": (round(cr_with_rollback_plan
                                                / cr_total_in_window * 100, 1)
                                          if cr_total_in_window else 100),
    }

    # ── Policy violations ──
    denied_rows = audit_log.list_entries(action="enforcement.denied",
                                             days=lookback_days, limit=2000)
    by_actor = Counter((r.get("actor") or {}).get("name", "anonymous")
                          for r in denied_rows)
    policy_violations = {
        "total_denials": len(denied_rows),
        "top_offenders": dict(by_actor.most_common(5)),
    }

    # ── Unresolved incidents ──
    open_incidents = [i for i in incidents.list_incidents(state="open")]
    mitigating = [i for i in incidents.list_incidents(state="mitigating")]
    unresolved_incidents = {
        "open_count": len(open_incidents),
        "mitigating_count": len(mitigating),
        "high_severity_open": [
            {"incident_id": i.incident_id, "title": i.title}
            for i in open_incidents if i.severity >= 4
        ],
    }

    # ── Experiment safety ──
    running_exp = experiments.list_experiments(state="running")
    risky_exp = []
    from execution.ops_platform import evaluation
    for exp in running_exp:
        ev = evaluation.evaluate_experiment(exp.experiment_id)
        for sig in ev.get("significance", []):
            if sig.get("direction") == "treatment_worse" and sig.get("confidence") in ("99%", "95%"):
                risky_exp.append({
                    "experiment_id": exp.experiment_id, "name": exp.name,
                    "arm_id": sig.get("arm_id"),
                    "confidence": sig.get("confidence"),
                    "z_score": sig.get("z_score"),
                })
    experiment_safety = {
        "running_total": len(running_exp),
        "risky_experiments": risky_exp,
        "risky_count": len(risky_exp),
    }

    # ── Audit integrity ──
    verification = signed_audit.verify_chain(days=lookback_days)
    audit_integrity = verification.to_dict()

    # ── Overall score (deterministic, transparent) ──
    score = 100.0
    score -= min(40, approval_hygiene["expiration_rate_pct"] * 0.5)
    score -= min(20, policy_violations["total_denials"] * 0.5)
    score -= 10 if unresolved_incidents["open_count"] >= 3 else 0
    score -= 20 if not audit_integrity["valid"] else 0
    score -= 10 * len(risky_exp)
    overall = max(0.0, round(score, 1))

    card = Scorecard(
        workspace_id=workspace_id, generated_at=now.isoformat(),
        approval_hygiene=approval_hygiene,
        rollback_readiness=rollback_readiness,
        policy_violations=policy_violations,
        unresolved_incidents=unresolved_incidents,
        experiment_safety=experiment_safety,
        audit_integrity=audit_integrity,
        overall_score=overall,
    )
    return card


def build_all(*, lookback_days: int = 30) -> list[Scorecard]:
    out: list[Scorecard] = []
    ws_list = workspaces.list_workspaces() if hasattr(workspaces, "list_workspaces") else []
    if not ws_list:
        out.append(build(workspace_id="global", lookback_days=lookback_days))
        return out
    for ws in ws_list:
        out.append(build(workspace_id=ws.workspace_id, lookback_days=lookback_days))
    return out


def persist(card: Scorecard) -> None:
    _SCORECARDS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (_SCORECARDS_DIR / f"{card.workspace_id}_{stamp}.json").write_text(
        json.dumps(card.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _safe_dt(s: str) -> datetime:
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)
