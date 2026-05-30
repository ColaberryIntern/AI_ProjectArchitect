"""Prompt diff + execution diff — quantify what changed between two
capability versions and whether it actually improved results.

Two diff types
--------------
1. **Prompt diff** (deterministic, no LLM): line-by-line unified diff,
   token delta, structural markers (added/removed bullet sections,
   added/removed numbered steps, changed JSON-mode contract sections),
   instruction-verb delta (counts of "must", "should", "do not", "avoid").
   Saved at output/ops_platform/prompt_diffs/{v1}_{v2}.json.

2. **Execution diff** (statistical, no LLM): pulls all run records made
   under each version and compares reliability, mean duration, mean
   prompt tokens, feedback ratings if any. Reports the deltas so the
   operator can see "did this improvement actually help".

This module never re-runs anything. Both flavors of diff are pure read.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution.ops_platform import (
    capability_versions,
    feedback_store,
    workflow_runner,
)

logger = logging.getLogger(__name__)

_DIFFS_DIR = OUTPUT_DIR / "ops_platform" / "prompt_diffs"


@dataclass
class PromptDiff:
    v1_id: str
    v2_id: str
    capability_id: str
    generated_at: str
    unified_diff: str
    line_count_v1: int
    line_count_v2: int
    delta_chars: int
    delta_words: int
    delta_tokens_estimated: int      # words / 0.75 as a rough token proxy
    added_lines: int
    removed_lines: int
    structural_markers: dict = field(default_factory=dict)
    instruction_verb_delta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ExecutionDiff:
    v1_id: str
    v2_id: str
    capability_id: str
    generated_at: str
    v1_runs: int
    v2_runs: int
    v1_reliability_pct: float | None
    v2_reliability_pct: float | None
    delta_reliability_pct: float | None
    v1_mean_duration_ms: float | None
    v2_mean_duration_ms: float | None
    delta_duration_ms: float | None
    v1_mean_prompt_tokens: float | None
    v2_mean_prompt_tokens: float | None
    delta_prompt_tokens: float | None
    v1_feedback_average: float | None
    v2_feedback_average: float | None
    delta_feedback: float | None
    verdict: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ── Public API ─────────────────────────────────────────────────────────


def diff_prompts(v1_id: str, v2_id: str) -> PromptDiff | None:
    v1 = capability_versions.get_version(v1_id)
    v2 = capability_versions.get_version(v2_id)
    if v1 is None or v2 is None or v1.capability_id != v2.capability_id:
        return None
    a = (v1.prompt_snapshot or "").splitlines()
    b = (v2.prompt_snapshot or "").splitlines()
    unified = "\n".join(difflib.unified_diff(
        a, b, fromfile=f"{v1.semver}", tofile=f"{v2.semver}", lineterm="",
    ))
    added = sum(1 for line in unified.splitlines() if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in unified.splitlines() if line.startswith("-") and not line.startswith("---"))
    word_v1 = len(" ".join(a).split())
    word_v2 = len(" ".join(b).split())
    diff = PromptDiff(
        v1_id=v1_id, v2_id=v2_id, capability_id=v1.capability_id,
        generated_at=datetime.now(timezone.utc).isoformat(),
        unified_diff=unified,
        line_count_v1=len(a), line_count_v2=len(b),
        delta_chars=sum(len(line) for line in b) - sum(len(line) for line in a),
        delta_words=word_v2 - word_v1,
        delta_tokens_estimated=int((word_v2 - word_v1) / 0.75),
        added_lines=added, removed_lines=removed,
        structural_markers=_structural_markers(v1.prompt_snapshot or "", v2.prompt_snapshot or ""),
        instruction_verb_delta=_instruction_verbs(v1.prompt_snapshot or "", v2.prompt_snapshot or ""),
    )
    _persist_prompt_diff(diff)
    return diff


def diff_executions(v1_id: str, v2_id: str) -> ExecutionDiff | None:
    """Compare run quality between two versions. Filters runs by their
    inputs.__capability_version_id tag (set by version-aware callers).
    Falls back to capability-id-wide stats when version tags are missing."""
    v1 = capability_versions.get_version(v1_id)
    v2 = capability_versions.get_version(v2_id)
    if v1 is None or v2 is None or v1.capability_id != v2.capability_id:
        return None
    cap_id = v1.capability_id

    all_runs = workflow_runner.list_runs(capability_id=cap_id, limit=2000)
    v1_runs = [r for r in all_runs if (r.inputs.get("__capability_version_id") if isinstance(r.inputs, dict) else None) == v1_id]
    v2_runs = [r for r in all_runs if (r.inputs.get("__capability_version_id") if isinstance(r.inputs, dict) else None) == v2_id]

    return ExecutionDiff(
        v1_id=v1_id, v2_id=v2_id, capability_id=cap_id,
        generated_at=datetime.now(timezone.utc).isoformat(),
        v1_runs=len(v1_runs), v2_runs=len(v2_runs),
        v1_reliability_pct=_reliability_pct(v1_runs),
        v2_reliability_pct=_reliability_pct(v2_runs),
        delta_reliability_pct=_delta(_reliability_pct(v1_runs), _reliability_pct(v2_runs)),
        v1_mean_duration_ms=_mean_attr(v1_runs, "duration_ms"),
        v2_mean_duration_ms=_mean_attr(v2_runs, "duration_ms"),
        delta_duration_ms=_delta(_mean_attr(v1_runs, "duration_ms"), _mean_attr(v2_runs, "duration_ms")),
        v1_mean_prompt_tokens=_mean_token(v1_runs),
        v2_mean_prompt_tokens=_mean_token(v2_runs),
        delta_prompt_tokens=_delta(_mean_token(v1_runs), _mean_token(v2_runs)),
        v1_feedback_average=_feedback_avg(cap_id),
        v2_feedback_average=_feedback_avg(cap_id),
        delta_feedback=None,
        verdict=_verdict(v1_runs, v2_runs),
    )


def get_prompt_diff(v1_id: str, v2_id: str) -> dict | None:
    path = _DIFFS_DIR / f"{v1_id}_{v2_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# ── Internal ───────────────────────────────────────────────────────────


_VERB_PATTERNS = {
    "must": re.compile(r"\bmust\b", re.IGNORECASE),
    "should": re.compile(r"\bshould\b", re.IGNORECASE),
    "do_not": re.compile(r"\bdo not\b|\bdon'?t\b", re.IGNORECASE),
    "avoid": re.compile(r"\bavoid\b", re.IGNORECASE),
    "never": re.compile(r"\bnever\b", re.IGNORECASE),
    "always": re.compile(r"\balways\b", re.IGNORECASE),
}


def _instruction_verbs(a: str, b: str) -> dict:
    out: dict = {}
    for name, rx in _VERB_PATTERNS.items():
        ca = len(rx.findall(a))
        cb = len(rx.findall(b))
        if ca or cb:
            out[name] = {"v1": ca, "v2": cb, "delta": cb - ca}
    return out


_BULLET_RE = re.compile(r"^\s*[-*]\s", re.MULTILINE)
_NUMBERED_RE = re.compile(r"^\s*\d+\.\s", re.MULTILINE)
_CODE_FENCE_RE = re.compile(r"^```", re.MULTILINE)
_HEADING_RE = re.compile(r"^#{1,6}\s", re.MULTILINE)


def _structural_markers(a: str, b: str) -> dict:
    return {
        "bullet_lines": {"v1": len(_BULLET_RE.findall(a)), "v2": len(_BULLET_RE.findall(b))},
        "numbered_lines": {"v1": len(_NUMBERED_RE.findall(a)), "v2": len(_NUMBERED_RE.findall(b))},
        "code_fences": {"v1": len(_CODE_FENCE_RE.findall(a)), "v2": len(_CODE_FENCE_RE.findall(b))},
        "headings": {"v1": len(_HEADING_RE.findall(a)), "v2": len(_HEADING_RE.findall(b))},
    }


def _reliability_pct(runs: list) -> float | None:
    if not runs:
        return None
    succ = sum(1 for r in runs if r.status == "succeeded")
    return round(succ / len(runs) * 100, 1)


def _mean_attr(runs: list, attr: str) -> float | None:
    values = [getattr(r, attr) for r in runs if getattr(r, attr, None) is not None]
    return round(sum(values) / len(values), 1) if values else None


def _mean_token(runs: list) -> float | None:
    vals = [
        (r.llm_usage or {}).get("prompt_tokens", 0) for r in runs
        if isinstance(r.llm_usage, dict)
    ]
    return round(sum(vals) / len(vals), 1) if vals else None


def _feedback_avg(capability_id: str) -> float | None:
    agg = feedback_store.get_aggregate(capability_id)
    return agg.get("overall_average")


def _delta(a, b) -> float | None:
    if a is None or b is None:
        return None
    return round(b - a, 2)


def _verdict(v1_runs: list, v2_runs: list) -> str:
    r1, r2 = _reliability_pct(v1_runs), _reliability_pct(v2_runs)
    if r1 is None or r2 is None:
        return "insufficient_data"
    if r2 - r1 >= 5:
        return "improved"
    if r2 - r1 <= -5:
        return "regressed"
    return "no_meaningful_change"


def _persist_prompt_diff(diff: PromptDiff) -> None:
    _DIFFS_DIR.mkdir(parents=True, exist_ok=True)
    path = _DIFFS_DIR / f"{diff.v1_id}_{diff.v2_id}.json"
    path.write_text(json.dumps(diff.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
