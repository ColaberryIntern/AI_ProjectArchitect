"""Change requests — formal record of a proposed mutation that requires
approval before execution.

Lifecycle:
   draft → submitted → approved → executed → closed
                  → rejected
                  → cancelled

Persistence
-----------
``output/ops_platform/change_requests/{cr_id}.json``

Wires approvals + audit + (optionally) optimistic_concurrency for the
target entity.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution.ops_platform import approvals, audit_log

logger = logging.getLogger(__name__)

_CR_DIR = OUTPUT_DIR / "ops_platform" / "change_requests"


@dataclass
class ChangeRequest:
    cr_id: str
    title: str
    action: str                     # e.g. "version.promote", "pipeline.publish"
    entity_type: str
    entity_id: str
    proposed_change: dict           # free-form payload describing the diff
    rollback_plan: str
    requested_by: dict
    state: str                      # draft | submitted | approved | rejected | executed | cancelled | closed
    created_at: str
    updated_at: str
    correlation_id: str
    approval_request_id: str | None = None
    linked_incident_ids: list = field(default_factory=list)
    linked_experiment_ids: list = field(default_factory=list)
    execution_window_start: str | None = None
    execution_window_end: str | None = None
    executed_at: str | None = None
    execution_ref: str | None = None
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ── Public API ─────────────────────────────────────────────────────────


def draft(
    *,
    title: str,
    action: str,
    entity_type: str,
    entity_id: str,
    proposed_change: dict,
    rollback_plan: str,
    requested_by: dict | str,
    linked_incident_ids: list | None = None,
    linked_experiment_ids: list | None = None,
    execution_window_start: str | None = None,
    execution_window_end: str | None = None,
    notes: str = "",
) -> ChangeRequest:
    actor = requested_by if isinstance(requested_by, dict) else {"name": str(requested_by)}
    cr = ChangeRequest(
        cr_id=f"CR-{uuid.uuid4().hex[:10].upper()}",
        title=title, action=action,
        entity_type=entity_type, entity_id=entity_id,
        proposed_change=dict(proposed_change),
        rollback_plan=rollback_plan,
        requested_by=actor,
        state="draft",
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
        correlation_id=str(uuid.uuid4()),
        linked_incident_ids=list(linked_incident_ids or []),
        linked_experiment_ids=list(linked_experiment_ids or []),
        execution_window_start=execution_window_start,
        execution_window_end=execution_window_end,
        notes=notes,
    )
    _persist(cr)
    audit_log.record(
        action="change_request.drafted", entity_type="change_request",
        entity_id=cr.cr_id, actor=actor,
        correlation_id=cr.correlation_id,
        new_state={"title": title, "action": action, "entity_id": entity_id},
    )
    return cr


def submit(
    cr_id: str,
    *,
    single_approver_roles: list | None = None,
    quorum: int = 1,
    ttl_hours: int = 48,
    actor: dict | str = "anonymous",
) -> ChangeRequest | None:
    cr = get(cr_id)
    if cr is None or cr.state != "draft":
        return cr
    appr = approvals.request_approval(
        action=cr.action, entity_type=cr.entity_type, entity_id=cr.entity_id,
        requested_by=cr.requested_by,
        single_approver_roles=single_approver_roles or ["admin"],
        quorum=quorum,
        reason=cr.title,
        metadata={"change_request_id": cr.cr_id},
        ttl_hours=ttl_hours,
        correlation_id=cr.correlation_id,
    )
    cr.approval_request_id = appr.request_id
    cr.state = "submitted"
    cr.updated_at = datetime.now(timezone.utc).isoformat()
    _persist(cr)
    audit_log.record(
        action="change_request.submitted", entity_type="change_request",
        entity_id=cr_id,
        actor=actor if isinstance(actor, dict) else {"name": str(actor)},
        correlation_id=cr.correlation_id,
        new_state={"approval_request_id": appr.request_id},
    )
    return cr


def sync_state_from_approval(cr_id: str) -> ChangeRequest | None:
    """Pull current approval state and reflect it into the CR."""
    cr = get(cr_id)
    if cr is None or cr.approval_request_id is None:
        return cr
    appr = approvals.get(cr.approval_request_id)
    if appr is None:
        return cr
    new_state = cr.state
    if appr.state == "approved" and cr.state == "submitted":
        new_state = "approved"
    elif appr.state == "rejected" and cr.state == "submitted":
        new_state = "rejected"
    elif appr.state == "expired" and cr.state == "submitted":
        new_state = "rejected"
    if new_state != cr.state:
        cr.state = new_state
        cr.updated_at = datetime.now(timezone.utc).isoformat()
        _persist(cr)
        audit_log.record(
            action=f"change_request.{new_state}", entity_type="change_request",
            entity_id=cr_id,
            actor={"name": "change_request_engine", "system": True},
            correlation_id=cr.correlation_id,
            new_state={"state": new_state},
        )
    return cr


def mark_executed(cr_id: str, *, execution_ref: str | None = None,
                    actor: dict | str = "anonymous") -> ChangeRequest | None:
    cr = get(cr_id)
    if cr is None or cr.state != "approved":
        return cr
    cr.state = "executed"
    cr.executed_at = datetime.now(timezone.utc).isoformat()
    cr.execution_ref = execution_ref
    cr.updated_at = cr.executed_at
    _persist(cr)
    audit_log.record(
        action="change_request.executed", entity_type="change_request",
        entity_id=cr_id,
        actor=actor if isinstance(actor, dict) else {"name": str(actor)},
        correlation_id=cr.correlation_id,
        new_state={"execution_ref": execution_ref},
    )
    if cr.approval_request_id:
        approvals.mark_executed(cr.approval_request_id, execution_ref=execution_ref)
    return cr


def cancel(cr_id: str, *, actor: dict | str = "anonymous",
             reason: str = "") -> ChangeRequest | None:
    cr = get(cr_id)
    if cr is None or cr.state in ("executed", "closed", "cancelled"):
        return cr
    previous = cr.state
    cr.state = "cancelled"
    cr.updated_at = datetime.now(timezone.utc).isoformat()
    _persist(cr)
    audit_log.record(
        action="change_request.cancelled", entity_type="change_request",
        entity_id=cr_id,
        actor=actor if isinstance(actor, dict) else {"name": str(actor)},
        correlation_id=cr.correlation_id,
        previous_state={"state": previous},
        metadata={"reason": reason},
    )
    return cr


def get(cr_id: str) -> ChangeRequest | None:
    path = _CR_DIR / f"{cr_id}.json"
    if not path.exists():
        return None
    try:
        return ChangeRequest(**json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def list_change_requests(*, state: str | None = None) -> list[ChangeRequest]:
    if not _CR_DIR.exists():
        return []
    out: list[ChangeRequest] = []
    for p in _CR_DIR.glob("CR-*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            out.append(ChangeRequest(**data))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    if state:
        out = [c for c in out if c.state == state]
    out.sort(key=lambda c: c.created_at, reverse=True)
    return out


# ── Internal ───────────────────────────────────────────────────────────


def _persist(cr: ChangeRequest) -> None:
    _CR_DIR.mkdir(parents=True, exist_ok=True)
    (_CR_DIR / f"{cr.cr_id}.json").write_text(
        json.dumps(cr.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8",
    )
