"""Organizational memory engine — what the platform has learned about how
this organization actually operates.

This module is the read-side roll-up of every other observation we already
collect. It does not introduce new persistence — it queries existing
artifacts (runs, feedback, reputations, enrichments, discoveries) and
returns the distillation:

  what_succeeds     — capabilities + pipelines with high success + adoption
  what_fails        — capabilities with persistent failure modes
  team_preferences  — per-department capability preferences derived from
                      use frequency + feedback
  prompt_insights   — capabilities whose feedback mentions prompt tweaks,
                      and the actual lines that surfaced
  success_patterns  — sequences of capabilities that correlate with
                      project success (proxy: pipeline runs with status
                      'succeeded' and high downstream reputation)

The output influences:
  - recommendation_engine (via reputation + graph that this engine already feeds)
  - the home-page "what your org is learning" widget
  - the onboarding flow (so new employees see what their team already proved)
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution.ops_platform import (
    feedback_store,
    pipeline_engine,
    reputation_scorer,
    semantic_analyzer,
    workflow_runner,
)
from execution.ops_platform.capability_registry import CapabilityRegistry, default_registry

logger = logging.getLogger(__name__)

_MEMORY_DIR = OUTPUT_DIR / "ops_platform" / "org_memory"


@dataclass
class MemorySnapshot:
    generated_at: str
    what_succeeds: list[dict] = field(default_factory=list)
    what_fails: list[dict] = field(default_factory=list)
    team_preferences: dict = field(default_factory=dict)  # dept -> [capabilities]
    prompt_insights: list[dict] = field(default_factory=list)
    success_patterns: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ── Public API ─────────────────────────────────────────────────────────


def build_snapshot(
    *,
    registry: CapabilityRegistry | None = None,
    persist: bool = True,
) -> MemorySnapshot:
    reg = registry or default_registry()
    snap = MemorySnapshot(
        generated_at=datetime.now(timezone.utc).isoformat(),
        what_succeeds=_what_succeeds(reg),
        what_fails=_what_fails(reg),
        team_preferences=_team_preferences(reg),
        prompt_insights=_prompt_insights(reg),
        success_patterns=_success_patterns(reg),
    )
    if persist:
        _persist(snap)
    return snap


def latest_snapshot() -> dict | None:
    if not _MEMORY_DIR.exists():
        return None
    snaps = sorted(_MEMORY_DIR.glob("*.json"))
    if not snaps:
        return None
    try:
        return json.loads(snaps[-1].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# ── Internal extractors ────────────────────────────────────────────────


def _what_succeeds(registry: CapabilityRegistry, top_n: int = 10) -> list[dict]:
    rows: list[dict] = []
    runs = workflow_runner.list_runs(limit=5000)
    by_cap: dict[str, list] = defaultdict(list)
    for r in runs:
        by_cap[r.capability_id].append(r)
    by_id = registry.snapshot().by_id()
    for cid, group in by_cap.items():
        succ = sum(1 for r in group if r.status == "succeeded")
        if succ < 3:
            continue
        rep = (reputation_scorer.load_score(cid) or {}).get("reputation_score", 0)
        agg = feedback_store.get_aggregate(cid)
        rows.append({
            "capability_id": cid,
            "name": by_id.get(cid, {}).get("name", cid),
            "successful_runs": succ,
            "reputation_score": rep,
            "feedback_average": agg.get("overall_average"),
        })
    rows.sort(key=lambda r: (r["successful_runs"], r["reputation_score"]), reverse=True)
    return rows[:top_n]


def _what_fails(registry: CapabilityRegistry, top_n: int = 10) -> list[dict]:
    by_id = registry.snapshot().by_id()
    runs = workflow_runner.list_runs(limit=5000)
    grouped: dict[str, list] = defaultdict(list)
    for r in runs:
        grouped[r.capability_id].append(r)
    rows: list[dict] = []
    for cid, group in grouped.items():
        if len(group) < 3:
            continue
        failures = sum(1 for r in group if r.status in ("error", "contract_failed"))
        if not failures:
            continue
        msgs = Counter(
            (r.error_message or "")[:80] for r in group
            if r.status in ("error", "contract_failed")
        )
        top_msg = msgs.most_common(1)[0][0] if msgs else None
        rows.append({
            "capability_id": cid,
            "name": by_id.get(cid, {}).get("name", cid),
            "failures": failures,
            "failure_rate_pct": round(failures / len(group) * 100, 1),
            "most_common_error": top_msg,
        })
    rows.sort(key=lambda r: r["failures"], reverse=True)
    return rows[:top_n]


def _team_preferences(registry: CapabilityRegistry) -> dict:
    """Per-department list of the top capabilities by run count."""
    by_id = registry.snapshot().by_id()
    runs = workflow_runner.list_runs(limit=5000)
    by_dept: dict[str, Counter] = defaultdict(Counter)
    for r in runs:
        if r.status != "succeeded":
            continue
        cap = by_id.get(r.capability_id)
        if not cap:
            continue
        dept = cap.get("category", "Uncategorized")
        by_dept[dept][r.capability_id] += 1
    out: dict[str, list[dict]] = {}
    for dept, counter in by_dept.items():
        out[dept] = [
            {"capability_id": cid,
             "name": by_id.get(cid, {}).get("name", cid),
             "successful_runs": n}
            for cid, n in counter.most_common(5)
        ]
    return out


def _prompt_insights(registry: CapabilityRegistry) -> list[dict]:
    """Pull suggested_enhancements from feedback that mention prompts."""
    by_id = registry.snapshot().by_id()
    out: list[dict] = []
    for cap in registry.snapshot().capabilities:
        records = feedback_store.list_feedback(cap["id"])
        for record in records:
            for s in record.get("suggested_enhancements") or []:
                title = ""
                description = ""
                if isinstance(s, dict):
                    title = s.get("title", "") or ""
                    description = s.get("description", "") or ""
                elif isinstance(s, str):
                    description = s
                blob = f"{title} {description}".lower()
                if any(k in blob for k in ("prompt", "instructions", "tone", "wording", "rewrite")):
                    out.append({
                        "capability_id": cap["id"],
                        "name": by_id.get(cap["id"], {}).get("name", cap["id"]),
                        "title": title,
                        "description": description[:240],
                        "submitted_at": record.get("submitted_at"),
                    })
    return out[:20]


def _success_patterns(registry: CapabilityRegistry) -> list[dict]:
    """Sequences seen in successful pipeline runs, ranked by frequency.
    Roughly: 'these chains tend to succeed end-to-end'."""
    by_id = registry.snapshot().by_id()
    out_counter: Counter = Counter()
    last_seen: dict = {}
    for run in pipeline_engine.list_pipeline_runs(limit=500):
        if run.status != "succeeded":
            continue
        seq = tuple(sr.capability_id for sr in run.step_runs
                    if sr.status in ("succeeded", "retried_succeeded"))
        if len(seq) < 2:
            continue
        out_counter[seq] += 1
        if run.finished_at and run.finished_at > last_seen.get(seq, ""):
            last_seen[seq] = run.finished_at
    rows: list[dict] = []
    for seq, count in out_counter.most_common(15):
        rows.append({
            "sequence": list(seq),
            "names": [by_id.get(c, {}).get("name", c) for c in seq],
            "successful_runs": count,
            "last_seen": last_seen.get(seq, ""),
        })
    return rows


def _persist(snap: MemorySnapshot) -> None:
    _MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = _MEMORY_DIR / f"{stamp}.json"
    path.write_text(json.dumps(snap.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
