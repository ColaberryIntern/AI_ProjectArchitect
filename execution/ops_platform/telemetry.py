"""Telemetry — operational visibility into the platform itself.

Read-only roll-ups that answer: is the platform healthy, fast, fresh?

Surfaces:
  - latency_stats        — per-capability mean / p95 / p99 / max
  - failure_trace        — last N failed runs with capability + message
  - cache_freshness      — last bump time per cache_bus topic + computed lag
  - recommendation_freshness — when did the graph cache last rebuild
  - token_usage          — per-capability LLM token consumption
  - dependency_health    — MCP servers / agents referenced but never used
  - executions_heatmap   — runs per hour-of-day grid for the last N days

Persistence: none. This module is pure read. Snapshots go to
``output/ops_platform/telemetry/{stamp}.json`` only when ``snapshot()``
is explicitly called.
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
    cache_bus,
    pipeline_engine,
    recommendation_engine,
    workflow_runner,
)
from execution.ops_platform.capability_registry import CapabilityRegistry, default_registry

logger = logging.getLogger(__name__)

_TELEMETRY_DIR = OUTPUT_DIR / "ops_platform" / "telemetry"


@dataclass
class HealthSummary:
    generated_at: str
    capability_count: int
    total_runs_24h: int
    failure_rate_24h_pct: float
    slowest_capabilities: list = field(default_factory=list)
    stale_caches: list = field(default_factory=list)
    cache_freshness_seconds: dict = field(default_factory=dict)
    recent_failures: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ── Public API ─────────────────────────────────────────────────────────


def health_summary(*, registry: CapabilityRegistry | None = None) -> HealthSummary:
    reg = registry or default_registry()
    snap = reg.snapshot()
    runs = workflow_runner.list_runs(limit=3000)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)
    last_24 = [r for r in runs if _safe_ts(r.started_at) >= cutoff]
    fail = sum(1 for r in last_24 if r.status in ("error", "contract_failed"))
    fail_pct = round(fail / len(last_24) * 100, 1) if last_24 else 0.0
    return HealthSummary(
        generated_at=now.isoformat(),
        capability_count=len(snap.capabilities),
        total_runs_24h=len(last_24),
        failure_rate_24h_pct=fail_pct,
        slowest_capabilities=latency_stats(registry=reg)[:5],
        stale_caches=_stale_caches(now=now, max_age_seconds=3600),
        cache_freshness_seconds=cache_freshness_seconds(now=now),
        recent_failures=failure_trace(limit=10),
    )


def latency_stats(*, registry: CapabilityRegistry | None = None) -> list[dict]:
    reg = registry or default_registry()
    by_id = reg.snapshot().by_id()
    runs = workflow_runner.list_runs(limit=5000)
    per_cap: dict[str, list[int]] = defaultdict(list)
    for r in runs:
        if r.status == "succeeded" and r.duration_ms is not None:
            per_cap[r.capability_id].append(r.duration_ms)
    rows: list[dict] = []
    for cid, ds in per_cap.items():
        cap = by_id.get(cid)
        if not cap or not ds:
            continue
        ds_sorted = sorted(ds)
        mean = sum(ds) / len(ds)
        p95 = ds_sorted[max(0, int(len(ds_sorted) * 0.95) - 1)]
        p99 = ds_sorted[max(0, int(len(ds_sorted) * 0.99) - 1)]
        rows.append({
            "capability_id": cid, "name": cap.get("name", cid),
            "samples": len(ds), "mean_ms": round(mean, 0),
            "p95_ms": p95, "p99_ms": p99, "max_ms": max(ds),
        })
    rows.sort(key=lambda r: r["p99_ms"], reverse=True)
    return rows


def failure_trace(*, limit: int = 25) -> list[dict]:
    runs = workflow_runner.list_runs(limit=200)
    out: list[dict] = []
    for r in runs:
        if r.status in ("error", "contract_failed", "llm_unavailable"):
            out.append({
                "run_id": r.run_id,
                "capability_id": r.capability_id,
                "status": r.status,
                "error_message": (r.error_message or "")[:200],
                "started_at": r.started_at,
            })
        if len(out) >= limit:
            break
    return out


def cache_freshness_seconds(*, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    out: dict = {}
    for topic in cache_bus.Topic:
        v = cache_bus.current_version(topic)
        if v == 0.0:
            out[topic.value] = None
        else:
            out[topic.value] = round(now.timestamp() - v, 1)
    return out


def recommendation_freshness() -> dict:
    """Reports the graph cache versions captured at last build vs current."""
    try:
        from execution.ops_platform import recommendation_engine as re_mod
        last = dict(re_mod._GRAPH_CACHE_VERSIONS)
    except Exception:
        last = {}
    current = {t.value: cache_bus.current_version(t) for t in cache_bus.Topic}
    return {"captured_at_build": last, "current": current, "stale": current != last}


def token_usage(*, registry: CapabilityRegistry | None = None) -> list[dict]:
    reg = registry or default_registry()
    by_id = reg.snapshot().by_id()
    runs = workflow_runner.list_runs(limit=5000)
    per_cap: dict[str, list[int]] = defaultdict(list)
    for r in runs:
        if isinstance(r.llm_usage, dict):
            t = r.llm_usage.get("prompt_tokens", 0) or 0
            if t:
                per_cap[r.capability_id].append(t)
    rows: list[dict] = []
    for cid, ts in per_cap.items():
        cap = by_id.get(cid)
        if not cap or not ts:
            continue
        rows.append({
            "capability_id": cid, "name": cap.get("name", cid),
            "samples": len(ts),
            "mean_prompt_tokens": round(sum(ts) / len(ts), 1),
            "total_prompt_tokens": sum(ts),
        })
    rows.sort(key=lambda r: r["total_prompt_tokens"], reverse=True)
    return rows


def dependency_health(*, registry: CapabilityRegistry | None = None) -> dict:
    """Find MCP servers / agents that are listed by capabilities but were never
    actually used (no co_occurs evidence in the operational graph)."""
    reg = registry or default_registry()
    declared_mcp: Counter = Counter()
    declared_agents: Counter = Counter()
    for cap in reg.snapshot().capabilities:
        for m in cap.get("mcp_servers_used") or []:
            declared_mcp[m] += 1
        for a in cap.get("agents_used") or []:
            declared_agents[a] += 1
    # Used dependencies = those whose nodes appear in the operational graph.
    g = recommendation_engine._cached_graph()
    used_mcp = set()
    used_agents = set()
    for key in g.nodes:
        ntype, nid = key.split(":", 1)
        if ntype == "mcp_server":
            used_mcp.add(nid)
        elif ntype == "agent":
            used_agents.add(nid)
    return {
        "declared_mcp": dict(declared_mcp),
        "declared_agents": dict(declared_agents),
        "unused_mcp": sorted(set(declared_mcp) - used_mcp),
        "unused_agents": sorted(set(declared_agents) - used_agents),
    }


def executions_heatmap(*, days: int = 14) -> dict:
    """Returns a 2D heatmap: rows=date (newest at top), cols=hour-of-day (0-23)."""
    runs = workflow_runner.list_runs(limit=10000)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    grid: dict[str, list[int]] = {}
    for r in runs:
        ts = _safe_ts(r.started_at)
        if ts < cutoff:
            continue
        day = ts.date().isoformat()
        if day not in grid:
            grid[day] = [0] * 24
        grid[day][ts.hour] += 1
    return {
        "rows": sorted(grid.keys(), reverse=True),
        "grid": grid,
    }


def snapshot(*, registry: CapabilityRegistry | None = None) -> Path:
    """Persist a full telemetry snapshot to disk."""
    reg = registry or default_registry()
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "health": health_summary(registry=reg).to_dict(),
        "latency": latency_stats(registry=reg),
        "token_usage": token_usage(registry=reg),
        "dependency_health": dependency_health(registry=reg),
        "executions_heatmap": executions_heatmap(),
    }
    _TELEMETRY_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = _TELEMETRY_DIR / f"{stamp}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


# ── Internal ───────────────────────────────────────────────────────────


def _stale_caches(*, now: datetime, max_age_seconds: int) -> list[str]:
    stale: list[str] = []
    for topic in cache_bus.Topic:
        v = cache_bus.current_version(topic)
        if v == 0.0:
            continue
        age = now.timestamp() - v
        if age > max_age_seconds:
            stale.append(topic.value)
    return stale


def _safe_ts(s: str) -> datetime:
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)
