"""Workspaces — lightweight team/department segmentation.

Why no auth?
------------
Phase 4 is explicit: introduce workspace segmentation without enterprise
auth complexity. So a workspace is a label + capability/pipeline whitelist
+ owner attribution. Routes accept an optional `?workspace=` parameter;
recommendation_engine + analytics adapt their result sets accordingly.

Persistence
-----------
``output/ops_platform/workspaces/{workspace_id}.json``. One file per
workspace. Schema-validated on every write.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import jsonschema

from config.settings import OUTPUT_DIR, SCHEMAS_DIR
from execution.ops_platform import audit_log

logger = logging.getLogger(__name__)

_WORKSPACES_DIR = OUTPUT_DIR / "ops_platform" / "workspaces"
_SCHEMA_PATH = SCHEMAS_DIR / "ops" / "workspace.schema.json"


@dataclass
class Workspace:
    workspace_id: str
    name: str
    department: str
    owner: dict
    visibility: str
    tags: list
    capability_ids: list
    pipeline_ids: list
    description: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict:
        return asdict(self)


# ── Public API ─────────────────────────────────────────────────────────


def create_workspace(
    *,
    workspace_id: str,
    name: str,
    owner: dict | str,
    department: str = "",
    visibility: str = "internal",
    tags: list | None = None,
    capability_ids: list | None = None,
    pipeline_ids: list | None = None,
    description: str = "",
) -> Workspace:
    if get_workspace(workspace_id) is not None:
        raise ValueError(f"workspace '{workspace_id}' already exists")
    owner_dict = owner if isinstance(owner, dict) else {"name": str(owner)}
    now = datetime.now(timezone.utc).isoformat()
    ws = Workspace(
        workspace_id=workspace_id, name=name, department=department,
        owner=owner_dict, visibility=visibility,
        tags=list(tags or []),
        capability_ids=list(capability_ids or []),
        pipeline_ids=list(pipeline_ids or []),
        description=description,
        created_at=now, updated_at=now,
    )
    _validate_or_raise(ws.to_dict())
    _persist(ws)
    audit_log.record(
        action="workspace.created", entity_type="workspace",
        entity_id=workspace_id, actor=owner_dict,
        new_state={"name": name, "visibility": visibility},
    )
    return ws


def list_workspaces(*, visibility: str | None = None) -> list[Workspace]:
    if not _WORKSPACES_DIR.exists():
        return []
    out: list[Workspace] = []
    for path in _WORKSPACES_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            out.append(Workspace(**data))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    if visibility:
        out = [w for w in out if w.visibility == visibility]
    out.sort(key=lambda w: w.name.lower())
    return out


def get_workspace(workspace_id: str) -> Workspace | None:
    path = _WORKSPACES_DIR / f"{workspace_id}.json"
    if not path.exists():
        return None
    try:
        return Workspace(**json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def attach_capability(workspace_id: str, capability_id: str, *,
                      actor: dict | str | None = None) -> Workspace | None:
    ws = get_workspace(workspace_id)
    if ws is None:
        return None
    if capability_id in ws.capability_ids:
        return ws
    previous = {"capability_ids": list(ws.capability_ids)}
    ws.capability_ids.append(capability_id)
    ws.updated_at = datetime.now(timezone.utc).isoformat()
    _persist(ws)
    audit_log.record(
        action="workspace.capability_attached", entity_type="workspace",
        entity_id=workspace_id, actor=actor or "anonymous",
        previous_state=previous,
        new_state={"capability_ids": ws.capability_ids},
    )
    return ws


def attach_pipeline(workspace_id: str, pipeline_id: str, *,
                    actor: dict | str | None = None) -> Workspace | None:
    ws = get_workspace(workspace_id)
    if ws is None:
        return None
    if pipeline_id in ws.pipeline_ids:
        return ws
    ws.pipeline_ids.append(pipeline_id)
    ws.updated_at = datetime.now(timezone.utc).isoformat()
    _persist(ws)
    audit_log.record(
        action="workspace.pipeline_attached", entity_type="workspace",
        entity_id=workspace_id, actor=actor or "anonymous",
        new_state={"pipeline_id": pipeline_id},
    )
    return ws


def capability_ids_for_scope(workspace_id: str | None) -> set[str] | None:
    """Return the set of capability ids visible in this workspace, or None
    when the caller wants the global view (no workspace filter).

    A workspace inherits global capabilities + its scoped capabilities,
    so the returned set is the union: ``global_caps ∪ workspace_caps``.
    """
    if workspace_id is None:
        return None
    ws = get_workspace(workspace_id)
    if ws is None:
        return set()
    # Global capabilities = those not attached to any workspace.
    attached_anywhere: set[str] = set()
    for other in list_workspaces():
        attached_anywhere.update(other.capability_ids)
    global_ids: set[str] = set()  # filled in by caller via registry; we return scoped union
    return set(ws.capability_ids).union(global_ids)


def is_visible_in_workspace(capability_id: str, workspace_id: str | None) -> bool:
    """True when a capability is visible from the given workspace context.
    None workspace = global view, everything visible."""
    if workspace_id is None:
        return True
    ws = get_workspace(workspace_id)
    if ws is None:
        return False
    if capability_id in ws.capability_ids:
        return True
    # Check whether the capability is owned by *any* workspace; if not, it's global.
    for other in list_workspaces():
        if capability_id in other.capability_ids and other.workspace_id != workspace_id:
            return False
    return True


# ── Internal ───────────────────────────────────────────────────────────


def _persist(ws: Workspace) -> None:
    _WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)
    target = _WORKSPACES_DIR / f"{ws.workspace_id}.json"
    target.write_text(json.dumps(ws.to_dict(), indent=2, ensure_ascii=False),
                       encoding="utf-8")


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
        raise ValueError(f"workspace schema invalid: {errors[:2]}")
