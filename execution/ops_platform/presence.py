"""Presence — operator presence tracking per workspace.

Per-workspace JSON files under ``output/ops_platform/presence/{workspace_id}.json``.
Each row carries last_seen_at; stale rows are swept on read.

Anonymous identities never appear in presence (isolated from collaboration).
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution.ops_platform import audit_log, realtime_bus
from execution.ops_platform.identity import IdentityContext

logger = logging.getLogger(__name__)

_PRESENCE_DIR = OUTPUT_DIR / "ops_platform" / "presence"

DEFAULT_TTL_SECONDS = 90


@dataclass
class PresenceEntry:
    user_id: str
    display_name: str
    last_seen_at: str
    currently_viewing: str | None = None      # entity_type:entity_id
    currently_editing: str | None = None      # entity_type:entity_id (mutex held via optimistic_concurrency)
    typing_in: str | None = None              # short label, ephemeral

    def to_dict(self) -> dict:
        return asdict(self)


# ── Public API ─────────────────────────────────────────────────────────


def heartbeat(
    *,
    workspace_id: str,
    identity: IdentityContext,
    currently_viewing: str | None = None,
    currently_editing: str | None = None,
    typing_in: str | None = None,
) -> PresenceEntry | None:
    if not identity.authenticated:
        return None
    rows = _load(workspace_id)
    entry = PresenceEntry(
        user_id=identity.user_id,
        display_name=identity.display_name or identity.user_id,
        last_seen_at=datetime.now(timezone.utc).isoformat(),
        currently_viewing=currently_viewing,
        currently_editing=currently_editing,
        typing_in=typing_in,
    )
    rows[identity.user_id] = entry.to_dict()
    _save(workspace_id, rows)
    realtime_bus.emit(
        "presence.heartbeat", actor=identity.as_actor(),
        workspace_id=workspace_id,
        payload={
            "user_id": identity.user_id,
            "currently_viewing": currently_viewing,
            "currently_editing": currently_editing,
        },
        mirror_to_audit=False,
    )
    return entry


def leave(*, workspace_id: str, identity: IdentityContext) -> bool:
    rows = _load(workspace_id)
    removed = rows.pop(identity.user_id, None) is not None
    if removed:
        _save(workspace_id, rows)
        realtime_bus.emit(
            "presence.left", actor=identity.as_actor(),
            workspace_id=workspace_id,
            payload={"user_id": identity.user_id},
            mirror_to_audit=False,
        )
    return removed


def active_in_workspace(workspace_id: str, *,
                          ttl_seconds: int = DEFAULT_TTL_SECONDS) -> list[dict]:
    rows = _load(workspace_id)
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=ttl_seconds)
    fresh: dict[str, dict] = {}
    for user_id, row in rows.items():
        try:
            last = datetime.fromisoformat(row["last_seen_at"])
        except (KeyError, TypeError, ValueError):
            continue
        if last >= cutoff:
            fresh[user_id] = row
    if len(fresh) != len(rows):
        _save(workspace_id, fresh)
    return list(fresh.values())


def all_active(*, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> dict[str, list[dict]]:
    """Admin-only view: every workspace's active presence."""
    out: dict[str, list[dict]] = {}
    if not _PRESENCE_DIR.exists():
        return out
    for p in _PRESENCE_DIR.glob("*.json"):
        ws_id = p.stem
        out[ws_id] = active_in_workspace(ws_id, ttl_seconds=ttl_seconds)
    return out


# ── Internal ───────────────────────────────────────────────────────────


def _load(workspace_id: str) -> dict:
    path = _PRESENCE_DIR / f"{workspace_id}.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save(workspace_id: str, rows: dict) -> None:
    _PRESENCE_DIR.mkdir(parents=True, exist_ok=True)
    path = _PRESENCE_DIR / f"{workspace_id}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
