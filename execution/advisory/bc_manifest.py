"""The plan↔Basecamp manifest: ``output/{slug}/bc_manifest.json``.

The ``project-plan.json`` is pure desired-state and holds NO Basecamp ids (so it
stays portable across BC accounts). The manifest is the side table that maps each
plan ``id`` → the Basecamp object it was materialized into, plus the
``contentHash`` last written (so the reconciler can tell "unchanged" from
"changed") and the per-build ``startDate`` (set once; ``dueOffsetDays`` resolve
against it so absolute dates never pollute the plan's content hash).

BC object mapping: initiative → todolist, list → todolist group, todo → todo.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR

SCHEMA = "cb-bc-manifest/v1"


def _manifest_path(slug: str) -> Path:
    return OUTPUT_DIR / slug / "bc_manifest.json"


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp", prefix=path.name + ".")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False, default=str)
        Path(tmp_path).replace(path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def load_manifest(slug: str) -> dict | None:
    p = _manifest_path(slug)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def ensure_manifest(slug: str, bc_project_id: int, bc_account: str,
                    start_date: str | None = None) -> dict:
    """Load the manifest or create a fresh one. ``start_date`` is set ONCE on
    first creation and never changed afterward (offsets resolve against it)."""
    m = load_manifest(slug)
    if m is None:
        m = {
            "schema": SCHEMA,
            "projectSlug": slug,
            "bcProjectId": int(bc_project_id),
            "bcAccount": str(bc_account),
            "startDate": start_date or datetime.now(timezone.utc).date().isoformat(),
            "planRevision": 0,
            "entries": {},
        }
        save_manifest(slug, m)
    return m


def save_manifest(slug: str, manifest: dict) -> None:
    manifest["updatedAt"] = datetime.now(timezone.utc).isoformat()
    _atomic_write(_manifest_path(slug), manifest)


def get_entry(manifest: dict, node_id: str) -> dict | None:
    return (manifest.get("entries") or {}).get(node_id)


def upsert_entry(manifest: dict, node_id: str, *, bc_type: str, bc_id,
                 content_hash: str, parent_bc_id=None, status: str = "active",
                 **extra) -> dict:
    """Record (or update) the Basecamp materialization of a plan node."""
    entries = manifest.setdefault("entries", {})
    entry = entries.get(node_id, {})
    entry.update({
        "bcType": bc_type,
        "bcId": bc_id,
        "contentHash": content_hash,
        "status": status,
    })
    if parent_bc_id is not None:
        entry["parentBcId"] = parent_bc_id
    entry.update(extra)
    entries[node_id] = entry
    return entry


def mark_retired(manifest: dict, node_id: str) -> None:
    entry = (manifest.get("entries") or {}).get(node_id)
    if entry:
        entry["status"] = "retired"
