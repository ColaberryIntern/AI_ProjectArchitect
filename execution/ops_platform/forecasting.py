"""Forecasting + drift detection — predict operational risk from existing
time-series in audit + run history + queue metrics.

Scope honesty
-------------
- Simple statistical methods (rolling mean, linear regression, EMA). No
  scikit-learn / numpy dependency.
- Predictions carry a ``confidence`` field that reflects sample size. Small
  N yields low confidence — the forecaster never claims more than the data
  supports.
- ``capacity_recommendations()`` suggests worker / queue / experiment
  changes; the suggestions are recommendations, never auto-applied.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from execution.ops_platform import (
    audit_log, incidents, reliability_monitor, runtime_queue, telemetry,
    workflow_runner,
)
from execution.ops_platform.capability_registry import CapabilityRegistry, default_registry

logger = logging.getLogger(__name__)


@dataclass
class Forecast:
    metric: str
    horizon_minutes: int
    predicted_value: float | None
    confidence: float
    method: str
    based_on_samples: int
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CapacityRecommendation:
    kind: str                      # scale_workers | stagger_schedules | throttle_experiments
    detail: str
    confidence: float
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ── Forecasts ─────────────────────────────────────────────────────────


def forecast_queue_saturation(*, horizon_minutes: int = 30) -> Forecast:
    """Project queue depth horizon_minutes ahead from the recent growth rate."""
    samples = _queue_depth_samples(lookback_minutes=60)
    if len(samples) < 5:
        return Forecast(metric="queue.depth", horizon_minutes=horizon_minutes,
                          predicted_value=None, confidence=0.0,
                          method="linear_regression",
                          based_on_samples=len(samples),
                          notes="not enough samples for a forecast")
    slope, intercept = _linear_fit(samples)
    minutes_from_first = (datetime.now(timezone.utc)
                            - samples[0][0]).total_seconds() / 60
    projected_x = minutes_from_first + horizon_minutes
    projected = max(0, slope * projected_x + intercept)
    confidence = min(1.0, len(samples) / 30)
    return Forecast(metric="queue.depth", horizon_minutes=horizon_minutes,
                      predicted_value=round(projected, 1), confidence=round(confidence, 2),
                      method="linear_regression",
                      based_on_samples=len(samples))


def forecast_incident_probability(*, horizon_hours: int = 6) -> Forecast:
    """Use reliability_monitor findings + open incidents as risk indicators."""
    findings = reliability_monitor.scan()
    open_incidents = incidents.list_incidents(state="open")
    high_severity = sum(1 for f in findings if f.severity >= 3)
    open_high = sum(1 for i in open_incidents if i.severity >= 3)
    # Naive logistic-style probability
    raw = 1 / (1 + math.exp(-(high_severity * 0.4 + open_high * 0.6 - 1.5)))
    return Forecast(
        metric="incident.probability_high_severity",
        horizon_minutes=horizon_hours * 60,
        predicted_value=round(raw, 3),
        confidence=0.7 if high_severity + open_high >= 2 else 0.3,
        method="logistic_signal_blend",
        based_on_samples=high_severity + open_high,
        notes=("high probability — investigate findings"
                 if raw >= 0.5 else "low predicted incident risk"),
    )


def forecast_worker_exhaustion(*, registry: CapabilityRegistry | None = None) -> Forecast:
    """Estimate when active workers will be saturated based on queue lag."""
    from execution.ops_platform import worker_coordination
    active_workers = max(1, len([w for w in worker_coordination.list_workers()
                                     if w.status == "active"]))
    queue_depth = runtime_queue.queue_depth().get("total", 0)
    samples_count = 10
    runs = workflow_runner.list_runs(limit=100)
    mean_duration_seconds = 5.0
    durations = [r.duration_ms for r in runs if r.duration_ms]
    if durations:
        mean_duration_seconds = sum(durations) / len(durations) / 1000
    seconds_per_worker = mean_duration_seconds
    minutes_to_drain = (queue_depth * seconds_per_worker / 60) / active_workers if active_workers else float("inf")
    return Forecast(
        metric="worker.drain_minutes", horizon_minutes=60,
        predicted_value=round(minutes_to_drain, 1),
        confidence=0.6 if durations else 0.3,
        method="queue_lag_estimate",
        based_on_samples=len(durations),
    )


def forecast_alert_storm() -> Forecast:
    """Use recent alert frequency to project storm probability."""
    from execution.ops_platform import alerts
    rows = audit_log.list_entries(action="alert.opened", days=1, limit=500)
    last_hour_count = 0
    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    for r in rows:
        try:
            ts = datetime.fromisoformat(r.get("timestamp", ""))
        except (TypeError, ValueError):
            continue
        if ts >= cutoff:
            last_hour_count += 1
    # Heuristic: >10 alerts/hr = storm risk
    p = min(1.0, last_hour_count / 20)
    return Forecast(
        metric="alert.storm_probability", horizon_minutes=60,
        predicted_value=round(p, 3), confidence=0.7 if last_hour_count >= 5 else 0.3,
        method="hourly_count", based_on_samples=last_hour_count,
    )


def capacity_recommendations() -> list[CapacityRecommendation]:
    out: list[CapacityRecommendation] = []
    queue_forecast = forecast_queue_saturation()
    if queue_forecast.predicted_value and queue_forecast.predicted_value > 100:
        out.append(CapacityRecommendation(
            kind="scale_workers",
            detail=f"projected queue depth {queue_forecast.predicted_value} in {queue_forecast.horizon_minutes}m",
            confidence=queue_forecast.confidence,
            evidence=queue_forecast.to_dict(),
        ))
    drain = forecast_worker_exhaustion()
    if drain.predicted_value and drain.predicted_value > 30:
        out.append(CapacityRecommendation(
            kind="scale_workers",
            detail=f"current workers need {drain.predicted_value}min to drain queue",
            confidence=drain.confidence, evidence=drain.to_dict(),
        ))
    storm = forecast_alert_storm()
    if storm.predicted_value and storm.predicted_value > 0.5:
        out.append(CapacityRecommendation(
            kind="throttle_experiments",
            detail="alert storm risk elevated — pause non-essential experiments",
            confidence=storm.confidence, evidence=storm.to_dict(),
        ))
    return out


# ── Drift detection ───────────────────────────────────────────────────


def detect_routing_drift(*, lookback_hours: int = 6) -> list[dict]:
    """Compare each capability's routing distribution between this window
    and the prior window of equal size."""
    rows = audit_log.list_entries(action="routing.selected", days=2, limit=10000)
    now = datetime.now(timezone.utc)
    recent_cutoff = now - timedelta(hours=lookback_hours)
    prior_cutoff = now - timedelta(hours=lookback_hours * 2)

    recent: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    prior: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in rows:
        try:
            ts = datetime.fromisoformat(r.get("timestamp", ""))
        except (TypeError, ValueError):
            continue
        cap = r.get("entity_id", "unknown")
        meta = r.get("metadata") or {}
        semver = meta.get("selected_semver") or "fallback"
        if ts >= recent_cutoff:
            recent[cap][semver] += 1
        elif prior_cutoff <= ts < recent_cutoff:
            prior[cap][semver] += 1

    out = []
    for cap in set(list(recent.keys()) + list(prior.keys())):
        recent_total = sum(recent[cap].values()) or 1
        prior_total = sum(prior[cap].values()) or 1
        recent_dist = {k: v / recent_total for k, v in recent[cap].items()}
        prior_dist = {k: v / prior_total for k, v in prior[cap].items()}
        max_shift = 0.0
        shifted_semver = None
        for k in set(list(recent_dist.keys()) + list(prior_dist.keys())):
            shift = abs(recent_dist.get(k, 0) - prior_dist.get(k, 0))
            if shift > max_shift:
                max_shift = shift
                shifted_semver = k
        if max_shift >= 0.15:
            out.append({
                "capability_id": cap,
                "max_shift": round(max_shift, 3),
                "shifted_semver": shifted_semver,
                "recent_distribution": recent_dist,
                "prior_distribution": prior_dist,
            })
    return out


def detect_latency_drift(*, registry: CapabilityRegistry | None = None) -> list[dict]:
    """Reuses reliability_monitor's latency_regression detector but exposes
    its findings as drift entries."""
    findings = reliability_monitor.scan(registry=registry or default_registry())
    return [f.to_dict() for f in findings if f.kind == "latency_regression"]


def detect_approval_bottlenecks(*, age_hours: int = 8) -> list[dict]:
    from execution.ops_platform import approvals as appr_mod
    cutoff = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    pending = appr_mod.list_requests(state="pending") + appr_mod.list_requests(state="in_progress")
    out = []
    for r in pending:
        try:
            created = datetime.fromisoformat(r.created_at)
        except ValueError:
            continue
        if created < cutoff:
            out.append({"request_id": r.request_id, "action": r.action,
                         "entity_type": r.entity_type, "entity_id": r.entity_id,
                         "age_hours": round((datetime.now(timezone.utc) - created).total_seconds() / 3600, 1)})
    return out


# ── Internal ───────────────────────────────────────────────────────────


def _queue_depth_samples(*, lookback_minutes: int) -> list:
    """Reconstruct queue depth over time from audit rows.

    Counts queue.enqueued − queue.acked − queue.cancelled − queue.nacked
    within a sliding window.
    """
    rows = audit_log.list_entries(days=1, limit=10000,
                                     action=None)
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)
    series = []
    for r in rows:
        try:
            ts = datetime.fromisoformat(r.get("timestamp", ""))
        except (TypeError, ValueError):
            continue
        if ts < cutoff:
            continue
        action = r.get("action", "")
        if action == "queue.enqueued":
            series.append((ts, +1))
        elif action in ("queue.acked", "queue.cancelled", "queue.nacked"):
            series.append((ts, -1))
    if not series:
        return []
    series.sort()
    depth = 0
    samples = []
    for ts, delta in series:
        depth = max(0, depth + delta)
        samples.append((ts, depth))
    return samples


def _linear_fit(samples) -> tuple[float, float]:
    """Simple least-squares fit on (x_minutes_from_first, y)."""
    if len(samples) < 2:
        return (0.0, 0.0)
    t0 = samples[0][0]
    xs = [(s[0] - t0).total_seconds() / 60 for s in samples]
    ys = [s[1] for s in samples]
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    den = sum((xs[i] - mean_x) ** 2 for i in range(n))
    if den == 0:
        return (0.0, mean_y)
    slope = num / den
    intercept = mean_y - slope * mean_x
    return (slope, intercept)
