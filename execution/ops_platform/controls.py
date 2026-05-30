"""Operational controls — enterprise runtime safety levers.

Control kinds
-------------
- ``freeze``         — capability cannot be invoked until unfrozen
- ``quarantine``     — capability cannot be invoked AND is hidden from
                       recommendations/search (full isolation)
- ``workspace_suspend`` — every capability scoped to the workspace is
                          treated as frozen
- ``maintenance_mode`` — global toggle blocks every runtime call
- ``rate_limit``     — per-capability max calls per window (lightweight)

State
-----
Persisted as one JSON per active control under
``output/ops_platform/controls/{control_id}.json``. Removing a control
removes the file. Every change emits an audit row.

Runtime check
-------------
``workflow_runner.run_workflow`` calls ``is_blocked(capability_id)`` once
before executing. Cheap — single directory scan + small per-capability cache.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution.ops_platform import audit_log

logger = logging.getLogger(__name__)

_CONTROLS_DIR = OUTPUT_DIR / "ops_platform" / "controls"

VALID_KINDS = ("freeze", "quarantine", "workspace_suspend", "maintenance_mode", "rate_limit")


@dataclass
class Control:
    control_id: str
    kind: str
    target_type: str          # "capability" | "workspace" | "global"
    target_id: str
    actor: dict
    reason: str
    activated_at: str
    expires_at: str | None = None
    rate_limit_max: int | None = None     # for rate_limit
    rate_limit_window_seconds: int | None = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ── Public API ─────────────────────────────────────────────────────────


def freeze(capability_id: str, *, actor: dict | str = "anonymous",
            reason: str = "operator freeze") -> Control:
    return _apply(kind="freeze", target_type="capability", target_id=capability_id,
                   actor=actor, reason=reason, audit_action="controls.frozen")


def unfreeze(capability_id: str, *, actor: dict | str = "anonymous",
              reason: str = "operator unfreeze") -> bool:
    return _remove(target_type="capability", target_id=capability_id,
                    kinds=("freeze",), actor=actor, reason=reason,
                    audit_action="controls.unfrozen")


def quarantine(capability_id: str, *, actor: dict | str = "anonymous",
                reason: str = "operator quarantine") -> Control:
    return _apply(kind="quarantine", target_type="capability", target_id=capability_id,
                   actor=actor, reason=reason, audit_action="controls.quarantined")


def release_quarantine(capability_id: str, *, actor: dict | str = "anonymous",
                        reason: str = "operator release") -> bool:
    return _remove(target_type="capability", target_id=capability_id,
                    kinds=("quarantine",), actor=actor, reason=reason,
                    audit_action="controls.unfrozen")


def suspend_workspace(workspace_id: str, *, actor: dict | str = "anonymous",
                       reason: str = "operator workspace suspend") -> Control:
    return _apply(kind="workspace_suspend", target_type="workspace",
                   target_id=workspace_id, actor=actor, reason=reason,
                   audit_action="controls.frozen")


def enable_maintenance_mode(*, actor: dict | str = "anonymous",
                              reason: str = "operator maintenance") -> Control:
    return _apply(kind="maintenance_mode", target_type="global",
                   target_id="global", actor=actor, reason=reason,
                   audit_action="controls.frozen")


def disable_maintenance_mode(*, actor: dict | str = "anonymous",
                                reason: str = "operator resume") -> bool:
    return _remove(target_type="global", target_id="global",
                    kinds=("maintenance_mode",), actor=actor, reason=reason,
                    audit_action="controls.unfrozen")


def set_rate_limit(capability_id: str, *, max_calls: int, window_seconds: int,
                    actor: dict | str = "anonymous", reason: str = "rate limit set") -> Control:
    return _apply(
        kind="rate_limit", target_type="capability", target_id=capability_id,
        actor=actor, reason=reason, audit_action="controls.frozen",
        rate_limit_max=max_calls, rate_limit_window_seconds=window_seconds,
    )


def emergency_rollback(capability_id: str, *, target_version_id: str,
                        actor: dict | str = "anonymous",
                        reason: str = "emergency rollback") -> dict:
    """Convenience: freeze the capability, rollback to a known-good version,
    then unfreeze. Three audit rows under one correlation_id."""
    from execution.ops_platform import capability_versions
    cid = str(uuid.uuid4())
    freeze(capability_id, actor=actor, reason=f"emergency rollback start: {reason}")
    rolled = capability_versions.rollback(capability_id,
                                            target_version_id=target_version_id,
                                            actor=actor)
    unfreeze(capability_id, actor=actor, reason="emergency rollback complete")
    audit_log.record(
        action="controls.rollback", entity_type="capability",
        entity_id=capability_id, actor=_normalize_actor(actor),
        metadata={"target_version_id": target_version_id, "reason": reason,
                  "rolled_to_status": rolled.status if rolled else None},
        correlation_id=cid,
    )
    return {
        "capability_id": capability_id,
        "rolled_to_version_id": target_version_id,
        "correlation_id": cid,
        "rolled_status": rolled.status if rolled else None,
    }


def list_active(*, target_type: str | None = None) -> list[Control]:
    if not _CONTROLS_DIR.exists():
        return []
    out: list[Control] = []
    for p in _CONTROLS_DIR.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            ctrl = Control(**data)
            if ctrl.expires_at:
                try:
                    if datetime.fromisoformat(ctrl.expires_at) < datetime.now(timezone.utc):
                        p.unlink()
                        continue
                except ValueError:
                    pass
            if target_type and ctrl.target_type != target_type:
                continue
            out.append(ctrl)
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    out.sort(key=lambda c: c.activated_at, reverse=True)
    return out


def is_blocked(capability_id: str, *, workspace_id: str | None = None) -> str | None:
    """Returns the blocking reason string or None when execution is allowed."""
    for ctrl in list_active():
        if ctrl.kind == "maintenance_mode":
            return "maintenance mode enabled globally"
        if ctrl.kind in ("freeze", "quarantine") and ctrl.target_id == capability_id:
            return f"{ctrl.kind}: {ctrl.reason}"
        if ctrl.kind == "workspace_suspend" and workspace_id and ctrl.target_id == workspace_id:
            return f"workspace '{workspace_id}' suspended: {ctrl.reason}"
        if ctrl.kind == "rate_limit" and ctrl.target_id == capability_id:
            if _rate_limit_exceeded(ctrl):
                return f"rate limit {ctrl.rate_limit_max}/{ctrl.rate_limit_window_seconds}s exceeded"
    return None


def is_hidden(capability_id: str) -> bool:
    """Quarantine hides the capability from recommendations + search."""
    for ctrl in list_active():
        if ctrl.kind == "quarantine" and ctrl.target_id == capability_id:
            return True
    return False


# ── Internal ───────────────────────────────────────────────────────────


def _apply(*, kind: str, target_type: str, target_id: str,
            actor, reason: str, audit_action: str,
            rate_limit_max: int | None = None,
            rate_limit_window_seconds: int | None = None) -> Control:
    if kind not in VALID_KINDS:
        raise ValueError(f"unknown control kind {kind}")
    actor_norm = _normalize_actor(actor)
    ctrl = Control(
        control_id=str(uuid.uuid4()),
        kind=kind, target_type=target_type, target_id=target_id,
        actor=actor_norm, reason=reason,
        activated_at=datetime.now(timezone.utc).isoformat(),
        rate_limit_max=rate_limit_max,
        rate_limit_window_seconds=rate_limit_window_seconds,
    )
    _persist(ctrl)
    audit_log.record(
        action=audit_action, entity_type=target_type,
        entity_id=target_id, actor=actor_norm,
        new_state={"control_id": ctrl.control_id, "kind": kind, "reason": reason},
    )
    return ctrl


def _remove(*, target_type: str, target_id: str, kinds: tuple,
             actor, reason: str, audit_action: str) -> bool:
    if not _CONTROLS_DIR.exists():
        return False
    removed = False
    actor_norm = _normalize_actor(actor)
    for p in list(_CONTROLS_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (data.get("target_type") == target_type
                and data.get("target_id") == target_id
                and data.get("kind") in kinds):
            try:
                p.unlink()
                removed = True
                audit_log.record(
                    action=audit_action, entity_type=target_type,
                    entity_id=target_id, actor=actor_norm,
                    previous_state={"kind": data.get("kind")},
                    metadata={"reason": reason, "control_id": data.get("control_id")},
                )
            except OSError:
                continue
    return removed


def _normalize_actor(actor) -> dict:
    if isinstance(actor, dict):
        out = dict(actor); out.setdefault("name", "anonymous"); return out
    return {"name": str(actor)}


def _persist(ctrl: Control) -> None:
    _CONTROLS_DIR.mkdir(parents=True, exist_ok=True)
    (_CONTROLS_DIR / f"{ctrl.control_id}.json").write_text(
        json.dumps(ctrl.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8",
    )


_RATE_LIMIT_HITS: dict[str, list[float]] = {}


def _rate_limit_exceeded(ctrl: Control) -> bool:
    """Lightweight rolling-window counter. Process-local; multi-worker users
    would need a shared store. For Phase 5 single-host scope this is fine."""
    if not (ctrl.rate_limit_max and ctrl.rate_limit_window_seconds):
        return False
    now = time.time()
    cutoff = now - ctrl.rate_limit_window_seconds
    hits = _RATE_LIMIT_HITS.setdefault(ctrl.target_id, [])
    # Trim expired hits
    fresh = [t for t in hits if t > cutoff]
    fresh.append(now)
    _RATE_LIMIT_HITS[ctrl.target_id] = fresh
    return len(fresh) > ctrl.rate_limit_max
