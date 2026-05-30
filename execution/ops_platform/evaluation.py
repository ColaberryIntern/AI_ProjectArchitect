"""Evaluation framework — per-arm / per-version KPIs + significance estimate.

KPIs (cheap reads, no LLM):
  - success_rate
  - mean_latency_ms
  - mean_prompt_tokens
  - retry_rate
  - operator_feedback_avg
  - downstream_completion_rate (proxy: % of runs whose response.summary is non-empty)

Significance
------------
Uses a two-proportion z-test on success_rate between the experiment's
control (largest non-shadow arm) and each treatment arm. Returns the
absolute z-score and a qualitative ``confidence`` bucket:
  >= 2.58 → 99%, >= 1.96 → 95%, >= 1.64 → 90%, else inconclusive.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import asdict, dataclass, field

from execution.ops_platform import (
    experiments, feedback_store, workflow_runner,
)
from execution.ops_platform.capability_registry import CapabilityRegistry, default_registry

logger = logging.getLogger(__name__)


@dataclass
class ArmKPIs:
    arm_id: str
    label: str
    sample_size: int
    success_rate: float | None
    mean_latency_ms: float | None
    mean_prompt_tokens: float | None
    retry_rate: float | None
    operator_feedback_avg: float | None
    downstream_completion_rate: float | None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SignificanceFinding:
    arm_id: str
    z_score: float
    confidence: str               # "99%" | "95%" | "90%" | "inconclusive"
    direction: str                # "treatment_better" | "treatment_worse" | "no_signal"

    def to_dict(self) -> dict:
        return asdict(self)


# ── Public API ─────────────────────────────────────────────────────────


def evaluate_experiment(
    experiment_id: str,
    *,
    registry: CapabilityRegistry | None = None,
) -> dict:
    reg = registry or default_registry()
    exp = experiments.get(experiment_id)
    if exp is None:
        return {"error": "experiment not found"}
    runs = workflow_runner.list_runs(capability_id=exp.capability_id, limit=5000)
    by_arm = _bucket_runs_by_arm(runs, exp)
    kpis = [_arm_kpis(arm, by_arm.get(arm["arm_id"], []), capability_id=exp.capability_id)
              for arm in exp.arms]
    control_arm = _control_arm(kpis)
    significance: list[SignificanceFinding] = []
    if control_arm:
        for k in kpis:
            if k.arm_id == control_arm.arm_id:
                continue
            sig = _z_test(control_arm, k)
            if sig:
                significance.append(sig)
    return {
        "experiment_id": experiment_id, "capability_id": exp.capability_id,
        "state": exp.state, "arm_kpis": [k.to_dict() for k in kpis],
        "control_arm_id": control_arm.arm_id if control_arm else None,
        "significance": [s.to_dict() for s in significance],
    }


def version_scorecard(
    capability_id: str,
    version_id: str,
    *,
    registry: CapabilityRegistry | None = None,
) -> dict:
    reg = registry or default_registry()
    runs = workflow_runner.list_runs(capability_id=capability_id, limit=5000)
    version_runs = [
        r for r in runs
        if isinstance(r.inputs, dict)
        and r.inputs.get("__capability_version_id") == version_id
    ]
    k = _arm_kpis({"arm_id": version_id, "label": version_id},
                    version_runs, capability_id=capability_id)
    return k.to_dict()


# ── Internal ───────────────────────────────────────────────────────────


def _bucket_runs_by_arm(runs, exp) -> dict[str, list]:
    """Group runs by the arm they were assigned to. Uses
    inputs.__experiment_arm_id when present; otherwise inputs.__capability_version_id
    mapped through arm.capability_version_id."""
    by_arm: dict[str, list] = defaultdict(list)
    version_to_arm = {a.get("capability_version_id"): a["arm_id"]
                        for a in exp.arms if a.get("capability_version_id")}
    for r in runs:
        if not isinstance(r.inputs, dict):
            continue
        arm_id = r.inputs.get("__experiment_arm_id")
        if not arm_id:
            vid = r.inputs.get("__capability_version_id")
            if vid in version_to_arm:
                arm_id = version_to_arm[vid]
        if arm_id:
            by_arm[arm_id].append(r)
    return by_arm


def _arm_kpis(arm: dict, arm_runs, *, capability_id: str) -> ArmKPIs:
    total = len(arm_runs)
    succ = [r for r in arm_runs if r.status == "succeeded"]
    durations = [r.duration_ms for r in succ if r.duration_ms]
    tokens = [
        (r.llm_usage or {}).get("prompt_tokens", 0)
        for r in succ if isinstance(r.llm_usage, dict)
    ]
    retries = sum(1 for r in arm_runs if r.status == "retried_succeeded")
    aggregate = feedback_store.get_aggregate(capability_id)
    downstream_complete = sum(
        1 for r in succ if (r.response or {}).get("summary")
    )
    return ArmKPIs(
        arm_id=arm["arm_id"], label=arm.get("label", arm["arm_id"]),
        sample_size=total,
        success_rate=round(len(succ) / total, 3) if total else None,
        mean_latency_ms=round(sum(durations) / len(durations), 1) if durations else None,
        mean_prompt_tokens=round(sum(tokens) / len(tokens), 1) if tokens else None,
        retry_rate=round(retries / total, 3) if total else None,
        operator_feedback_avg=aggregate.get("overall_average"),
        downstream_completion_rate=round(downstream_complete / len(succ), 3) if succ else None,
    )


def _control_arm(kpis: list[ArmKPIs]) -> ArmKPIs | None:
    if not kpis:
        return None
    return max(kpis, key=lambda k: k.sample_size or 0)


def _z_test(control: ArmKPIs, treatment: ArmKPIs) -> SignificanceFinding | None:
    if not (control.sample_size and treatment.sample_size):
        return None
    if control.success_rate is None or treatment.success_rate is None:
        return None
    p1 = control.success_rate
    p2 = treatment.success_rate
    n1 = control.sample_size
    n2 = treatment.sample_size
    p_pool = (p1 * n1 + p2 * n2) / (n1 + n2)
    denom = math.sqrt(p_pool * (1 - p_pool) * (1 / n1 + 1 / n2))
    if denom == 0:
        return None
    z = (p2 - p1) / denom
    abs_z = abs(z)
    if abs_z >= 2.58:
        confidence = "99%"
    elif abs_z >= 1.96:
        confidence = "95%"
    elif abs_z >= 1.64:
        confidence = "90%"
    else:
        confidence = "inconclusive"
    if confidence == "inconclusive":
        direction = "no_signal"
    elif z > 0:
        direction = "treatment_better"
    else:
        direction = "treatment_worse"
    return SignificanceFinding(arm_id=treatment.arm_id, z_score=round(z, 3),
                                  confidence=confidence, direction=direction)
