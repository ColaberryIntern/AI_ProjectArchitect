"""Adoption helpers — trust signals, badges, and skill-level adaptations
that make the platform feel safe to use.

This module is read-only. It synthesizes a per-capability "trust packet"
from existing signals (reputation, runs, feedback, verification, training)
that the UI uses to render badges and contextual guidance.

Surfaces:
  - badges_for(capability_id)        → list of badge dicts {kind, label, tone}
  - mode_for(user_runs_total)        → "beginner" | "intermediate" | "expert"
  - confidence_indicator(capability) → {level: "high"|"medium"|"low", reasons:[]}
  - estimated_completion_time(cap_id)→ {seconds, sample_count, p95_seconds}
  - common_mistakes(cap_id)          → clustered failure messages w/ counts
  - next_action_for(capability_id, role) → "Try it on a real task" / "Submit feedback"
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import asdict, dataclass, field

from execution.ops_platform import (
    feedback_store,
    reputation_scorer,
    training_agent,
    workflow_runner,
)
from execution.ops_platform.capability_registry import CapabilityRegistry, default_registry

logger = logging.getLogger(__name__)


# ── Public API ─────────────────────────────────────────────────────────


def badges_for(capability_id: str) -> list[dict]:
    """Build the badge set the UI renders next to a capability."""
    badges: list[dict] = []
    rep = reputation_scorer.load_score(capability_id) or {}
    score = rep.get("reputation_score", 0)
    runs = workflow_runner.list_runs(capability_id=capability_id, limit=500)
    succ = sum(1 for r in runs if r.status == "succeeded")
    total = len(runs)
    reliability = (succ / total) if total else 0

    if total >= 25 and reliability >= 0.9:
        badges.append({"kind": "production_safe", "label": "Safe for production",
                       "tone": "green",
                       "tooltip": f"{succ}/{total} successful runs ({reliability * 100:.0f}%)"})
    if score >= 70:
        badges.append({"kind": "high_reputation", "label": "High reputation",
                       "tone": "green",
                       "tooltip": f"Reputation score {score:.0f}/100"})
    if training_agent.has_walkthrough(capability_id):
        badges.append({"kind": "walkthrough", "label": "Walkthrough available",
                       "tone": "blue", "tooltip": "Generated training markdown ships with this capability"})

    agg = feedback_store.get_aggregate(capability_id)
    if (agg.get("total_feedback") or 0) >= 5 and (agg.get("overall_average") or 0) >= 4:
        badges.append({"kind": "human_reviewed", "label": "Human reviewed",
                       "tone": "blue",
                       "tooltip": f"{agg['total_feedback']} feedback records, avg {agg['overall_average']}/5"})

    # Trend
    try:
        trend = reputation_scorer.trend(capability_id)
        if trend.get("direction") == "rising":
            badges.append({"kind": "trending_up", "label": "Trending up", "tone": "blue",
                            "tooltip": f"Reputation Δ {trend.get('delta')}"})
    except Exception:
        pass

    if total == 0:
        badges.append({"kind": "new", "label": "New", "tone": "amber",
                       "tooltip": "Not yet run by anyone in this org"})
    if reliability and reliability < 0.6 and total >= 5:
        badges.append({"kind": "needs_attention", "label": "Needs attention",
                       "tone": "red",
                       "tooltip": f"Only {reliability * 100:.0f}% success across {total} runs"})
    return badges


def mode_for(user_runs_total: int) -> str:
    if user_runs_total < 3:
        return "beginner"
    if user_runs_total < 20:
        return "intermediate"
    return "expert"


def confidence_indicator(capability_id: str) -> dict:
    reasons: list[str] = []
    rep = reputation_scorer.load_score(capability_id) or {}
    score = rep.get("reputation_score", 0)
    runs = workflow_runner.list_runs(capability_id=capability_id, limit=500)
    total = len(runs)
    succ = sum(1 for r in runs if r.status == "succeeded")
    reliability = (succ / total) if total else 0
    if total >= 25 and reliability >= 0.9:
        reasons.append(f"{succ}/{total} successful runs across the org")
    if score >= 70:
        reasons.append(f"reputation {score:.0f}/100")
    if score >= 70 and total >= 25 and reliability >= 0.9:
        level = "high"
    elif total >= 5 and reliability >= 0.7:
        level = "medium"
        if reliability < 0.9:
            reasons.append(f"reliability {reliability * 100:.0f}% — small sample")
    else:
        level = "low"
        if total == 0:
            reasons.append("no run history yet")
        if total > 0 and reliability < 0.7:
            reasons.append(f"reliability {reliability * 100:.0f}%")
    return {"level": level, "reasons": reasons}


def estimated_completion_time(capability_id: str) -> dict:
    runs = workflow_runner.list_runs(capability_id=capability_id, limit=200)
    durations = [r.duration_ms for r in runs if r.status == "succeeded" and r.duration_ms]
    if not durations:
        return {"seconds": None, "sample_count": 0, "p95_seconds": None}
    durations_sorted = sorted(durations)
    median = durations_sorted[len(durations_sorted) // 2]
    p95 = durations_sorted[max(0, int(len(durations_sorted) * 0.95) - 1)]
    return {
        "seconds": round(median / 1000, 1),
        "p95_seconds": round(p95 / 1000, 1),
        "sample_count": len(durations),
    }


def common_mistakes(capability_id: str, *, limit: int = 5) -> list[dict]:
    runs = workflow_runner.list_runs(capability_id=capability_id, limit=200)
    msgs = [r.error_message or "" for r in runs if r.status in ("error", "contract_failed") and r.error_message]
    if not msgs:
        return []
    buckets: Counter = Counter(msg[:80] for msg in msgs)
    return [{"prefix": prefix, "count": count}
            for prefix, count in buckets.most_common(limit)]


def next_action_for(capability_id: str, *, role: str | None = None,
                     registry: CapabilityRegistry | None = None) -> str:
    reg = registry or default_registry()
    cap = reg.get(capability_id)
    runs = workflow_runner.list_runs(capability_id=capability_id, limit=100)
    if not cap:
        return "Capability not found."
    if not runs:
        return "Try it on a small example to see the output shape."
    succ = [r for r in runs if r.status == "succeeded"]
    if not succ:
        return "All recent runs failed — open the failure trace before running again."
    agg = feedback_store.get_aggregate(capability_id)
    if (agg.get("total_feedback") or 0) == 0:
        return "Submit feedback so the platform learns from your usage."
    if training_agent.has_walkthrough(capability_id) and len(runs) < 3:
        return "Open the walkthrough — it explains the expected inputs."
    return "Run it on a real task — this capability is well-validated."
