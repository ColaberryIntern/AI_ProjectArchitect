"""Discovery approval queue — persists discovered workflow patterns and tracks
operator approval state through to publication.

Why
---
Phase 2 returned ``DiscoveredPattern`` objects from a stateless scan but did
nothing with them — operators had to copy-paste manifests into the pipelines
directory to act on a discovery. This module persists each unique discovery
once, tracks its approval state, and offers one-click publication into the
pipeline registry.

States
------
- ``pending``  — newly discovered, awaiting review
- ``approved`` — operator approved, ready to publish
- ``rejected`` — operator declined; suppressed from future discovery scans
- ``published``— pipeline manifest has been written; this row is the audit trail
- ``superseded`` — a newer revision of the same sequence has supplanted this

Storage
-------
JSON file per item under ``output/ops_platform/discovery_queue/{queue_id}.json``.
A tiny ``_index.json`` indexes by content_hash for de-duplication. Index is
recomputed on demand; never load-bearing.

Idempotency
-----------
``record_discovery()`` hashes the sequence (tuple of capability_ids) and
upserts: existing pending/approved rows have their ``occurrences`` /
``last_observed`` refreshed; rejected items are NOT resurrected (operator
already said no); published items are left alone.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution.ops_platform import audit_log, cache_bus, pipeline_engine

logger = logging.getLogger(__name__)

_QUEUE_DIR = OUTPUT_DIR / "ops_platform" / "discovery_queue"
_INDEX_PATH = _QUEUE_DIR / "_index.json"

VALID_STATES = ("pending", "approved", "rejected", "published", "superseded")


@dataclass
class DiscoveryItem:
    queue_id: str
    content_hash: str
    sequence: list[str]
    capability_names: list[str]
    occurrences: int
    distinct_initiators: int
    first_observed: str
    last_observed: str
    state: str = "pending"
    draft_pipeline: dict = field(default_factory=dict)
    published_pipeline_id: str | None = None
    reviewer: str | None = None
    review_notes: str | None = None
    updated_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ── Public API ─────────────────────────────────────────────────────────


def record_discovery(pattern) -> DiscoveryItem:
    """Idempotently persist (or update) a single pattern. Accepts the
    DiscoveredPattern dataclass from workflow_discovery."""
    sequence = list(pattern.sequence)
    content_hash = _hash_sequence(sequence)
    existing = _find_by_hash(content_hash)
    now = datetime.now(timezone.utc).isoformat()

    if existing:
        # Rejected stays rejected, published stays published.
        if existing.state in ("rejected", "published", "superseded"):
            return existing
        existing.occurrences = max(existing.occurrences, pattern.occurrences)
        existing.distinct_initiators = max(existing.distinct_initiators, pattern.distinct_initiators)
        if pattern.last_observed > existing.last_observed:
            existing.last_observed = pattern.last_observed
        existing.updated_at = now
        _persist(existing)
        return existing

    item = DiscoveryItem(
        queue_id=str(uuid.uuid4()),
        content_hash=content_hash,
        sequence=sequence,
        capability_names=list(pattern.capability_names),
        occurrences=pattern.occurrences,
        distinct_initiators=pattern.distinct_initiators,
        first_observed=pattern.last_observed,
        last_observed=pattern.last_observed,
        state="pending",
        draft_pipeline=dict(pattern.draft_pipeline or {}),
        updated_at=now,
    )
    _persist(item)
    _emit()
    return item


def list_items(*, state: str | None = None) -> list[DiscoveryItem]:
    """Return all items, optionally filtered by state. Newest-first."""
    if not _QUEUE_DIR.exists():
        return []
    items: list[DiscoveryItem] = []
    for p in _QUEUE_DIR.glob("*.json"):
        if p.name.startswith("_"):
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            items.append(DiscoveryItem(**data))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    if state:
        items = [i for i in items if i.state == state]
    items.sort(key=lambda i: i.last_observed, reverse=True)
    return items


def get_item(queue_id: str) -> DiscoveryItem | None:
    path = _QUEUE_DIR / f"{queue_id}.json"
    if not path.exists():
        return None
    try:
        return DiscoveryItem(**json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def approve(queue_id: str, *, reviewer: str | None = None, notes: str | None = None) -> DiscoveryItem | None:
    item = get_item(queue_id)
    if item is None or item.state in ("published", "superseded"):
        return item
    previous = item.to_dict()
    item.state = "approved"
    item.reviewer = reviewer
    item.review_notes = notes
    item.updated_at = datetime.now(timezone.utc).isoformat()
    _persist(item)
    audit_log.record(
        action="discovery.approved", entity_type="discovery_item",
        entity_id=item.queue_id, actor=reviewer or "anonymous",
        previous_state={"state": previous["state"]},
        new_state={"state": item.state},
        metadata={"notes": notes} if notes else None,
    )
    _emit()
    return item


def reject(queue_id: str, *, reviewer: str | None = None, notes: str | None = None) -> DiscoveryItem | None:
    item = get_item(queue_id)
    if item is None or item.state in ("published", "superseded"):
        return item
    previous = item.to_dict()
    item.state = "rejected"
    item.reviewer = reviewer
    item.review_notes = notes
    item.updated_at = datetime.now(timezone.utc).isoformat()
    _persist(item)
    audit_log.record(
        action="discovery.rejected", entity_type="discovery_item",
        entity_id=item.queue_id, actor=reviewer or "anonymous",
        previous_state={"state": previous["state"]},
        new_state={"state": item.state},
        metadata={"notes": notes} if notes else None,
    )
    _emit()
    return item


def publish(queue_id: str, *, override_pipeline_id: str | None = None,
            reviewer: str | None = None) -> tuple[DiscoveryItem | None, str | None]:
    """Materialize the draft as a pipeline manifest and persist via pipeline_engine.
    Returns (item, error_message). On success error_message is None."""
    item = get_item(queue_id)
    if item is None:
        return None, "queue item not found"
    if item.state == "published":
        return item, "already published"
    if not item.draft_pipeline:
        return item, "no draft pipeline attached"

    manifest = dict(item.draft_pipeline)
    if override_pipeline_id:
        manifest["pipeline_id"] = override_pipeline_id
    try:
        pipeline_engine.save_pipeline(manifest)
    except (ValueError, OSError) as e:
        return item, str(e)

    previous_state = item.to_dict()
    item.state = "published"
    item.published_pipeline_id = manifest["pipeline_id"]
    item.reviewer = reviewer or item.reviewer
    item.updated_at = datetime.now(timezone.utc).isoformat()
    _persist(item)
    audit_log.record(
        action="discovery.published", entity_type="discovery_item",
        entity_id=item.queue_id, actor=reviewer or "anonymous",
        previous_state={"state": previous_state["state"]},
        new_state={"state": item.state, "pipeline_id": item.published_pipeline_id},
    )
    _emit()
    return item, None


def is_rejected(sequence: list[str]) -> bool:
    """Helper used by workflow_discovery to skip patterns the operator already
    rejected, instead of re-surfacing them each scan."""
    content_hash = _hash_sequence(sequence)
    existing = _find_by_hash(content_hash)
    return existing is not None and existing.state == "rejected"


def queue_stats() -> dict:
    """Counts by state — for the analytics widget."""
    items = list_items()
    counts = {s: 0 for s in VALID_STATES}
    for it in items:
        counts[it.state] = counts.get(it.state, 0) + 1
    counts["total"] = len(items)
    return counts


# ── Internal ───────────────────────────────────────────────────────────


def _hash_sequence(sequence: list[str]) -> str:
    h = hashlib.sha256()
    for cid in sequence:
        h.update(cid.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def _persist(item: DiscoveryItem) -> None:
    _QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    (_QUEUE_DIR / f"{item.queue_id}.json").write_text(
        json.dumps(item.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _find_by_hash(content_hash: str) -> DiscoveryItem | None:
    for item in list_items():
        if item.content_hash == content_hash:
            return item
    return None


def _emit() -> None:
    try:
        cache_bus.emit(cache_bus.Topic.DISCOVERY_UPDATED, {})
    except Exception:
        logger.warning("cache_bus emit failed for DISCOVERY_UPDATED", exc_info=True)
