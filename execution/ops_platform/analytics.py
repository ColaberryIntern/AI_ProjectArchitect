"""Analytics — read-only roll-ups for the operations dashboard.

What this exposes (each cheap to compute, no LLM, no I/O beyond reading
persisted JSON):

  - top_capabilities()         most-used / highest-reputation / fastest-growing
  - department_usage()         runs + reputation + adoption by category
  - automation_roi()           per-capability and aggregate time-savings
  - bottlenecks()              capabilities with high contract_failed / error rate
  - training_gaps()            popular capabilities with no walkthrough generated
  - feedback_pulse()           7- and 30-day rolling rating averages
  - executive_summary()        a single dict the home page can render in one widget

All functions return plain dicts/lists so the router can ship them straight
to a Jinja template or an /api/* JSON endpoint.

There is NO mutation, NO scheduled job. Refresh happens on read. Each call
typically completes in <50ms even with ~5K runs.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from execution.ops_platform import (
    feedback_store,
    pipeline_engine,
    reputation_scorer,
    training_agent,
    workflow_runner,
)
from execution.ops_platform.capability_registry import CapabilityRegistry, default_registry

logger = logging.getLogger(__name__)

# Rolling windows for trend computation
RECENT_WINDOW = timedelta(days=7)
PRIOR_WINDOW = timedelta(days=14)  # the 7 days before RECENT_WINDOW
LONG_WINDOW = timedelta(days=30)

# Default cost-of-manual-time assumption — operator can override per-org later.
DEFAULT_DOLLARS_PER_HOUR = 60.0


# ── Public API ─────────────────────────────────────────────────────────


def top_capabilities(
    *,
    by: str = "usage",
    top_n: int = 10,
    registry: CapabilityRegistry | None = None,
) -> list[dict]:
    """Return top capabilities ranked by ``by``.

    by ∈ {usage, reputation, growth}
    """
    reg = registry or default_registry()
    caps = reg.snapshot().capabilities

    if by == "reputation":
        scores = []
        for c in caps:
            score = reputation_scorer.load_score(c["id"])
            rep = (score or {}).get("reputation_score", 0)
            scores.append({"capability_id": c["id"], "name": c.get("name", c["id"]),
                           "type": c.get("type"), "score": round(rep, 2)})
        scores.sort(key=lambda s: s["score"], reverse=True)
        return scores[:top_n]

    if by == "growth":
        return _growth_ranking(caps, top_n=top_n)

    # default = usage
    items = [{
        "capability_id": c["id"], "name": c.get("name", c["id"]),
        "type": c.get("type"), "usage_count": c.get("usage_count", 0),
    } for c in caps]
    items.sort(key=lambda x: x["usage_count"], reverse=True)
    return items[:top_n]


def department_usage(
    *,
    registry: CapabilityRegistry | None = None,
) -> list[dict]:
    """Aggregate runs + reputation per department (= capability.category)."""
    reg = registry or default_registry()
    snap = reg.snapshot()
    runs = workflow_runner.list_runs(limit=5000)

    by_id = snap.by_id()
    dept_caps: dict[str, list[dict]] = defaultdict(list)
    for c in snap.capabilities:
        dept_caps[c.get("category", "Uncategorized")].append(c)

    dept_runs: dict[str, int] = defaultdict(int)
    dept_successes: dict[str, int] = defaultdict(int)
    for r in runs:
        cap = by_id.get(r.capability_id)
        if not cap:
            continue
        dept = cap.get("category", "Uncategorized")
        dept_runs[dept] += 1
        if r.status == "succeeded":
            dept_successes[dept] += 1

    out: list[dict] = []
    for dept, members in dept_caps.items():
        rep_scores = []
        for c in members:
            score = reputation_scorer.load_score(c["id"])
            if score:
                rep_scores.append(score.get("reputation_score", 0))
        out.append({
            "department": dept,
            "capability_count": len(members),
            "total_runs": dept_runs.get(dept, 0),
            "succeeded_runs": dept_successes.get(dept, 0),
            "reliability_pct": round(
                (dept_successes.get(dept, 0) / dept_runs[dept] * 100) if dept_runs.get(dept) else 0.0, 1
            ),
            "average_reputation": round(sum(rep_scores) / len(rep_scores), 2) if rep_scores else 0.0,
        })
    out.sort(key=lambda d: d["total_runs"], reverse=True)
    return out


def automation_roi(
    *,
    dollars_per_hour: float = DEFAULT_DOLLARS_PER_HOUR,
    registry: CapabilityRegistry | None = None,
) -> dict:
    """Compute estimated time + dollar savings from successful runs.

    Per-capability estimated_time_savings.minutes_per_run × successful_runs.
    Aggregate roll-up is the sum across capabilities.
    """
    reg = registry or default_registry()
    snap = reg.snapshot()
    by_id = snap.by_id()
    runs = workflow_runner.list_runs(limit=10000)

    successes_per_cap: Counter[str] = Counter()
    for r in runs:
        if r.status == "succeeded":
            successes_per_cap[r.capability_id] += 1

    per_capability: list[dict] = []
    total_minutes = 0.0
    for cid, succ in successes_per_cap.items():
        cap = by_id.get(cid)
        if not cap:
            continue
        mpr = (cap.get("estimated_time_savings") or {}).get("minutes_per_run", 0) or 0
        minutes = succ * mpr
        per_capability.append({
            "capability_id": cid,
            "name": cap.get("name", cid),
            "succeeded_runs": succ,
            "minutes_per_run": mpr,
            "total_minutes_saved": round(minutes, 1),
            "estimated_dollars_saved": round(minutes / 60 * dollars_per_hour, 2),
        })
        total_minutes += minutes

    per_capability.sort(key=lambda r: r["total_minutes_saved"], reverse=True)
    return {
        "dollars_per_hour": dollars_per_hour,
        "total_minutes_saved": round(total_minutes, 1),
        "total_hours_saved": round(total_minutes / 60, 1),
        "estimated_dollars_saved": round(total_minutes / 60 * dollars_per_hour, 2),
        "per_capability": per_capability,
    }


def bottlenecks(
    *,
    min_runs: int = 5,
    registry: CapabilityRegistry | None = None,
) -> list[dict]:
    """Capabilities with high failure or contract_failed rates.

    Skips capabilities with fewer than ``min_runs`` (statistically too noisy).
    """
    reg = registry or default_registry()
    snap = reg.snapshot()
    by_id = snap.by_id()
    runs = workflow_runner.list_runs(limit=5000)

    grouped: dict[str, list] = defaultdict(list)
    for r in runs:
        grouped[r.capability_id].append(r)

    rows: list[dict] = []
    for cid, group in grouped.items():
        if len(group) < min_runs:
            continue
        cap = by_id.get(cid)
        if not cap:
            continue
        statuses = Counter(r.status for r in group)
        failures = statuses.get("error", 0) + statuses.get("contract_failed", 0)
        failure_rate = failures / len(group)
        if failure_rate <= 0:
            continue
        rows.append({
            "capability_id": cid,
            "name": cap.get("name", cid),
            "type": cap.get("type"),
            "total_runs": len(group),
            "failures": failures,
            "failure_rate_pct": round(failure_rate * 100, 1),
            "by_status": dict(statuses),
        })
    rows.sort(key=lambda r: r["failure_rate_pct"], reverse=True)
    return rows


def training_gaps(
    *,
    min_usage: int = 5,
    registry: CapabilityRegistry | None = None,
) -> list[dict]:
    """Popular capabilities (used ≥ min_usage times) without a published
    walkthrough. The training_agent surfaces what we should generate next."""
    reg = registry or default_registry()
    gaps: list[dict] = []
    for cap in reg.snapshot().capabilities:
        usage = cap.get("usage_count", 0)
        if usage < min_usage:
            continue
        has_walkthrough = training_agent.has_walkthrough(cap["id"])
        if has_walkthrough:
            continue
        gaps.append({
            "capability_id": cap["id"],
            "name": cap.get("name", cap["id"]),
            "type": cap.get("type"),
            "usage_count": usage,
            "category": cap.get("category"),
        })
    gaps.sort(key=lambda g: g["usage_count"], reverse=True)
    return gaps


def feedback_pulse() -> dict:
    """Rolling rating averages over 7d and 30d, plus a delta vs prior week."""
    aggregates = feedback_store.all_aggregates()
    now = datetime.now(timezone.utc)

    last_7 = []
    last_30 = []
    prior_7 = []
    for cap_id, agg in aggregates.items():
        avg = agg.get("overall_average")
        last_sub = agg.get("last_submission")
        if avg is None or not last_sub:
            continue
        try:
            ts = datetime.fromisoformat(last_sub)
        except (TypeError, ValueError):
            continue
        delta = now - ts
        if delta <= RECENT_WINDOW:
            last_7.append(avg)
        if delta <= LONG_WINDOW:
            last_30.append(avg)
        if RECENT_WINDOW < delta <= PRIOR_WINDOW:
            prior_7.append(avg)

    def _avg(values: list[float]) -> float | None:
        return round(sum(values) / len(values), 2) if values else None

    avg_7 = _avg(last_7)
    avg_prior = _avg(prior_7)
    return {
        "last_7d_average": avg_7,
        "last_30d_average": _avg(last_30),
        "prior_7d_average": avg_prior,
        "delta_vs_prior": round(avg_7 - avg_prior, 2) if (avg_7 is not None and avg_prior is not None) else None,
        "feedback_count_7d": len(last_7),
        "feedback_count_30d": len(last_30),
    }


def pipeline_health(
    *,
    min_runs: int = 1,
) -> list[dict]:
    """Per-pipeline success rate over recent pipeline runs."""
    runs = pipeline_engine.list_pipeline_runs(limit=1000)
    grouped: dict[str, list] = defaultdict(list)
    for r in runs:
        grouped[r.pipeline_id].append(r)

    out: list[dict] = []
    for pipeline_id, group in grouped.items():
        if len(group) < min_runs:
            continue
        succ = sum(1 for r in group if r.status == "succeeded")
        out.append({
            "pipeline_id": pipeline_id,
            "total_runs": len(group),
            "succeeded": succ,
            "success_rate_pct": round(succ / len(group) * 100, 1),
        })
    out.sort(key=lambda r: r["total_runs"], reverse=True)
    return out


def executive_summary(
    *,
    dollars_per_hour: float = DEFAULT_DOLLARS_PER_HOUR,
    registry: CapabilityRegistry | None = None,
) -> dict:
    """One-shot snapshot the home page hero widget renders."""
    reg = registry or default_registry()
    snap = reg.snapshot()
    runs = workflow_runner.list_runs(limit=5000)
    roi = automation_roi(dollars_per_hour=dollars_per_hour, registry=reg)
    pulse = feedback_pulse()

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "capability_count": len(snap.capabilities),
        "department_count": len(snap.departments()),
        "total_runs": len(runs),
        "succeeded_runs": sum(1 for r in runs if r.status == "succeeded"),
        "total_hours_saved": roi["total_hours_saved"],
        "estimated_dollars_saved": roi["estimated_dollars_saved"],
        "feedback_last_7d_average": pulse["last_7d_average"],
        "feedback_delta_vs_prior": pulse["delta_vs_prior"],
        "top_capabilities_by_usage": top_capabilities(by="usage", top_n=5, registry=reg),
        "top_capabilities_by_reputation": top_capabilities(by="reputation", top_n=5, registry=reg),
    }


# ── Internal ───────────────────────────────────────────────────────────


def abandonment_analysis(
    *,
    min_runs: int = 3,
    registry: CapabilityRegistry | None = None,
) -> list[dict]:
    """Find capabilities whose users appear to abandon them after first try.

    Heuristic: per-capability, count distinct initiators who ran it once vs
    those who ran it 2+ times. A high one-time ratio signals abandonment.
    """
    reg = registry or default_registry()
    by_id = reg.snapshot().by_id()
    runs = workflow_runner.list_runs(limit=10000)

    per_cap_initiators: dict[str, Counter[str]] = defaultdict(Counter)
    for r in runs:
        if r.status != "succeeded":
            continue
        initiator = (r.inputs.get("__initiator") if isinstance(r.inputs, dict) else None) or "anonymous"
        per_cap_initiators[r.capability_id][initiator] += 1

    out: list[dict] = []
    for cid, counts in per_cap_initiators.items():
        if sum(counts.values()) < min_runs:
            continue
        cap = by_id.get(cid)
        if not cap:
            continue
        one_time = sum(1 for c in counts.values() if c == 1)
        repeaters = sum(1 for c in counts.values() if c >= 2)
        total_users = one_time + repeaters
        abandon_pct = round(one_time / total_users * 100, 1) if total_users else 0.0
        out.append({
            "capability_id": cid,
            "name": cap.get("name", cid),
            "category": cap.get("category"),
            "one_time_users": one_time,
            "repeat_users": repeaters,
            "abandonment_pct": abandon_pct,
        })
    out.sort(key=lambda r: r["abandonment_pct"], reverse=True)
    return out


def duration_analysis(
    *,
    registry: CapabilityRegistry | None = None,
) -> list[dict]:
    """Per-capability mean / p95 duration in ms. Bottleneck detection."""
    reg = registry or default_registry()
    by_id = reg.snapshot().by_id()
    runs = workflow_runner.list_runs(limit=5000)
    per_cap: dict[str, list[int]] = defaultdict(list)
    for r in runs:
        if r.status == "succeeded" and r.duration_ms is not None:
            per_cap[r.capability_id].append(r.duration_ms)
    out: list[dict] = []
    for cid, durations in per_cap.items():
        cap = by_id.get(cid)
        if not cap or not durations:
            continue
        durations_sorted = sorted(durations)
        mean = sum(durations) / len(durations)
        p95_idx = max(0, int(len(durations_sorted) * 0.95) - 1)
        p95 = durations_sorted[p95_idx]
        out.append({
            "capability_id": cid,
            "name": cap.get("name", cid),
            "samples": len(durations),
            "mean_ms": round(mean, 0),
            "p95_ms": p95,
            "max_ms": max(durations),
        })
    out.sort(key=lambda r: r["p95_ms"], reverse=True)
    return out


def roi_trend(
    *,
    bucket_days: int = 7,
    buckets: int = 8,
    dollars_per_hour: float = DEFAULT_DOLLARS_PER_HOUR,
    registry: CapabilityRegistry | None = None,
) -> list[dict]:
    """Bucketed time-savings over rolling windows. Returns oldest-first."""
    reg = registry or default_registry()
    by_id = reg.snapshot().by_id()
    runs = workflow_runner.list_runs(limit=10000)
    now = datetime.now(timezone.utc)
    series: list[dict] = []
    for i in range(buckets - 1, -1, -1):
        end = now - timedelta(days=bucket_days * i)
        start = end - timedelta(days=bucket_days)
        minutes = 0.0
        n_runs = 0
        for r in runs:
            if r.status != "succeeded":
                continue
            try:
                ts = datetime.fromisoformat(r.started_at)
            except (TypeError, ValueError):
                continue
            if not (start < ts <= end):
                continue
            cap = by_id.get(r.capability_id)
            if not cap:
                continue
            mpr = (cap.get("estimated_time_savings") or {}).get("minutes_per_run", 0) or 0
            minutes += mpr
            n_runs += 1
        series.append({
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "succeeded_runs": n_runs,
            "minutes_saved": round(minutes, 1),
            "dollars_saved": round(minutes / 60 * dollars_per_hour, 2),
        })
    return series


def department_adoption_curve(
    *,
    bucket_days: int = 7,
    buckets: int = 8,
    registry: CapabilityRegistry | None = None,
) -> dict:
    """Distinct initiators per department per time bucket — adoption growth."""
    reg = registry or default_registry()
    by_id = reg.snapshot().by_id()
    runs = workflow_runner.list_runs(limit=10000)
    now = datetime.now(timezone.utc)

    # bucket initiators per department
    series: dict[str, list[dict]] = defaultdict(list)
    departments = sorted({c.get("category", "Uncategorized") for c in reg.snapshot().capabilities})
    for i in range(buckets - 1, -1, -1):
        end = now - timedelta(days=bucket_days * i)
        start = end - timedelta(days=bucket_days)
        dept_initiators: dict[str, set[str]] = defaultdict(set)
        for r in runs:
            if r.status != "succeeded":
                continue
            try:
                ts = datetime.fromisoformat(r.started_at)
            except (TypeError, ValueError):
                continue
            if not (start < ts <= end):
                continue
            cap = by_id.get(r.capability_id)
            if not cap:
                continue
            initiator = (r.inputs.get("__initiator") if isinstance(r.inputs, dict) else None) or "anonymous"
            dept_initiators[cap.get("category", "Uncategorized")].add(initiator)
        for dept in departments:
            series[dept].append({
                "window_end": end.isoformat(),
                "distinct_initiators": len(dept_initiators.get(dept, set())),
            })
    return {"departments": departments, "series": dict(series)}


def workflow_dependency_heatmap(
    *,
    registry: CapabilityRegistry | None = None,
) -> dict:
    """Build a matrix of how often capability B is run within 30 min after A
    (the followed_by signal). Returns {nodes, matrix[i][j]=count}."""
    reg = registry or default_registry()
    snap = reg.snapshot()
    # Reuse operational_graph's followed_by computation by reading the
    # built graph (which uses cache_bus, so this stays fresh).
    from execution.ops_platform import recommendation_engine
    g = recommendation_engine._cached_graph()
    nodes = [c["id"] for c in snap.capabilities]
    idx = {cid: i for i, cid in enumerate(nodes)}
    matrix = [[0.0 for _ in nodes] for _ in nodes]
    for edge in g.edges.values():
        if edge.kind != "followed_by":
            continue
        try:
            _, src = edge.src.split(":", 1)
            _, dst = edge.dst.split(":", 1)
        except ValueError:
            continue
        if src in idx and dst in idx:
            matrix[idx[src]][idx[dst]] = edge.weight
    return {
        "nodes": [{"capability_id": cid,
                   "name": snap.by_id().get(cid, {}).get("name", cid),
                   "category": snap.by_id().get(cid, {}).get("category")}
                  for cid in nodes],
        "matrix": matrix,
    }


def training_effectiveness(
    *,
    registry: CapabilityRegistry | None = None,
) -> list[dict]:
    """Compare runs-per-week before vs after a walkthrough was generated.

    Heuristic: if the walkthrough file's mtime is in the past, use it as the
    split point and count successful runs before/after.
    """
    reg = registry or default_registry()
    runs = workflow_runner.list_runs(limit=10000)
    out: list[dict] = []
    for cap in reg.snapshot().capabilities:
        walkthrough = training_agent._TRAINING_DIR / f"{cap['id']}.md"
        if not walkthrough.exists():
            continue
        try:
            mtime = datetime.fromtimestamp(walkthrough.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        before = after = 0
        for r in runs:
            if r.capability_id != cap["id"] or r.status != "succeeded":
                continue
            try:
                ts = datetime.fromisoformat(r.started_at)
            except (TypeError, ValueError):
                continue
            if ts < mtime:
                before += 1
            else:
                after += 1
        if before + after == 0:
            continue
        out.append({
            "capability_id": cap["id"],
            "name": cap.get("name", cap["id"]),
            "walkthrough_generated_at": mtime.isoformat(),
            "runs_before": before,
            "runs_after": after,
            "delta": after - before,
        })
    out.sort(key=lambda r: r["delta"], reverse=True)
    return out


def _growth_ranking(capabilities: list[dict], *, top_n: int) -> list[dict]:
    """Compare last-7d run counts vs prior 7d, per capability. Returns
    capabilities with biggest absolute jump."""
    runs = workflow_runner.list_runs(limit=5000)
    now = datetime.now(timezone.utc)
    counts_recent: Counter[str] = Counter()
    counts_prior: Counter[str] = Counter()

    for r in runs:
        try:
            ts = datetime.fromisoformat(r.started_at)
        except (TypeError, ValueError):
            continue
        delta = now - ts
        if delta <= RECENT_WINDOW:
            counts_recent[r.capability_id] += 1
        elif RECENT_WINDOW < delta <= PRIOR_WINDOW:
            counts_prior[r.capability_id] += 1

    by_id = {c["id"]: c for c in capabilities}
    out: list[dict] = []
    for cid in set(counts_recent) | set(counts_prior):
        cap = by_id.get(cid)
        if not cap:
            continue
        recent = counts_recent.get(cid, 0)
        prior = counts_prior.get(cid, 0)
        out.append({
            "capability_id": cid,
            "name": cap.get("name", cid),
            "type": cap.get("type"),
            "runs_last_7d": recent,
            "runs_prior_7d": prior,
            "delta": recent - prior,
        })
    out.sort(key=lambda r: r["delta"], reverse=True)
    return out[:top_n]
