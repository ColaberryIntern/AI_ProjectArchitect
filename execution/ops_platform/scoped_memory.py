"""Scoped organizational memory — workspace-aware snapshots that prevent
cross-workspace leakage.

Wraps `organizational_memory.build_snapshot` and filters every section to
the capabilities visible from the requesting workspace. Admin identity
bypasses the filter and gets the global snapshot.

Persistence
-----------
``output/ops_platform/org_memory/{workspace_id}/{stamp}.json``

Global snapshots continue to land at the original Phase 3 path
(``output/ops_platform/org_memory/{stamp}.json``).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution.ops_platform import (
    organizational_memory,
    workspaces,
)
from execution.ops_platform.capability_registry import CapabilityRegistry, default_registry
from execution.ops_platform.identity import IdentityContext, anonymous_identity

logger = logging.getLogger(__name__)

_SCOPED_DIR = OUTPUT_DIR / "ops_platform" / "org_memory"


def build_for_workspace(
    workspace_id: str,
    *,
    identity: IdentityContext | None = None,
    persist: bool = True,
    registry: CapabilityRegistry | None = None,
) -> dict:
    """Build a workspace-scoped snapshot. When the identity is admin the
    workspace filter is skipped and the full global snapshot is returned."""
    identity = identity or anonymous_identity()
    reg = registry or default_registry()
    global_snap = organizational_memory.build_snapshot(registry=reg, persist=False)

    if "admin" in identity.roles:
        if persist:
            _persist_global(global_snap.to_dict())
        return global_snap.to_dict()

    ws = workspaces.get_workspace(workspace_id)
    visible_cap_ids: set[str] = set(ws.capability_ids) if ws else set()
    # Plus all global (unowned) capabilities
    owned_anywhere: set[str] = set()
    for other in workspaces.list_workspaces():
        owned_anywhere.update(other.capability_ids)
    for cap in reg.snapshot().capabilities:
        if cap["id"] not in owned_anywhere:
            visible_cap_ids.add(cap["id"])

    filtered = _filter_snapshot(global_snap.to_dict(), visible_cap_ids)
    filtered["workspace_id"] = workspace_id
    filtered["scoped_at"] = datetime.now(timezone.utc).isoformat()
    if persist:
        _persist_workspace(workspace_id, filtered)
    return filtered


def latest_for_workspace(workspace_id: str) -> dict | None:
    target_dir = _SCOPED_DIR / workspace_id
    if not target_dir.exists():
        return None
    files = sorted(target_dir.glob("*.json"))
    if not files:
        return None
    try:
        return json.loads(files[-1].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def workspace_insights(workspace_id: str, *,
                        registry: CapabilityRegistry | None = None) -> dict:
    """Convenience wrapper for the dashboard: scoped memory + workspace meta."""
    ws = workspaces.get_workspace(workspace_id)
    if ws is None:
        return {"error": "workspace not found"}
    snap = build_for_workspace(workspace_id, persist=False, registry=registry)
    return {
        "workspace": ws.to_dict(),
        "memory": snap,
        "capability_count": len(snap.get("what_succeeds", []) or []),
    }


# ── Internal ───────────────────────────────────────────────────────────


def _filter_snapshot(snap: dict, visible: set[str]) -> dict:
    out = dict(snap)
    out["what_succeeds"] = [r for r in snap.get("what_succeeds", [])
                              if r.get("capability_id") in visible]
    out["what_fails"] = [r for r in snap.get("what_fails", [])
                          if r.get("capability_id") in visible]
    out["team_preferences"] = {
        dept: [r for r in caps if r.get("capability_id") in visible]
        for dept, caps in (snap.get("team_preferences", {}) or {}).items()
    }
    out["prompt_insights"] = [r for r in snap.get("prompt_insights", [])
                                if r.get("capability_id") in visible]
    out["success_patterns"] = [
        p for p in snap.get("success_patterns", [])
        if all(cid in visible for cid in (p.get("sequence") or []))
    ]
    return out


def _persist_workspace(workspace_id: str, snap: dict) -> None:
    target_dir = _SCOPED_DIR / workspace_id
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (target_dir / f"{stamp}.json").write_text(
        json.dumps(snap, indent=2, ensure_ascii=False), encoding="utf-8",
    )


def _persist_global(snap: dict) -> None:
    _SCOPED_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (_SCOPED_DIR / f"{stamp}.json").write_text(
        json.dumps(snap, indent=2, ensure_ascii=False), encoding="utf-8",
    )
