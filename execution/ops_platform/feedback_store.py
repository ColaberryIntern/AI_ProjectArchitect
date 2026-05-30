"""Feedback store — ratings, operational notes, suggested enhancements.

Persistence:
- output/ops_platform/feedback/{capability_id}.jsonl     (one record per line)
- output/ops_platform/feedback/_index.json               (per-capability aggregates)

Aggregates are recomputed on every write and pushed into the capability
registry's rating overlay (registry.set_rating_aggregate) so the homepage
and detail pages render the latest numbers without re-reading every record.

Validation: every record is validated against
config/schemas/ops/feedback.schema.json before persistence. Invalid records
are rejected with a list of schema errors.
"""

from __future__ import annotations

import json
import logging
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import jsonschema

from config.settings import OUTPUT_DIR, SCHEMAS_DIR
from execution.ops_platform.capability_registry import CapabilityRegistry, default_registry

logger = logging.getLogger(__name__)

_FEEDBACK_DIR = OUTPUT_DIR / "ops_platform" / "feedback"
_INDEX_PATH = _FEEDBACK_DIR / "_index.json"
_SCHEMA_PATH = SCHEMAS_DIR / "ops" / "feedback.schema.json"


@dataclass
class FeedbackInvalid(Exception):
    errors: list[str]

    def __str__(self) -> str:
        return f"feedback validation failed: {self.errors}"


def _load_schema() -> dict:
    global _SCHEMA_CACHE
    try:
        return _SCHEMA_CACHE
    except NameError:
        with open(_SCHEMA_PATH, "r", encoding="utf-8") as f:
            _SCHEMA_CACHE = json.load(f)
        return _SCHEMA_CACHE


def submit_feedback(
    record: dict,
    *,
    registry: CapabilityRegistry | None = None,
) -> dict:
    """Persist one feedback record. Returns the fully-populated record.

    Auto-fills id and submitted_at if missing.
    """
    record = dict(record)  # copy — never mutate caller's dict
    record.setdefault("id", str(uuid.uuid4()))
    record.setdefault("submitted_at", datetime.now(timezone.utc).isoformat())

    errors = _validate(record)
    if errors:
        raise FeedbackInvalid(errors)

    capability_id = record["capability_id"]
    _FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    path = _FEEDBACK_DIR / f"{capability_id}.jsonl"

    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    aggregate = _recompute_aggregate(capability_id)
    _write_index_entry(capability_id, aggregate)

    # Push the aggregate into the registry so the capability's detail page
    # and the homepage cards see updated ratings.
    try:
        reg = registry or default_registry()
        reg.set_rating_aggregate(capability_id, aggregate)
    except Exception:
        logger.warning("failed to push aggregate into registry", exc_info=True)

    try:
        from execution.ops_platform import cache_bus
        cache_bus.emit(cache_bus.Topic.FEEDBACK_SUBMITTED, {
            "capability_id": capability_id,
            "feedback_id": record.get("id"),
        })
    except Exception:
        logger.warning("cache_bus emit failed for FEEDBACK_SUBMITTED", exc_info=True)

    return record


def list_feedback(capability_id: str) -> list[dict]:
    """Return all feedback records for a capability, newest-first."""
    path = _FEEDBACK_DIR / f"{capability_id}.jsonl"
    if not path.exists():
        return []
    records: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    records.sort(key=lambda r: r.get("submitted_at", ""), reverse=True)
    return records


def get_aggregate(capability_id: str) -> dict:
    """Return the current rating + notes aggregate for a capability."""
    index = _load_index()
    return index.get(capability_id, _empty_aggregate())


def all_aggregates() -> dict[str, dict]:
    return _load_index()


# ── Internal ────────────────────────────────────────────────────────────


def _validate(record: dict) -> list[str]:
    schema = _load_schema()
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(record), key=lambda e: e.absolute_path)
    return [
        f"{'.'.join(str(p) for p in err.absolute_path) or '<root>'}: {err.message}"
        for err in errors
    ]


def _recompute_aggregate(capability_id: str) -> dict:
    records = list_feedback(capability_id)
    if not records:
        return _empty_aggregate()

    rating_sums = {"usefulness": 0, "accuracy": 0, "time_savings": 0, "reliability": 0}
    rating_counts = {"usefulness": 0, "accuracy": 0, "time_savings": 0, "reliability": 0}

    suggestion_count = 0
    notes_count = 0

    for r in records:
        for dim, total in (r.get("ratings") or {}).items():
            if dim in rating_sums and isinstance(total, int):
                rating_sums[dim] += total
                rating_counts[dim] += 1
        if r.get("operational_notes"):
            notes_count += 1
        if r.get("suggested_enhancements"):
            suggestion_count += len(r["suggested_enhancements"])

    averages = {
        dim: (rating_sums[dim] / rating_counts[dim]) if rating_counts[dim] else None
        for dim in rating_sums
    }
    overall_values = [v for v in averages.values() if v is not None]
    overall_avg = round(sum(overall_values) / len(overall_values), 2) if overall_values else None

    return {
        "total_feedback": len(records),
        "averages": {k: (round(v, 2) if v is not None else None) for k, v in averages.items()},
        "overall_average": overall_avg,
        "notes_count": notes_count,
        "suggestion_count": suggestion_count,
        "last_submission": records[0]["submitted_at"] if records else None,
    }


def _empty_aggregate() -> dict:
    return {
        "total_feedback": 0,
        "averages": {"usefulness": None, "accuracy": None, "time_savings": None, "reliability": None},
        "overall_average": None,
        "notes_count": 0,
        "suggestion_count": 0,
        "last_submission": None,
    }


def _load_index() -> dict[str, dict]:
    if not _INDEX_PATH.exists():
        return {}
    try:
        with open(_INDEX_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        logger.warning("feedback index unreadable; resetting", exc_info=True)
        return {}


def _write_index_entry(capability_id: str, aggregate: dict) -> None:
    index = _load_index()
    index[capability_id] = aggregate
    _FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(_FEEDBACK_DIR), suffix=".tmp")
    tmp = Path(tmp_path)
    try:
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2, sort_keys=True)
        tmp.replace(_INDEX_PATH)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
