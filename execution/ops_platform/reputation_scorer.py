"""Reputation scorer — 8-dimension capability score from existing signals.

Reads (does not write) the four signal sources:
  1. workflow_runner runs        →  usage_score, reliability_score
  2. feedback_store records      →  feedback_score, business_impact_score, team_adoption_score
  3. pipeline_engine runs        →  pipeline_usage_score
  4. verification_agent results  →  verification_score (derived from run.response.known_issues
                                    + persisted spec_reports if present)

Score model (each 0-100 unless noted):
  reputation_score = weighted_sum of the 7 sub-scores, weights below.

  W_usage         = 0.10
  W_reliability   = 0.20
  W_business_impact = 0.20
  W_feedback      = 0.15
  W_verification  = 0.15
  W_pipeline_use  = 0.10
  W_team_adoption = 0.10

The scorer is intentionally cheap (O(N runs + M feedback)). It runs on demand
when callers ask for a score, or in batch when the analytics dashboard refreshes.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution.ops_platform import feedback_store, pipeline_engine, workflow_runner
from execution.ops_platform.capability_registry import CapabilityRegistry, default_registry

logger = logging.getLogger(__name__)

_SCORE_DIR = OUTPUT_DIR / "ops_platform" / "reputation"

# Saturation points: above these raw counts the sub-score saturates at 100.
SAT_USAGE_RUNS = 50            # 50 successful runs → full usage score
SAT_FEEDBACK_RECORDS = 20      # 20 feedback records → full team_adoption signal
SAT_PIPELINE_INCLUSIONS = 5    # included in 5 pipelines → full pipeline_usage signal
SAT_DISTINCT_SUBMITTERS = 10

# Weights for composite reputation score (must sum to 1.0)
WEIGHTS = {
    "usage_score": 0.10,
    "reliability_score": 0.20,
    "business_impact_score": 0.20,
    "feedback_score": 0.15,
    "verification_score": 0.15,
    "pipeline_usage_score": 0.10,
    "team_adoption_score": 0.10,
}


@dataclass
class ReputationScore:
    capability_id: str
    computed_at: str
    reputation_score: float
    usage_score: float
    reliability_score: float
    business_impact_score: float
    feedback_score: float
    verification_score: float
    pipeline_usage_score: float
    team_adoption_score: float
    signal_counts: dict

    def to_dict(self) -> dict:
        return self.__dict__.copy()


# ── Public API ─────────────────────────────────────────────────────────


def score_capability(
    capability_id: str,
    *,
    registry: CapabilityRegistry | None = None,
    persist: bool = True,
) -> ReputationScore:
    """Compute a fresh reputation score for one capability. Persists when asked."""
    reg = registry or default_registry()

    # Signal 1: runs
    runs = workflow_runner.list_runs(capability_id=capability_id, limit=1000)
    total_runs = len(runs)
    succeeded_runs = sum(1 for r in runs if r.status == "succeeded")
    failed_runs = total_runs - succeeded_runs

    # Signal 2: feedback
    feedback_records = feedback_store.list_feedback(capability_id)
    aggregate = feedback_store.get_aggregate(capability_id)
    distinct_submitters = len({
        (r.get("submitter") or {}).get("name", "") for r in feedback_records
        if (r.get("submitter") or {}).get("name")
    })

    # Signal 3: pipeline inclusions
    pipeline_inclusions = _count_pipeline_inclusions(capability_id)

    # Signal 4: verification — derived from runs that had no blocker known_issues
    v_green, v_yellow, v_red = _verification_distribution(runs)

    # Compute sub-scores
    usage_score = _saturate(succeeded_runs, SAT_USAGE_RUNS)
    reliability_score = (succeeded_runs / total_runs * 100) if total_runs else 0.0
    feedback_score = _ratings_to_score(aggregate.get("overall_average"))
    business_impact_score = _ratings_to_score(
        (aggregate.get("averages") or {}).get("time_savings")
    )
    pipeline_usage_score = _saturate(pipeline_inclusions, SAT_PIPELINE_INCLUSIONS)
    team_adoption_score = _saturate(distinct_submitters, SAT_DISTINCT_SUBMITTERS)
    verification_score = _verification_score(v_green, v_yellow, v_red)

    reputation_score = (
        usage_score * WEIGHTS["usage_score"]
        + reliability_score * WEIGHTS["reliability_score"]
        + business_impact_score * WEIGHTS["business_impact_score"]
        + feedback_score * WEIGHTS["feedback_score"]
        + verification_score * WEIGHTS["verification_score"]
        + pipeline_usage_score * WEIGHTS["pipeline_usage_score"]
        + team_adoption_score * WEIGHTS["team_adoption_score"]
    )

    score = ReputationScore(
        capability_id=capability_id,
        computed_at=datetime.now(timezone.utc).isoformat(),
        reputation_score=round(reputation_score, 2),
        usage_score=round(usage_score, 2),
        reliability_score=round(reliability_score, 2),
        business_impact_score=round(business_impact_score, 2),
        feedback_score=round(feedback_score, 2),
        verification_score=round(verification_score, 2),
        pipeline_usage_score=round(pipeline_usage_score, 2),
        team_adoption_score=round(team_adoption_score, 2),
        signal_counts={
            "total_runs": total_runs,
            "succeeded_runs": succeeded_runs,
            "failed_runs": failed_runs,
            "feedback_records": len(feedback_records),
            "distinct_submitters": distinct_submitters,
            "pipeline_inclusions": pipeline_inclusions,
            "verifications_green": v_green,
            "verifications_yellow": v_yellow,
            "verifications_red": v_red,
        },
    )
    if persist:
        _persist(score)
    return score


def score_all(
    *,
    registry: CapabilityRegistry | None = None,
    persist: bool = True,
) -> dict[str, ReputationScore]:
    reg = registry or default_registry()
    out: dict[str, ReputationScore] = {}
    for cap in reg.snapshot().capabilities:
        out[cap["id"]] = score_capability(cap["id"], registry=reg, persist=persist)
    return out


def ranked(
    *,
    top_n: int | None = None,
    registry: CapabilityRegistry | None = None,
) -> list[ReputationScore]:
    """Score every capability and return sorted by reputation desc."""
    scores = list(score_all(registry=registry).values())
    scores.sort(key=lambda s: s.reputation_score, reverse=True)
    return scores[:top_n] if top_n else scores


def load_score(capability_id: str) -> dict | None:
    path = _SCORE_DIR / f"{capability_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# ── Internal ───────────────────────────────────────────────────────────


def _saturate(count: int, saturation: int) -> float:
    """Map a raw count into 0-100 using a linear-then-clamp curve."""
    if saturation <= 0:
        return 0.0
    return min(100.0, (count / saturation) * 100.0)


def _ratings_to_score(rating_1_to_5: float | None) -> float:
    """A 4.5/5 rating becomes 90; missing → 0."""
    if rating_1_to_5 is None:
        return 0.0
    return max(0.0, min(100.0, float(rating_1_to_5) * 20.0))


def _verification_distribution(runs: list) -> tuple[int, int, int]:
    """Approximate verification distribution from RunRecord.response.known_issues
    severities. A run with no blocker = green; with high = yellow; with blocker = red.
    This is a deliberate proxy when verification_agent hasn't been invoked per-run.
    """
    green = yellow = red = 0
    for run in runs:
        if run.status != "succeeded":
            red += 1
            continue
        issues = (run.response or {}).get("known_issues") or []
        severities = [i.get("severity", "medium") for i in issues]
        if "blocker" in severities:
            red += 1
        elif "high" in severities:
            yellow += 1
        else:
            green += 1
    return green, yellow, red


def _verification_score(green: int, yellow: int, red: int) -> float:
    total = green + yellow + red
    if not total:
        return 0.0
    return ((green * 1.0) + (yellow * 0.5) + (red * 0.0)) / total * 100.0


def _count_pipeline_inclusions(capability_id: str) -> int:
    """How many distinct pipelines include this capability_id."""
    count = 0
    try:
        for manifest in pipeline_engine.list_pipelines():
            step_caps = {s.get("capability_id") for s in manifest.get("steps") or []}
            if capability_id in step_caps:
                count += 1
    except Exception:
        logger.warning("pipeline inclusion count failed", exc_info=True)
    return count


def _persist(score: ReputationScore) -> None:
    _SCORE_DIR.mkdir(parents=True, exist_ok=True)
    target = _SCORE_DIR / f"{score.capability_id}.json"
    target.write_text(json.dumps(score.to_dict(), indent=2), encoding="utf-8")
    # Append to history for trend analysis
    try:
        _append_history(score)
    except Exception:
        logger.warning("reputation history append failed for %s", score.capability_id, exc_info=True)
    try:
        from execution.ops_platform import cache_bus
        cache_bus.emit(cache_bus.Topic.REPUTATION_RECORDED, {
            "capability_id": score.capability_id,
            "reputation_score": score.reputation_score,
        })
    except Exception:
        logger.warning("cache_bus emit failed for REPUTATION_RECORDED", exc_info=True)


# ── History (trend) ────────────────────────────────────────────────────


_HISTORY_DIR = OUTPUT_DIR / "ops_platform" / "reputation_history"


def _append_history(score: ReputationScore) -> None:
    _HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    path = _HISTORY_DIR / f"{score.capability_id}.jsonl"
    payload = {
        "computed_at": score.computed_at,
        "reputation_score": score.reputation_score,
        "usage_score": score.usage_score,
        "reliability_score": score.reliability_score,
        "business_impact_score": score.business_impact_score,
        "feedback_score": score.feedback_score,
        "verification_score": score.verification_score,
        "pipeline_usage_score": score.pipeline_usage_score,
        "team_adoption_score": score.team_adoption_score,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload) + "\n")


def load_history(capability_id: str, *, limit: int = 200) -> list[dict]:
    """Return persisted score history (oldest-first)."""
    path = _HISTORY_DIR / f"{capability_id}.jsonl"
    if not path.exists():
        return []
    out: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out[-limit:]


def trend(capability_id: str, *, lookback: int = 5) -> dict:
    """Compare the latest score to N back. Returns {direction, delta, latest}."""
    history = load_history(capability_id)
    if not history:
        return {"direction": "unknown", "delta": 0.0, "latest": None}
    latest = history[-1]["reputation_score"]
    earlier = history[-min(lookback, len(history))]["reputation_score"]
    delta = round(latest - earlier, 2)
    if delta > 2:
        direction = "rising"
    elif delta < -2:
        direction = "falling"
    else:
        direction = "stable"
    return {"direction": direction, "delta": delta, "latest": latest, "samples": len(history)}


# ── Incremental scoring (skip if unchanged) ────────────────────────────


def score_if_stale(
    capability_id: str,
    *,
    registry: CapabilityRegistry | None = None,
    persist: bool = True,
    max_age_seconds: int = 3600,
) -> tuple[ReputationScore | None, bool]:
    """Recompute only if the persisted score is older than ``max_age_seconds``.
    Returns (score_or_None, recomputed_flag)."""
    persisted = load_score(capability_id)
    from datetime import datetime as _dt
    if persisted and persisted.get("computed_at"):
        try:
            then = _dt.fromisoformat(persisted["computed_at"])
            age = (datetime.now(timezone.utc) - then).total_seconds()
            if age < max_age_seconds:
                return None, False
        except (TypeError, ValueError):
            pass
    score = score_capability(capability_id, registry=registry, persist=persist)
    return score, True
