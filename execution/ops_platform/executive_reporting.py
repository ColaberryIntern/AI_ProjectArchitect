"""Executive reporting — high-level operational summaries an exec can scan
in two minutes.

Two surfaces:
  - ``executive_scorecard()``      — single dict for the home/exec page
  - ``monthly_report(year, month)``— bucketed view per calendar month, persisted
                                     so historical exec reports survive

This module is read-only over existing data: runs, feedback, capabilities,
reputation, training, pipelines. No new persistence beyond the
``output/ops_platform/reporting/{YYYY-MM}.json`` snapshot file.
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution.ops_platform import (
    analytics,
    feedback_store,
    organizational_memory,
    pipeline_engine,
    reputation_scorer,
    training_agent,
    workflow_runner,
)
from execution.ops_platform.capability_registry import CapabilityRegistry, default_registry

logger = logging.getLogger(__name__)

_REPORTING_DIR = OUTPUT_DIR / "ops_platform" / "reporting"

DEFAULT_DOLLARS_PER_HOUR = 60.0


@dataclass
class ExecutiveScorecard:
    generated_at: str
    capability_count: int
    pipeline_count: int
    department_count: int
    total_runs_30d: int
    successful_runs_30d: int
    reliability_pct_30d: float
    total_hours_saved_30d: float
    estimated_dollars_saved_30d: float
    distinct_initiators_30d: int
    average_feedback_30d: float | None
    feedback_records_30d: int
    new_capabilities_30d: int
    walkthroughs_published: int
    top_value_capabilities: list = field(default_factory=list)
    department_adoption: list = field(default_factory=list)
    failure_reduction_pct_vs_prior_month: float | None = None
    workflow_count_growth_pct: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


# ── Public API ─────────────────────────────────────────────────────────


def executive_scorecard(
    *,
    dollars_per_hour: float = DEFAULT_DOLLARS_PER_HOUR,
    registry: CapabilityRegistry | None = None,
) -> ExecutiveScorecard:
    reg = registry or default_registry()
    snap = reg.snapshot()
    now = datetime.now(timezone.utc)
    cutoff_30 = now - timedelta(days=30)
    cutoff_60 = now - timedelta(days=60)

    runs = workflow_runner.list_runs(limit=10000)
    runs_30 = [r for r in runs if _started_after(r, cutoff_30)]
    runs_30_60 = [r for r in runs if (not _started_after(r, cutoff_30) and _started_after(r, cutoff_60))]

    successful_30 = sum(1 for r in runs_30 if r.status == "succeeded")
    failures_30 = sum(1 for r in runs_30 if r.status in ("error", "contract_failed"))
    failures_30_60 = sum(1 for r in runs_30_60 if r.status in ("error", "contract_failed"))
    failure_reduction = None
    if failures_30_60:
        failure_reduction = round((failures_30_60 - failures_30) / failures_30_60 * 100, 1)

    minutes_saved_30 = 0.0
    by_id = snap.by_id()
    for r in runs_30:
        if r.status != "succeeded":
            continue
        cap = by_id.get(r.capability_id)
        if not cap:
            continue
        minutes_saved_30 += (cap.get("estimated_time_savings") or {}).get("minutes_per_run", 0) or 0

    initiators_30 = {
        (r.inputs.get("__initiator") if isinstance(r.inputs, dict) else None) or "anonymous"
        for r in runs_30
    }

    # Feedback last 30d
    aggregates = feedback_store.all_aggregates()
    recent_avgs: list[float] = []
    feedback_count = 0
    for _, agg in aggregates.items():
        last_sub = agg.get("last_submission")
        avg = agg.get("overall_average")
        if not last_sub or avg is None:
            continue
        try:
            ts = datetime.fromisoformat(last_sub)
        except (TypeError, ValueError):
            continue
        if ts >= cutoff_30:
            recent_avgs.append(avg)
            feedback_count += 1

    walkthroughs = len(training_agent.list_walkthroughs()) if hasattr(training_agent, "list_walkthroughs") else 0
    pipeline_count = len(pipeline_engine.list_pipelines())

    # Top value capabilities = top-N by (succeeded_runs * minutes_per_run)
    top_value: list[dict] = []
    per_cap_runs: dict[str, int] = defaultdict(int)
    for r in runs_30:
        if r.status == "succeeded":
            per_cap_runs[r.capability_id] += 1
    rows = []
    for cid, succ in per_cap_runs.items():
        cap = by_id.get(cid)
        if not cap:
            continue
        mpr = (cap.get("estimated_time_savings") or {}).get("minutes_per_run", 0) or 0
        rows.append({"capability_id": cid, "name": cap.get("name", cid),
                     "category": cap.get("category"),
                     "succeeded_runs": succ, "hours_saved": round(succ * mpr / 60, 1)})
    rows.sort(key=lambda r: r["hours_saved"], reverse=True)
    top_value = rows[:8]

    dept_curve = analytics.department_usage(registry=reg)
    workflow_count_growth = None
    return ExecutiveScorecard(
        generated_at=now.isoformat(),
        capability_count=len(snap.capabilities),
        pipeline_count=pipeline_count,
        department_count=len(snap.departments()),
        total_runs_30d=len(runs_30),
        successful_runs_30d=successful_30,
        reliability_pct_30d=round(successful_30 / len(runs_30) * 100, 1) if runs_30 else 0.0,
        total_hours_saved_30d=round(minutes_saved_30 / 60, 1),
        estimated_dollars_saved_30d=round(minutes_saved_30 / 60 * dollars_per_hour, 2),
        distinct_initiators_30d=len(initiators_30),
        average_feedback_30d=round(sum(recent_avgs) / len(recent_avgs), 2) if recent_avgs else None,
        feedback_records_30d=feedback_count,
        new_capabilities_30d=0,
        walkthroughs_published=walkthroughs,
        top_value_capabilities=top_value,
        department_adoption=dept_curve,
        failure_reduction_pct_vs_prior_month=failure_reduction,
        workflow_count_growth_pct=workflow_count_growth,
    )


def monthly_report(year: int, month: int, *, dollars_per_hour: float = DEFAULT_DOLLARS_PER_HOUR,
                    persist: bool = True, registry: CapabilityRegistry | None = None) -> dict:
    """Bucketed roll-up for the given (year, month). Idempotent — re-runnable."""
    reg = registry or default_registry()
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(year, month + 1, 1, tzinfo=timezone.utc)

    runs = workflow_runner.list_runs(limit=20000)
    monthly_runs = [r for r in runs if start <= _safe_ts(r.started_at) < end]
    succ = sum(1 for r in monthly_runs if r.status == "succeeded")
    by_id = reg.snapshot().by_id()
    minutes_saved = 0.0
    for r in monthly_runs:
        if r.status != "succeeded":
            continue
        cap = by_id.get(r.capability_id)
        if cap:
            minutes_saved += (cap.get("estimated_time_savings") or {}).get("minutes_per_run", 0) or 0
    initiators = {
        (r.inputs.get("__initiator") if isinstance(r.inputs, dict) else None) or "anonymous"
        for r in monthly_runs
    }
    payload = {
        "year": year, "month": month,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_runs": len(monthly_runs),
        "successful_runs": succ,
        "reliability_pct": round(succ / len(monthly_runs) * 100, 1) if monthly_runs else 0.0,
        "hours_saved": round(minutes_saved / 60, 1),
        "dollars_saved": round(minutes_saved / 60 * dollars_per_hour, 2),
        "distinct_initiators": len(initiators),
    }
    if persist:
        _REPORTING_DIR.mkdir(parents=True, exist_ok=True)
        path = _REPORTING_DIR / f"{year}-{month:02d}.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def list_monthly_reports() -> list[dict]:
    if not _REPORTING_DIR.exists():
        return []
    out: list[dict] = []
    for path in sorted(_REPORTING_DIR.glob("*.json")):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return out


# ── Internal ───────────────────────────────────────────────────────────


def _started_after(run, cutoff: datetime) -> bool:
    return _safe_ts(run.started_at) >= cutoff


def _safe_ts(s: str) -> datetime:
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)
