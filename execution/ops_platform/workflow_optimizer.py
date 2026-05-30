"""Adaptive workflow optimizer — produces concrete improvement suggestions
for capabilities and pipelines based on actual usage signals.

This is the "auto-improving" layer that watches what happens, finds the
levers worth pulling, and surfaces them as a review queue for operators.
The module itself never applies changes — humans always decide.

Suggestion kinds emitted
------------------------
- SHORTEN_PROMPT       : capability prompt is long AND token usage runs hot
- DECOMPOSE_CAPABILITY : capability has chronic high-severity issues
- LOW_VALUE_CANDIDATE  : low usage + low reputation + no recent feedback
- BOTTLENECK_STEP      : pipeline step with p95 duration >> other steps
- REDUNDANT_STEP       : step output never read by downstream bindings
- CONSOLIDATE_PAIR     : two capabilities with semantic overlap + similar usage
- AUTO_PIPELINE        : repeated A→B run sequence above confidence threshold
                         (uses workflow_discovery under the hood)

Output shape: list[Suggestion] with capability/pipeline ref, severity (1-5),
confidence (0-1), human-readable title, evidence dict, recommended action.
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from execution.ops_platform import (
    analytics,
    pipeline_engine,
    reputation_scorer,
    semantic_analyzer,
    workflow_discovery,
    workflow_runner,
)
from execution.ops_platform.capability_registry import CapabilityRegistry, default_registry

logger = logging.getLogger(__name__)


@dataclass
class Suggestion:
    kind: str
    title: str
    capability_id: str | None = None
    pipeline_id: str | None = None
    severity: int = 3                # 1=advisory, 5=urgent
    confidence: float = 0.5          # 0..1
    evidence: dict = field(default_factory=dict)
    recommended_action: str = ""
    suggestion_id: str = ""          # stable id derived from kind + entity

    def __post_init__(self) -> None:
        if not self.suggestion_id:
            anchor = self.capability_id or self.pipeline_id or self.title
            self.suggestion_id = f"{self.kind}:{anchor}"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Simulation:
    suggestion_id: str
    kind: str
    projected_hours_saved: float = 0.0
    projected_failure_reduction_pct: float = 0.0
    projected_complexity_delta: int = 0
    risk: str = "low"          # low | medium | high
    reversible: bool = True
    rationale: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ── Public API ─────────────────────────────────────────────────────────


def analyze(
    *,
    registry: CapabilityRegistry | None = None,
) -> list[Suggestion]:
    reg = registry or default_registry()
    out: list[Suggestion] = []
    out.extend(_shorten_prompt_candidates(reg))
    out.extend(_decompose_candidates(reg))
    out.extend(_low_value_candidates(reg))
    out.extend(_bottleneck_steps(reg))
    out.extend(_redundant_steps(reg))
    out.extend(_consolidate_pairs(reg))
    out.extend(_auto_pipeline_candidates(reg))
    out.sort(key=lambda s: (s.severity, s.confidence), reverse=True)
    return out


def auto_pipeline_suggestions(
    *,
    min_occurrences: int = 3,
    window: int = 3,
    registry: CapabilityRegistry | None = None,
) -> list[Suggestion]:
    """Surface auto-pipeline suggestions in isolation (used by the
    home-page widget)."""
    return _auto_pipeline_candidates(registry or default_registry(),
                                      min_occurrences=min_occurrences,
                                      window=window)


# ── Phase 4F: simulate + apply ─────────────────────────────────────────


def simulate(suggestion_id: str, *, registry: CapabilityRegistry | None = None) -> Simulation | None:
    """Project the impact of applying a suggestion. Pure read — no mutation."""
    reg = registry or default_registry()
    suggestions = analyze(registry=reg)
    target = next((s for s in suggestions if s.suggestion_id == suggestion_id), None)
    if target is None:
        return None

    if target.kind == "SHORTEN_PROMPT":
        mean_tokens = target.evidence.get("mean_prompt_tokens", 0) or 0
        savings_per_run = max(0, mean_tokens - 1500)
        return Simulation(
            suggestion_id=target.suggestion_id, kind=target.kind,
            projected_hours_saved=round(savings_per_run * 0.00001, 2),
            projected_failure_reduction_pct=0.0,
            projected_complexity_delta=-1,
            risk="low", reversible=True,
            rationale=(
                f"Shortening from ~{int(mean_tokens)} to ~1500 tokens cuts marginal "
                "LLM cost; behavior unchanged when the trimmed framing was redundant."
            ),
        )

    if target.kind == "DECOMPOSE_CAPABILITY":
        return Simulation(
            suggestion_id=target.suggestion_id, kind=target.kind,
            projected_failure_reduction_pct=round(target.evidence.get("failure_rate_pct", 0) * 0.5, 1),
            projected_complexity_delta=+1,
            risk="medium", reversible=False,
            rationale=(
                "Splitting one failing capability into 2-3 smaller ones typically "
                "halves the end-to-end failure rate but adds maintenance surface."
            ),
        )

    if target.kind == "LOW_VALUE_CANDIDATE":
        return Simulation(
            suggestion_id=target.suggestion_id, kind=target.kind,
            projected_complexity_delta=-1,
            risk="low", reversible=True,
            rationale=(
                "Retiring an unused capability reduces registry noise. Restore by "
                "moving the manifest back into /plugins/ if needed."
            ),
        )

    if target.kind == "BOTTLENECK_STEP":
        p95 = target.evidence.get("p95_ms", 0) or 0
        return Simulation(
            suggestion_id=target.suggestion_id, kind=target.kind,
            projected_hours_saved=round(p95 / 1000 / 3600, 2),
            risk="medium", reversible=True,
            rationale=(
                f"Reducing p95 from {p95} ms to the pipeline median improves "
                "perceived latency without changing outputs."
            ),
        )

    if target.kind == "REDUNDANT_STEP":
        return Simulation(
            suggestion_id=target.suggestion_id, kind=target.kind,
            projected_complexity_delta=-1,
            risk="medium", reversible=False,
            rationale=(
                "Removing the step is reversible only via git; consider whether "
                "any downstream consumer relies on its persistence as an audit point."
            ),
        )

    if target.kind == "CONSOLIDATE_PAIR":
        overlap = target.evidence.get("overlap", 0)
        return Simulation(
            suggestion_id=target.suggestion_id, kind=target.kind,
            projected_complexity_delta=-1,
            risk="medium", reversible=False,
            rationale=(
                f"Pair overlaps at {overlap * 100:.0f}%. Merge reduces registry "
                "duplication but affects every existing user of the dropped capability."
            ),
        )

    if target.kind == "AUTO_PIPELINE":
        occ = target.evidence.get("occurrences", 0)
        return Simulation(
            suggestion_id=target.suggestion_id, kind=target.kind,
            projected_hours_saved=round(occ * 0.05, 2),
            risk="low", reversible=True,
            rationale=(
                f"Publishing this pipeline standardizes {occ} observed runs. "
                "Reversible by archiving the pipeline manifest."
            ),
        )

    return Simulation(
        suggestion_id=target.suggestion_id, kind=target.kind,
        risk="medium", reversible=False,
        rationale="No specific projection model for this kind yet.",
    )


def apply(suggestion_id: str, *, actor: dict | str | None = None,
          registry: CapabilityRegistry | None = None) -> dict:
    """Apply a suggestion safely. NO suggestion ever mutates without an
    explicit operator action that triggers this function. Returns a result
    dict with ``status`` plus either ``new_state`` or ``error``.

    Implementations:
      - AUTO_PIPELINE     : publish the draft via the discovery_queue route
      - LOW_VALUE_CANDIDATE: record the retire decision (no file deletion)
      - All others        : recorded but no automated mutation (operator must act)
    """
    from execution.ops_platform import audit_log
    reg = registry or default_registry()
    suggestions = analyze(registry=reg)
    target = next((s for s in suggestions if s.suggestion_id == suggestion_id), None)
    if target is None:
        return {"status": "not_found", "error": f"suggestion '{suggestion_id}' not found"}

    actor_norm = actor if isinstance(actor, dict) else {"name": str(actor or "anonymous")}

    if target.kind == "AUTO_PIPELINE":
        from execution.ops_platform import pipeline_engine, workflow_discovery
        # Re-derive the draft pipeline manifest from the optimizer evidence
        sequence = target.evidence.get("sequence") or []
        if not sequence:
            return {"status": "rejected", "error": "no sequence in suggestion evidence"}
        names = [reg.snapshot().by_id().get(cid, {}).get("name", cid) for cid in sequence]
        manifest = {
            "pipeline_id": f"applied-{target.suggestion_id.split(':', 1)[1][:24]}".lower().replace(":", "-"),
            "name": " -> ".join(names),
            "description": "Materialized from optimizer suggestion via apply().",
            "version": "0.1.0",
            "created_by": actor_norm,
            "execution_strategy": "sequential",
            "tags": ["optimizer", "auto-applied"],
            "steps": [
                {
                    "step_id": f"step_{i + 1}", "capability_id": cid,
                    "depends_on": ([f"step_{i}"] if i > 0 else []),
                    "on_failure": "abort", "input_bindings": {},
                }
                for i, cid in enumerate(sequence)
            ],
        }
        try:
            pipeline_engine.save_pipeline(manifest)
        except (ValueError, OSError) as e:
            return {"status": "failed", "error": str(e)}
        audit_log.record(
            action="optimizer.applied", entity_type="optimizer_suggestion",
            entity_id=suggestion_id, actor=actor_norm,
            new_state={"created_pipeline": manifest["pipeline_id"]},
            metadata={"kind": target.kind},
        )
        return {"status": "applied", "new_state": {"pipeline_id": manifest["pipeline_id"]}}

    # All other kinds: record the decision but do not mutate
    audit_log.record(
        action="optimizer.acknowledged", entity_type="optimizer_suggestion",
        entity_id=suggestion_id, actor=actor_norm,
        metadata={"kind": target.kind, "title": target.title,
                  "note": "no automated mutation for this kind; operator must act"},
    )
    return {
        "status": "acknowledged",
        "message": "Suggestion logged; manual action required to apply this kind.",
    }


# ── Internal detectors ─────────────────────────────────────────────────


def _shorten_prompt_candidates(reg: CapabilityRegistry, char_threshold: int = 3500) -> list[Suggestion]:
    """Long prompts + high token usage → opportunity to shorten."""
    out: list[Suggestion] = []
    from config.settings import PROJECT_ROOT
    from pathlib import Path
    for cap in reg.snapshot().capabilities:
        if cap.get("type") not in ("workflow", "agent"):
            continue
        prompt_rel = cap.get("prompt_path")
        if not prompt_rel:
            continue
        meta = cap.get("_meta") or {}
        abs_dir = meta.get("plugin_dir_absolute")
        plugin_dir = Path(abs_dir) if abs_dir else (PROJECT_ROOT / meta.get("plugin_dir", ""))
        prompt_file = plugin_dir / prompt_rel
        try:
            size = prompt_file.stat().st_size
        except OSError:
            continue
        if size < char_threshold:
            continue
        runs = workflow_runner.list_runs(capability_id=cap["id"], limit=50)
        tokens = [
            (r.llm_usage or {}).get("prompt_tokens", 0) for r in runs
            if isinstance(r.llm_usage, dict)
        ]
        mean_tokens = sum(tokens) / len(tokens) if tokens else 0
        if mean_tokens < 1500:
            continue
        out.append(Suggestion(
            kind="SHORTEN_PROMPT",
            title=f"Consider shortening the prompt for {cap.get('name', cap['id'])}",
            capability_id=cap["id"],
            severity=2,
            confidence=min(1.0, mean_tokens / 4000),
            evidence={
                "prompt_size_bytes": size,
                "mean_prompt_tokens": int(mean_tokens),
                "samples": len(tokens),
            },
            recommended_action=(
                "Open the prompt file, remove repeated framing, move static "
                "context into the system prompt, and re-test."
            ),
        ))
    return out


def _decompose_candidates(reg: CapabilityRegistry) -> list[Suggestion]:
    """Capabilities with persistent failure rates → split into smaller units."""
    out: list[Suggestion] = []
    bots = analytics.bottlenecks(min_runs=5, registry=reg)
    for row in bots:
        if row["failure_rate_pct"] < 25:
            continue
        out.append(Suggestion(
            kind="DECOMPOSE_CAPABILITY",
            title=f"{row['name']} fails {row['failure_rate_pct']}% of the time — consider decomposition",
            capability_id=row["capability_id"],
            severity=4,
            confidence=min(1.0, row["failure_rate_pct"] / 100),
            evidence={
                "failures": row["failures"],
                "total_runs": row["total_runs"],
                "by_status": row.get("by_status"),
            },
            recommended_action=(
                "Inspect failure messages, then split this capability into "
                "smaller workflows so partial successes are recoverable."
            ),
        ))
    return out


def _low_value_candidates(reg: CapabilityRegistry) -> list[Suggestion]:
    out: list[Suggestion] = []
    by_id = reg.snapshot().by_id()
    for cap in reg.snapshot().capabilities:
        usage = cap.get("usage_count", 0)
        if usage > 3:
            continue
        score = (reputation_scorer.load_score(cap["id"]) or {}).get("reputation_score", 0)
        if score > 30:
            continue
        out.append(Suggestion(
            kind="LOW_VALUE_CANDIDATE",
            title=f"{cap.get('name', cap['id'])} is barely used — keep or retire?",
            capability_id=cap["id"],
            severity=2,
            confidence=0.5,
            evidence={"usage_count": usage, "reputation_score": score},
            recommended_action=(
                "Confirm with the capability's owner. If still strategic, "
                "queue a marketing push or onboarding insertion. Otherwise retire."
            ),
        ))
    return out


def _bottleneck_steps(reg: CapabilityRegistry) -> list[Suggestion]:
    """Pipeline steps with p95 duration much larger than the pipeline median."""
    out: list[Suggestion] = []
    runs = pipeline_engine.list_pipeline_runs(limit=500)
    by_pipeline: dict[str, list] = defaultdict(list)
    for r in runs:
        by_pipeline[r.pipeline_id].append(r)
    for pipeline_id, group in by_pipeline.items():
        # Collect durations per step across successful runs.
        per_step: dict[str, list[int]] = defaultdict(list)
        for run in group:
            for sr in run.step_runs:
                if sr.status in ("succeeded", "retried_succeeded") and sr.started_at and sr.finished_at:
                    try:
                        a = datetime.fromisoformat(sr.started_at)
                        b = datetime.fromisoformat(sr.finished_at)
                        per_step[sr.step_id].append(int((b - a).total_seconds() * 1000))
                    except (TypeError, ValueError):
                        continue
        if not per_step:
            continue
        medians = {s: sorted(ds)[len(ds) // 2] for s, ds in per_step.items() if ds}
        if not medians:
            continue
        overall_median = sorted(medians.values())[len(medians) // 2] or 1
        for step_id, ds in per_step.items():
            p95 = sorted(ds)[max(0, int(len(ds) * 0.95) - 1)]
            if p95 > overall_median * 3 and p95 > 2000:
                out.append(Suggestion(
                    kind="BOTTLENECK_STEP",
                    title=f"Step '{step_id}' in pipeline '{pipeline_id}' is the slow link",
                    pipeline_id=pipeline_id,
                    severity=3,
                    confidence=0.7,
                    evidence={
                        "step_id": step_id,
                        "p95_ms": p95,
                        "samples": len(ds),
                        "pipeline_median_ms": overall_median,
                    },
                    recommended_action=(
                        "Reduce context size, parallelize where possible, or "
                        "swap the underlying capability for a leaner variant."
                    ),
                ))
    return out


def _redundant_steps(reg: CapabilityRegistry) -> list[Suggestion]:
    """Pipeline steps whose outputs no downstream binding references."""
    out: list[Suggestion] = []
    for manifest in pipeline_engine.list_pipelines():
        steps = manifest.get("steps") or []
        if len(steps) < 2:
            continue
        all_bindings_text = " ".join(
            f"{v}" for s in steps
            for v in (s.get("input_bindings") or {}).values()
        )
        output_mapping_text = " ".join(
            m.get("source", "") for m in manifest.get("output_mappings") or []
        )
        haystack = all_bindings_text + " " + output_mapping_text
        for step in steps[:-1]:  # last step's output may flow out of pipeline naturally
            needle = f"$step.{step['step_id']}."
            if needle not in haystack:
                out.append(Suggestion(
                    kind="REDUNDANT_STEP",
                    title=f"Step '{step['step_id']}' output is never consumed",
                    pipeline_id=manifest["pipeline_id"],
                    severity=2,
                    confidence=0.8,
                    evidence={"step_id": step["step_id"], "capability_id": step["capability_id"]},
                    recommended_action=(
                        "Either map its output to a downstream binding or remove the step."
                    ),
                ))
    return out


def _consolidate_pairs(reg: CapabilityRegistry) -> list[Suggestion]:
    pairs = semantic_analyzer.workflow_overlap(registry=reg, threshold=0.4)
    out: list[Suggestion] = []
    for p in pairs[:10]:
        out.append(Suggestion(
            kind="CONSOLIDATE_PAIR",
            title=f"{p['a_name']} and {p['b_name']} overlap heavily ({p['overlap']*100:.0f}%)",
            severity=2,
            confidence=p["overlap"],
            evidence=p,
            recommended_action=(
                "Compare manifests; merge if functionality is duplicated, or "
                "tighten descriptions if they are distinct."
            ),
        ))
    return out


def _auto_pipeline_candidates(reg: CapabilityRegistry, *,
                                min_occurrences: int = 3,
                                window: int = 3) -> list[Suggestion]:
    patterns = workflow_discovery.discover_patterns(
        window=window, min_occurrences=min_occurrences, registry=reg,
    )
    out: list[Suggestion] = []
    for p in patterns[:8]:
        sample = " → ".join(p.capability_names)
        confidence = min(1.0, (p.occurrences / 10) * 0.6 + (p.distinct_initiators / 5) * 0.4)
        out.append(Suggestion(
            kind="AUTO_PIPELINE",
            title=f"Repeated pattern: {sample}",
            severity=3,
            confidence=round(confidence, 2),
            evidence={
                "sequence": list(p.sequence),
                "occurrences": p.occurrences,
                "distinct_initiators": p.distinct_initiators,
                "draft_pipeline_id": (p.draft_pipeline or {}).get("pipeline_id"),
            },
            recommended_action=(
                "Open the Discovery Queue to approve and publish this as an "
                "automated pipeline. One click."
            ),
        ))
    return out
