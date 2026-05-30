"""Reliability monitor — detects operational degradation from existing signals.

Detectors (cheap reads, no LLM):
  - rising_failure_rate    (failure rate spike vs prior window)
  - timeout_spike          (errors mentioning timeout/timed out)
  - latency_regression     (p95 increased vs prior window by Δ)
  - routing_degradation    (experimental version reliability << approved)
  - retry_storm            (claim → nack → re-claim loops on the same job)
  - queue_starvation       (pending jobs older than threshold)
  - dead_workers           (worker registry rows past heartbeat TTL)
  - stale_caches           (cache topic mtime > threshold)
  - runaway_token_usage    (mean prompt_tokens climbing per capability)

This module is read-only. ``self_healing`` reads from here and may take
reversible action; everything autonomous is opt-in via the self-healing
policy.
"""

from __future__ import annotations

import logging
import statistics
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from execution.ops_platform import (
    cache_bus, capability_versions, runtime_queue, telemetry,
    worker_coordination, workflow_runner,
)
from execution.ops_platform.capability_registry import CapabilityRegistry, default_registry

logger = logging.getLogger(__name__)


@dataclass
class Finding:
    kind: str
    target_type: str
    target_id: str
    severity: int                # 1..5
    detail: str
    evidence: dict = field(default_factory=dict)
    suggested_action: str = ""
    confidence: float = 0.5

    def to_dict(self) -> dict:
        return asdict(self)


def scan(*, registry: CapabilityRegistry | None = None) -> list[Finding]:
    reg = registry or default_registry()
    findings: list[Finding] = []
    findings.extend(_rising_failure_rate(reg))
    findings.extend(_timeout_spike(reg))
    findings.extend(_latency_regression(reg))
    findings.extend(_routing_degradation(reg))
    findings.extend(_retry_storm())
    findings.extend(_queue_starvation())
    findings.extend(_dead_workers())
    findings.extend(_stale_caches())
    findings.extend(_runaway_token_usage(reg))
    findings.sort(key=lambda f: (f.severity, f.confidence), reverse=True)
    return findings


# ── Detectors ──────────────────────────────────────────────────────────


def _rising_failure_rate(reg: CapabilityRegistry, *, lookback_hours: int = 1) -> list[Finding]:
    runs = workflow_runner.list_runs(limit=2000)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=lookback_hours)
    prior_cutoff = now - timedelta(hours=lookback_hours * 2)
    out: list[Finding] = []
    by_id = reg.snapshot().by_id()
    grouped: dict[str, list] = defaultdict(list)
    for r in runs:
        grouped[r.capability_id].append(r)
    for cid, rs in grouped.items():
        recent = [r for r in rs if _safe_ts(r.started_at) >= cutoff]
        prior = [r for r in rs if prior_cutoff <= _safe_ts(r.started_at) < cutoff]
        if len(recent) < 3 or len(prior) < 3:
            continue
        recent_fail = sum(1 for r in recent if r.status in ("error", "contract_failed")) / len(recent)
        prior_fail = sum(1 for r in prior if r.status in ("error", "contract_failed")) / len(prior)
        delta = recent_fail - prior_fail
        if delta >= 0.20:
            out.append(Finding(
                kind="rising_failure_rate", target_type="capability",
                target_id=cid, severity=4,
                detail=f"failure rate jumped from {prior_fail*100:.0f}% to {recent_fail*100:.0f}%",
                evidence={"recent_runs": len(recent), "prior_runs": len(prior),
                          "delta_pct": round(delta * 100, 1)},
                suggested_action="quarantine or rollback to prior approved version",
                confidence=min(1.0, len(recent) / 25),
            ))
    return out


def _timeout_spike(reg: CapabilityRegistry, *, threshold: int = 5,
                     lookback_hours: int = 1) -> list[Finding]:
    runs = workflow_runner.list_runs(limit=2000)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    counts: Counter = Counter()
    for r in runs:
        if _safe_ts(r.started_at) < cutoff:
            continue
        msg = (r.error_message or "").lower()
        if r.status in ("error", "contract_failed") and ("timeout" in msg or "timed out" in msg):
            counts[r.capability_id] += 1
    out: list[Finding] = []
    for cid, n in counts.most_common():
        if n < threshold:
            continue
        out.append(Finding(
            kind="timeout_spike", target_type="capability",
            target_id=cid, severity=3, confidence=0.8,
            detail=f"{n} timeout-related failures in the last {lookback_hours}h",
            evidence={"timeout_count": n},
            suggested_action="increase max_tokens / reduce input size / quarantine",
        ))
    return out


def _latency_regression(reg: CapabilityRegistry) -> list[Finding]:
    runs = workflow_runner.list_runs(limit=2000)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=1)
    prior_cutoff = now - timedelta(hours=2)
    by_cap: dict[str, dict[str, list[int]]] = defaultdict(lambda: {"recent": [], "prior": []})
    for r in runs:
        if r.status != "succeeded" or not r.duration_ms:
            continue
        ts = _safe_ts(r.started_at)
        if ts >= cutoff:
            by_cap[r.capability_id]["recent"].append(r.duration_ms)
        elif prior_cutoff <= ts < cutoff:
            by_cap[r.capability_id]["prior"].append(r.duration_ms)
    out: list[Finding] = []
    for cid, buckets in by_cap.items():
        if len(buckets["recent"]) < 5 or len(buckets["prior"]) < 5:
            continue
        recent_p95 = sorted(buckets["recent"])[max(0, int(len(buckets["recent"]) * 0.95) - 1)]
        prior_p95 = sorted(buckets["prior"])[max(0, int(len(buckets["prior"]) * 0.95) - 1)]
        if recent_p95 > prior_p95 * 2 and recent_p95 > 3000:
            out.append(Finding(
                kind="latency_regression", target_type="capability",
                target_id=cid, severity=2, confidence=0.7,
                detail=f"p95 jumped from {prior_p95}ms to {recent_p95}ms",
                evidence={"prior_p95_ms": prior_p95, "recent_p95_ms": recent_p95},
                suggested_action="investigate input size or model swap",
            ))
    return out


def _routing_degradation(reg: CapabilityRegistry, *,
                           min_runs_per_version: int = 5) -> list[Finding]:
    runs = workflow_runner.list_runs(limit=2000)
    out: list[Finding] = []
    by_cap_version: dict[tuple[str, str], list] = defaultdict(list)
    for r in runs:
        vid = (r.inputs.get("__capability_version_id") if isinstance(r.inputs, dict) else None)
        if not vid:
            continue
        by_cap_version[(r.capability_id, vid)].append(r)
    # Compare experimental reliability against approved
    for cap in reg.snapshot().capabilities:
        versions = capability_versions.list_versions(cap["id"])
        approved = next((v for v in versions if v.status == "approved"), None)
        if approved is None:
            continue
        approved_runs = by_cap_version.get((cap["id"], approved.version_id), [])
        if len(approved_runs) < min_runs_per_version:
            continue
        approved_rel = sum(1 for r in approved_runs if r.status == "succeeded") / len(approved_runs)
        for v in versions:
            if v.status != "experimental":
                continue
            exp_runs = by_cap_version.get((cap["id"], v.version_id), [])
            if len(exp_runs) < min_runs_per_version:
                continue
            exp_rel = sum(1 for r in exp_runs if r.status == "succeeded") / len(exp_runs)
            if approved_rel - exp_rel >= 0.20:
                out.append(Finding(
                    kind="routing_degradation", target_type="capability_version",
                    target_id=v.version_id, severity=4, confidence=0.85,
                    detail=(f"experimental {v.semver} reliability "
                            f"{exp_rel*100:.0f}% << approved {approved_rel*100:.0f}%"),
                    evidence={"experimental_runs": len(exp_runs),
                              "approved_runs": len(approved_runs),
                              "delta_pct": round((approved_rel - exp_rel) * 100, 1)},
                    suggested_action="halt experimental rollout; rollback recommended",
                ))
    return out


def _retry_storm() -> list[Finding]:
    out: list[Finding] = []
    for job in runtime_queue.list_jobs(limit=500):
        if job.attempts >= max(3, job.max_attempts) and job.status in ("pending", "claimed", "dead_letter"):
            out.append(Finding(
                kind="retry_storm", target_type="job",
                target_id=job.job_id, severity=3, confidence=0.9,
                detail=f"job exhausted retries (attempts={job.attempts})",
                evidence={"attempts": job.attempts, "status": job.status,
                          "last_error": (job.last_error or "")[:160]},
                suggested_action="inspect last_error; consider dead-letter triage",
            ))
    return out


def _queue_starvation(*, age_minutes: int = 30) -> list[Finding]:
    out: list[Finding] = []
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    for job in runtime_queue.list_jobs(status="pending", limit=200):
        try:
            ts = datetime.fromisoformat(job.enqueued_at)
        except ValueError:
            continue
        if ts < cutoff:
            out.append(Finding(
                kind="queue_starvation", target_type="job",
                target_id=job.job_id, severity=2, confidence=0.7,
                detail=f"pending job older than {age_minutes}min",
                evidence={"enqueued_at": job.enqueued_at, "kind": job.kind},
                suggested_action="check worker_coordination.list_workers — workers may be down",
            ))
    return out


def _dead_workers() -> list[Finding]:
    out: list[Finding] = []
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=worker_coordination.DEFAULT_HEARTBEAT_TTL_SECONDS)
    for w in worker_coordination.list_workers():
        try:
            hb = datetime.fromisoformat(w.last_heartbeat_at)
        except ValueError:
            continue
        if hb < cutoff and w.status != "stopped":
            out.append(Finding(
                kind="dead_worker", target_type="worker",
                target_id=w.worker_id, severity=2, confidence=0.95,
                detail="worker missed heartbeat TTL",
                evidence={"last_heartbeat_at": w.last_heartbeat_at, "status": w.status},
                suggested_action="evict via worker_coordination.evict_stale()",
            ))
    return out


def _stale_caches(*, max_age_seconds: int = 7200) -> list[Finding]:
    out: list[Finding] = []
    freshness = telemetry.cache_freshness_seconds()
    for topic, age in freshness.items():
        if age is None or age <= max_age_seconds:
            continue
        out.append(Finding(
            kind="stale_cache", target_type="cache_topic",
            target_id=topic, severity=1, confidence=0.8,
            detail=f"no version bump for {age:.0f}s",
            evidence={"age_seconds": age},
            suggested_action="confirm corresponding writer is alive; cache may be in disuse",
        ))
    return out


def _runaway_token_usage(reg: CapabilityRegistry) -> list[Finding]:
    out: list[Finding] = []
    rows = telemetry.token_usage(registry=reg)
    for row in rows:
        if row["mean_prompt_tokens"] > 4000 and row["samples"] >= 5:
            out.append(Finding(
                kind="runaway_token_usage", target_type="capability",
                target_id=row["capability_id"], severity=2, confidence=0.85,
                detail=f"mean prompt_tokens={row['mean_prompt_tokens']} on {row['samples']} samples",
                evidence=row,
                suggested_action="trim system prompt or capability description",
            ))
    return out


# ── Internal ───────────────────────────────────────────────────────────


def _safe_ts(s: str) -> datetime:
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)
