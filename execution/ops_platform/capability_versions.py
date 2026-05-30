"""Capability versioning — first-class lifecycle management for capability
revisions.

A "capability" in the registry is now the live name. Each named capability
can have multiple **versions** living side-by-side, each with its own
semver, status, manifest snapshot, prompt snapshot, and rollout %.

Status lifecycle
----------------
   draft ──── promote ────▶ experimental ──── promote ────▶ approved
                                                                │
                                                                ▼
                                                          deprecated ────▶ archived
   (any state)               ◀────── rollback ──────       (any state)

Rollout routing
---------------
The `resolve_version_for_call()` helper picks which version a caller
should run, given the request context. The simple rule v1:

  - approved version absorbs (100 − Σ experimental.rollout_percentage)% of traffic
  - each experimental version gets its declared rollout_percentage
  - draft / deprecated / archived versions are not routed by default

Persistence
-----------
``output/ops_platform/capability_versions/{capability_id}/{version_id}.json``

The latest registry-merged capability is also pointed to by the active
``approved`` row's manifest_snapshot, so the live behavior is always
reproducible from disk even when the plugin tree changes.
"""

from __future__ import annotations

import json
import logging
import random
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import jsonschema

from config.settings import OUTPUT_DIR, SCHEMAS_DIR
from execution.ops_platform import audit_log, cache_bus
from execution.ops_platform.capability_registry import CapabilityRegistry, default_registry

logger = logging.getLogger(__name__)

_VERSIONS_DIR = OUTPUT_DIR / "ops_platform" / "capability_versions"
_SCHEMA_PATH = SCHEMAS_DIR / "ops" / "capability_version.schema.json"

VALID_STATUS = ("draft", "experimental", "approved", "deprecated", "archived")


@dataclass
class CapabilityVersion:
    version_id: str
    capability_id: str
    semver: str
    status: str
    parent_version_id: str | None
    changelog: str
    migration_notes: str
    compatibility_notes: str
    rollout_percentage: float
    created_by: dict
    created_at: str
    approved_by: dict | None
    approval_timestamp: str | None
    deprecated_at: str | None
    manifest_snapshot: dict
    prompt_snapshot: str | None
    tags: list = field(default_factory=list)
    revision_id: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


# ── Public API ─────────────────────────────────────────────────────────


def register_version(
    capability_id: str,
    *,
    semver: str,
    changelog: str,
    created_by: dict | str,
    parent_version_id: str | None = None,
    status: str = "draft",
    rollout_percentage: float = 0.0,
    migration_notes: str = "",
    compatibility_notes: str = "",
    manifest_snapshot: dict | None = None,
    prompt_snapshot: str | None = None,
    tags: list | None = None,
    registry: CapabilityRegistry | None = None,
) -> CapabilityVersion:
    """Create a new version row. Defaults `manifest_snapshot` to a deep
    snapshot of the live registry entry when not supplied."""
    if status not in VALID_STATUS:
        raise ValueError(f"status must be one of {VALID_STATUS}")
    actor = _normalize_actor(created_by)

    if manifest_snapshot is None:
        reg = registry or default_registry()
        cap = reg.get(capability_id)
        if cap is None:
            raise ValueError(f"capability '{capability_id}' is not registered")
        manifest_snapshot = {k: v for k, v in cap.items() if k != "_meta"}

    version = CapabilityVersion(
        version_id=str(uuid.uuid4()),
        capability_id=capability_id,
        semver=semver,
        status=status,
        parent_version_id=parent_version_id,
        changelog=changelog,
        migration_notes=migration_notes,
        compatibility_notes=compatibility_notes,
        rollout_percentage=float(rollout_percentage),
        created_by=actor,
        created_at=datetime.now(timezone.utc).isoformat(),
        approved_by=None,
        approval_timestamp=None,
        deprecated_at=None,
        manifest_snapshot=manifest_snapshot,
        prompt_snapshot=prompt_snapshot,
        tags=list(tags or []),
    )
    _validate_or_raise(version.to_dict())
    _persist(version)
    audit_log.record(
        action="capability_version.created", entity_type="capability_version",
        entity_id=version.version_id, actor=actor,
        new_state={"capability_id": capability_id, "semver": semver, "status": status},
    )
    cache_bus.emit(cache_bus.Topic.REGISTRY_REFRESHED, {"capability_id": capability_id})
    return version


def list_versions(capability_id: str) -> list[CapabilityVersion]:
    """Return all versions for a capability, newest semver first."""
    cap_dir = _VERSIONS_DIR / capability_id
    if not cap_dir.exists():
        return []
    out: list[CapabilityVersion] = []
    for path in cap_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            out.append(CapabilityVersion(**data))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    out.sort(key=lambda v: _semver_tuple(v.semver), reverse=True)
    return out


def get_version(version_id: str) -> CapabilityVersion | None:
    if not _VERSIONS_DIR.exists():
        return None
    for cap_dir in _VERSIONS_DIR.iterdir():
        if not cap_dir.is_dir():
            continue
        target = cap_dir / f"{version_id}.json"
        if target.exists():
            try:
                return CapabilityVersion(**json.loads(target.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError, TypeError):
                return None
    return None


def promote(version_id: str, *, target_status: str,
            approver: dict | str | None = None,
            rollout_percentage: float | None = None) -> CapabilityVersion | None:
    """Move a version forward in the lifecycle. Only valid forward transitions
    are allowed (draft → experimental → approved → deprecated → archived)."""
    if target_status not in VALID_STATUS:
        raise ValueError(f"target_status must be one of {VALID_STATUS}")
    version = get_version(version_id)
    if version is None:
        return None
    if not _is_forward_transition(version.status, target_status):
        raise ValueError(f"cannot move from {version.status} to {target_status}")
    previous = {"status": version.status, "rollout_percentage": version.rollout_percentage}
    actor = _normalize_actor(approver)

    # If we're approving, demote any existing approved version to deprecated.
    if target_status == "approved":
        for v in list_versions(version.capability_id):
            if v.version_id == version.version_id:
                continue
            if v.status == "approved":
                v.status = "deprecated"
                v.deprecated_at = datetime.now(timezone.utc).isoformat()
                _persist(v)
                audit_log.record(
                    action="capability_version.deprecated", entity_type="capability_version",
                    entity_id=v.version_id, actor=actor,
                    previous_state={"status": "approved"},
                    new_state={"status": "deprecated"},
                    metadata={"superseded_by": version.version_id},
                )

    version.status = target_status
    if target_status == "approved":
        version.approved_by = actor
        version.approval_timestamp = datetime.now(timezone.utc).isoformat()
        # Approved version absorbs 100% minus experimentals' total
        experimental_total = sum(
            v.rollout_percentage for v in list_versions(version.capability_id)
            if v.status == "experimental" and v.version_id != version.version_id
        )
        version.rollout_percentage = max(0.0, 100.0 - experimental_total)
    if rollout_percentage is not None:
        version.rollout_percentage = float(rollout_percentage)
    if target_status == "deprecated":
        version.deprecated_at = datetime.now(timezone.utc).isoformat()

    _persist(version)
    audit_log.record(
        action="capability_version.promoted", entity_type="capability_version",
        entity_id=version.version_id, actor=actor,
        previous_state=previous,
        new_state={"status": version.status, "rollout_percentage": version.rollout_percentage},
    )
    cache_bus.emit(cache_bus.Topic.REGISTRY_REFRESHED, {"capability_id": version.capability_id})
    return version


def rollback(capability_id: str, *, target_version_id: str,
              actor: dict | str | None = None) -> CapabilityVersion | None:
    """Roll back: demote the current approved version and re-approve the
    target_version_id. Reversible: the audit trail records both steps."""
    target = get_version(target_version_id)
    if target is None or target.capability_id != capability_id:
        return None
    versions = list_versions(capability_id)
    actor_dict = _normalize_actor(actor)

    for v in versions:
        if v.status == "approved" and v.version_id != target.version_id:
            previous = {"status": v.status}
            v.status = "deprecated"
            v.deprecated_at = datetime.now(timezone.utc).isoformat()
            _persist(v)
            audit_log.record(
                action="capability_version.deprecated", entity_type="capability_version",
                entity_id=v.version_id, actor=actor_dict,
                previous_state=previous, new_state={"status": v.status},
                metadata={"reason": "rollback", "rolled_back_to": target.version_id},
            )

    previous = {"status": target.status, "rollout_percentage": target.rollout_percentage}
    target.status = "approved"
    target.approved_by = actor_dict
    target.approval_timestamp = datetime.now(timezone.utc).isoformat()
    target.deprecated_at = None
    target.rollout_percentage = 100.0
    _persist(target)
    audit_log.record(
        action="rollback.executed", entity_type="capability_version",
        entity_id=target.version_id, actor=actor_dict,
        previous_state=previous,
        new_state={"status": target.status, "rollout_percentage": 100.0},
    )
    cache_bus.emit(cache_bus.Topic.REGISTRY_REFRESHED, {"capability_id": capability_id})
    return target


def compare(v1_id: str, v2_id: str) -> dict:
    """Side-by-side comparison of two versions. Field-level diff for the
    manifest_snapshot plus diff metrics for the prompt body."""
    a = get_version(v1_id)
    b = get_version(v2_id)
    if a is None or b is None:
        return {"error": "version not found"}
    manifest_diff = _shallow_dict_diff(a.manifest_snapshot, b.manifest_snapshot)
    prompt_diff_summary = _prompt_diff_summary(a.prompt_snapshot, b.prompt_snapshot)
    return {
        "v1": {"version_id": a.version_id, "semver": a.semver, "status": a.status},
        "v2": {"version_id": b.version_id, "semver": b.semver, "status": b.status},
        "manifest_diff": manifest_diff,
        "prompt_diff_summary": prompt_diff_summary,
    }


def resolve_version_for_call(capability_id: str) -> CapabilityVersion | None:
    """Pick a version for a fresh call. Honors rollout_percentage when
    experimentals exist. Returns None when no approved version exists yet
    (caller should fall through to the live registry capability)."""
    versions = [v for v in list_versions(capability_id)
                if v.status in ("approved", "experimental")]
    if not versions:
        return None
    approved = next((v for v in versions if v.status == "approved"), None)
    experimentals = [v for v in versions if v.status == "experimental"]
    roll = random.uniform(0.0, 100.0)
    cumulative = 0.0
    for v in experimentals:
        cumulative += v.rollout_percentage
        if roll <= cumulative:
            return v
    return approved


def latest_stable(capability_id: str) -> CapabilityVersion | None:
    for v in list_versions(capability_id):
        if v.status == "approved":
            return v
    return None


# ── Internal ───────────────────────────────────────────────────────────


_FORWARD_TRANSITIONS = {
    "draft": {"experimental", "approved", "archived"},
    "experimental": {"approved", "deprecated", "archived"},
    "approved": {"deprecated", "archived"},
    "deprecated": {"archived"},
    "archived": set(),
}


def _is_forward_transition(current: str, target: str) -> bool:
    return target in _FORWARD_TRANSITIONS.get(current, set())


def _normalize_actor(actor) -> dict:
    if isinstance(actor, dict):
        out = dict(actor)
        out.setdefault("name", "anonymous")
        return out
    if isinstance(actor, str):
        return {"name": actor}
    return {"name": "anonymous", "system": True}


def _semver_tuple(s: str) -> tuple:
    try:
        return tuple(int(p) for p in s.split("."))
    except ValueError:
        return (0, 0, 0)


def _persist(version: CapabilityVersion) -> None:
    from execution.ops_platform import optimistic_concurrency
    version.revision_id = optimistic_concurrency.new_revision()
    target_dir = _VERSIONS_DIR / version.capability_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{version.version_id}.json"
    target.write_text(json.dumps(version.to_dict(), indent=2, ensure_ascii=False),
                       encoding="utf-8")


def save_with_revision_check(version: CapabilityVersion, *,
                                observed_revision: str | None,
                                actor: dict | str | None = None) -> CapabilityVersion:
    from execution.ops_platform import optimistic_concurrency
    current = get_version(version.version_id)
    optimistic_concurrency.compare(
        entity_type="capability_version", entity_id=version.version_id,
        observed_revision=observed_revision,
        current_revision=current.revision_id if current else None,
        actor=actor,
    )
    _persist(version)
    return version


_SCHEMA_CACHE: dict | None = None


def _load_schema() -> dict:
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is None:
        with open(_SCHEMA_PATH, "r", encoding="utf-8") as f:
            _SCHEMA_CACHE = json.load(f)
    return _SCHEMA_CACHE


def _validate_or_raise(payload: dict) -> None:
    schema = _load_schema()
    validator = jsonschema.Draft202012Validator(schema)
    errors = [
        f"{'.'.join(str(p) for p in err.absolute_path) or '<root>'}: {err.message}"
        for err in sorted(validator.iter_errors(payload), key=lambda e: e.absolute_path)
    ]
    if errors:
        raise ValueError(f"capability_version schema invalid: {errors[:2]}")


def _shallow_dict_diff(a: dict, b: dict) -> dict:
    """Return {added, removed, changed} for the top-level fields."""
    added = {k: b[k] for k in b if k not in a}
    removed = {k: a[k] for k in a if k not in b}
    changed = {k: {"from": a[k], "to": b[k]} for k in a if k in b and a[k] != b[k]}
    return {"added": added, "removed": removed, "changed": changed}


def _prompt_diff_summary(a: str | None, b: str | None) -> dict:
    if a is None and b is None:
        return {"both_missing": True}
    a = a or ""
    b = b or ""
    return {
        "v1_char_count": len(a),
        "v2_char_count": len(b),
        "v1_word_count": len(a.split()),
        "v2_word_count": len(b.split()),
        "delta_chars": len(b) - len(a),
        "delta_words": len(b.split()) - len(a.split()),
    }
