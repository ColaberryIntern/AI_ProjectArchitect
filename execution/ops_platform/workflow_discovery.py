"""Workflow discovery — mines run history for repeated A→B→C patterns and
proposes them as pipeline candidates.

Why: as the platform accumulates runs, certain capability sequences emerge
('summarize → extract action items → email draft' for example). Instead of
relying on operators to spot and codify those, the platform watches and
suggests.

Algorithm (cheap, batch-style):
  1. Pull recent successful runs from workflow_runner.list_runs().
  2. Group runs by initiator (defaults to 'anonymous') and sort by start time.
  3. Slide a window of size N (default 3) across each group; emit each
     observed sequence with its length.
  4. Aggregate identical sequences; count occurrences per distinct
     (initiator-set, sequence) — a sequence the same person ran 5 times is
     more interesting than 5 different people each running it once, but
     both matter.
  5. Filter:
       - min_occurrences (default 3)
       - sequence must have ≥2 distinct capability_ids (skip A→A→A)
       - sequence must not duplicate an existing pipeline manifest
  6. Rank by (occurrences desc, distinct_initiators desc, recency desc).
  7. Each candidate ships with a draft pipeline manifest the operator can
     accept-and-save with one click (the actual save happens elsewhere — this
     module is observation, not mutation).

This module never mutates state. It reads runs + pipelines and returns
``DiscoveredPattern`` objects. Persistence (so the dashboard can show
'we noticed this pattern yesterday') is opt-in via ``snapshot_discoveries()``.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution.ops_platform import pipeline_engine, workflow_runner
from execution.ops_platform.capability_registry import CapabilityRegistry, default_registry

logger = logging.getLogger(__name__)

_DISCOVERY_DIR = OUTPUT_DIR / "ops_platform" / "discoveries"

# Defaults tuned for a small/medium org. Bigger orgs will want to raise
# min_occurrences so the candidate list stays curated.
DEFAULT_WINDOW = 3
DEFAULT_MIN_OCCURRENCES = 3
DEFAULT_MAX_RUNS_SCANNED = 5000


@dataclass
class DiscoveredPattern:
    sequence: list[str]                  # ordered list of capability_ids
    occurrences: int
    distinct_initiators: int
    last_observed: str
    capability_names: list[str] = field(default_factory=list)
    draft_pipeline: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


# ── Public API ─────────────────────────────────────────────────────────


def discover_patterns(
    *,
    window: int = DEFAULT_WINDOW,
    min_occurrences: int = DEFAULT_MIN_OCCURRENCES,
    max_runs: int = DEFAULT_MAX_RUNS_SCANNED,
    registry: CapabilityRegistry | None = None,
    top_n: int | None = 25,
) -> list[DiscoveredPattern]:
    """Scan recent runs and return candidate patterns above min_occurrences.

    ``window`` controls sequence length. window=3 means we look for A→B→C
    triplets; the loop also emits the contained pairs (A→B and B→C) so the
    pattern surface includes 2-step pipelines.
    """
    reg = registry or default_registry()
    by_id = reg.snapshot().by_id()

    runs = workflow_runner.list_runs(limit=max_runs)
    runs = [r for r in runs if r.status == "succeeded"]
    runs.sort(key=lambda r: r.started_at)

    sequences: Counter[tuple[str, ...]] = Counter()
    initiators_per_seq: dict[tuple[str, ...], set[str]] = defaultdict(set)
    last_seen: dict[tuple[str, ...], str] = {}

    grouped = _group_by_initiator(runs)

    for initiator, group in grouped.items():
        cap_seq = [r.capability_id for r in group]
        starts = [r.started_at for r in group]
        for size in range(2, max(2, window) + 1):
            for i in range(0, len(cap_seq) - size + 1):
                seq = tuple(cap_seq[i:i + size])
                if len({*seq}) < 2:
                    # A→A→A is not a pipeline candidate.
                    continue
                sequences[seq] += 1
                initiators_per_seq[seq].add(initiator)
                seq_last = starts[i + size - 1]
                if seq_last > last_seen.get(seq, ""):
                    last_seen[seq] = seq_last

    if not sequences:
        return []

    existing_pipelines = _existing_pipeline_sequences()
    # Lazy import to avoid circular dependency.
    try:
        from execution.ops_platform import discovery_queue
        is_rejected = discovery_queue.is_rejected
    except Exception:
        def is_rejected(_seq):
            return False

    candidates: list[DiscoveredPattern] = []
    for seq, count in sequences.items():
        if count < min_occurrences:
            continue
        if seq in existing_pipelines:
            continue
        if is_rejected(list(seq)):
            continue
        names = [by_id.get(cid, {}).get("name", cid) for cid in seq]
        draft = _draft_pipeline_manifest(seq, names)
        candidates.append(DiscoveredPattern(
            sequence=list(seq),
            occurrences=count,
            distinct_initiators=len(initiators_per_seq[seq]),
            last_observed=last_seen.get(seq, ""),
            capability_names=names,
            draft_pipeline=draft,
        ))

    candidates.sort(key=lambda p: (p.occurrences, p.distinct_initiators, p.last_observed),
                    reverse=True)
    return candidates[:top_n] if top_n else candidates


def record_to_queue(patterns: list[DiscoveredPattern]) -> int:
    """Push every pattern into the discovery_queue (idempotent upsert)."""
    try:
        from execution.ops_platform import discovery_queue
    except Exception:
        return 0
    n = 0
    for p in patterns:
        try:
            discovery_queue.record_discovery(p)
            n += 1
        except Exception:
            logger.warning("record_to_queue failed for sequence=%s", p.sequence, exc_info=True)
    return n


def snapshot_discoveries(patterns: list[DiscoveredPattern]) -> Path:
    """Persist the current discovery set so the analytics dashboard can show
    'discovered today vs last week'. Returns the snapshot path."""
    _DISCOVERY_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = _DISCOVERY_DIR / f"{stamp}.json"
    target.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "patterns": [p.to_dict() for p in patterns],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return target


def latest_snapshot() -> dict | None:
    """Return the most recent persisted discovery snapshot, or None."""
    if not _DISCOVERY_DIR.exists():
        return None
    snaps = sorted(_DISCOVERY_DIR.glob("*.json"))
    if not snaps:
        return None
    try:
        return json.loads(snaps[-1].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# ── Internal ───────────────────────────────────────────────────────────


def _group_by_initiator(runs: list) -> dict[str, list]:
    grouped: dict[str, list] = defaultdict(list)
    for r in runs:
        initiator = (r.inputs.get("__initiator") if isinstance(r.inputs, dict) else None) or "anonymous"
        grouped[initiator].append(r)
    for group in grouped.values():
        group.sort(key=lambda r: r.started_at)
    return grouped


def _existing_pipeline_sequences() -> set[tuple[str, ...]]:
    """Return the set of capability-id sequences already represented by
    persisted pipeline manifests, so we don't propose duplicates."""
    out: set[tuple[str, ...]] = set()
    for manifest in pipeline_engine.list_pipelines():
        seq = tuple(
            s.get("capability_id", "") for s in manifest.get("steps") or []
            if s.get("capability_id")
        )
        if len(seq) >= 2:
            out.add(seq)
    return out


def _draft_pipeline_manifest(sequence: tuple[str, ...], names: list[str]) -> dict:
    """Build a one-click pipeline manifest the operator can save as-is.
    Follows the pipeline_manifest.schema.json contract."""
    slug = "discovered-" + "-".join(_slugify(n)[:20] for n in names)[:80]
    return {
        "pipeline_id": slug,
        "name": " -> ".join(names),
        "description": (
            "Auto-discovered from repeated user activity. Review and adjust "
            "step input bindings before publishing."
        ),
        "version": "0.1.0",
        "created_by": {"name": "workflow_discovery", "team": "Operations Platform"},
        "tags": ["discovered", "auto-suggested"],
        "execution_strategy": "sequential",
        "steps": [
            {
                "step_id": f"step_{i + 1}",
                "capability_id": cid,
                "depends_on": ([f"step_{i}"] if i > 0 else []),
                "on_failure": "abort",
                "input_bindings": {},
            }
            for i, cid in enumerate(sequence)
        ],
    }


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    return _SLUG_RE.sub("-", text.lower()).strip("-")
