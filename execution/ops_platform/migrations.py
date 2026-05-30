"""Schema migration framework — version-tracked, rollback-safe.

Scope honesty
-------------
The platform's persistence is JSON files, not a relational DB. "Migrations"
here mean structural reshapes of the on-disk records — e.g. adding a new
required field with a default, splitting one row into two, renaming a key.

A Migration is a Python module-level pair of functions:

    def up(): ...
    def down(): ...

Plus metadata declared at registration:

    register_migration(
        version="2026.05.0001",
        description="add revision_id to approvals",
        up=lambda: ...,
        down=lambda: ...,
    )

The applied set is persisted to
``output/ops_platform/migrations/applied.json``. ``apply_pending()`` runs
every unapplied migration in ascending version order. ``rollback_one()``
applies the most recent migration's ``down()``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from config.settings import OUTPUT_DIR
from execution.ops_platform import audit_log

logger = logging.getLogger(__name__)

_MIGRATIONS_DIR = OUTPUT_DIR / "ops_platform" / "migrations"
_APPLIED_PATH = _MIGRATIONS_DIR / "applied.json"


@dataclass
class Migration:
    version: str           # e.g. "2026.05.0001"
    description: str
    up: Callable
    down: Callable | None = None

    def to_metadata(self) -> dict:
        return {"version": self.version, "description": self.description,
                  "down_available": self.down is not None}


_REGISTRY: dict[str, Migration] = {}


def register_migration(
    *,
    version: str,
    description: str,
    up: Callable,
    down: Callable | None = None,
) -> Migration:
    if version in _REGISTRY:
        raise ValueError(f"migration version {version} already registered")
    m = Migration(version=version, description=description, up=up, down=down)
    _REGISTRY[version] = m
    return m


def applied_versions() -> list[str]:
    if not _APPLIED_PATH.exists():
        return []
    try:
        return list(json.loads(_APPLIED_PATH.read_text(encoding="utf-8")).get("applied", []))
    except (OSError, json.JSONDecodeError):
        return []


def pending() -> list[Migration]:
    applied = set(applied_versions())
    return sorted([m for v, m in _REGISTRY.items() if v not in applied],
                    key=lambda m: m.version)


def apply_pending(*, actor: dict | str = "migration_runner") -> list[dict]:
    actor_norm = actor if isinstance(actor, dict) else {"name": str(actor), "system": True}
    results: list[dict] = []
    for m in pending():
        try:
            m.up()
        except Exception as e:
            audit_log.record(
                action="migration.failed", entity_type="migration",
                entity_id=m.version, actor=actor_norm,
                metadata={"error": str(e)[:200]},
            )
            results.append({"version": m.version, "applied": False,
                              "error": str(e)})
            break
        _mark_applied(m.version)
        audit_log.record(
            action="migration.applied", entity_type="migration",
            entity_id=m.version, actor=actor_norm,
            new_state=m.to_metadata(),
        )
        results.append({"version": m.version, "applied": True,
                          "description": m.description})
    return results


def rollback_one(*, actor: dict | str = "migration_runner") -> dict:
    applied = applied_versions()
    if not applied:
        return {"rolled_back": False, "reason": "no migrations applied"}
    last = applied[-1]
    m = _REGISTRY.get(last)
    if m is None or m.down is None:
        return {"rolled_back": False, "reason": f"no down() available for {last}"}
    try:
        m.down()
    except Exception as e:
        audit_log.record(
            action="migration.rollback_failed", entity_type="migration",
            entity_id=last,
            actor=actor if isinstance(actor, dict) else {"name": str(actor)},
            metadata={"error": str(e)[:200]},
        )
        return {"rolled_back": False, "error": str(e)}
    _mark_unapplied(last)
    audit_log.record(
        action="migration.rolled_back", entity_type="migration",
        entity_id=last,
        actor=actor if isinstance(actor, dict) else {"name": str(actor)},
        previous_state={"applied": True}, new_state={"applied": False},
    )
    return {"rolled_back": True, "version": last}


def status() -> dict:
    return {
        "registered": [m.to_metadata() for m in _REGISTRY.values()],
        "applied": applied_versions(),
        "pending": [m.version for m in pending()],
    }


# ── Internal ───────────────────────────────────────────────────────────


def _mark_applied(version: str) -> None:
    _MIGRATIONS_DIR.mkdir(parents=True, exist_ok=True)
    data = {"applied": []}
    if _APPLIED_PATH.exists():
        try:
            data = json.loads(_APPLIED_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {"applied": []}
    if version not in data["applied"]:
        data["applied"].append(version)
    _APPLIED_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _mark_unapplied(version: str) -> None:
    if not _APPLIED_PATH.exists():
        return
    try:
        data = json.loads(_APPLIED_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if version in data.get("applied", []):
        data["applied"].remove(version)
    _APPLIED_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
