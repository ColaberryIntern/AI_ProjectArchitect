"""Enforcement — the glue between identity + rbac + audit_log + errors.

Single entry point for routes:

    enforce(identity, permission, ...)

On allow: returns silently.
On deny: raises ``OpsError(code='ACCESS_DENIED', ...)``, AND writes one
``enforcement.denied`` audit row so every denial is queryable.
"""

from __future__ import annotations

import uuid

from execution.ops_platform import audit_log, rbac, workspaces
from execution.ops_platform.errors import OpsError
from execution.ops_platform.identity import IdentityContext


def enforce(
    identity: IdentityContext,
    permission: str,
    *,
    workspace_id: str | None = None,
    capability_id: str | None = None,
    correlation_id: str | None = None,
) -> None:
    """Raise OpsError(403) if the identity may not perform the permission.

    When workspace_id is supplied AND the capability is owned by some other
    workspace (per workspaces.is_visible_in_workspace), the call is also
    denied. Admin bypasses both checks.
    """
    if rbac.has_permission(identity, permission,
                            workspace_id=workspace_id,
                            capability_id=capability_id):
        # Capability-level workspace visibility: admin bypass, otherwise enforced
        if (capability_id and workspace_id
                and "admin" not in identity.roles
                and not workspaces.is_visible_in_workspace(capability_id, workspace_id)):
            _deny(identity, permission, workspace_id=workspace_id,
                  capability_id=capability_id,
                  reason="capability not visible in workspace",
                  correlation_id=correlation_id)
        return
    _deny(identity, permission, workspace_id=workspace_id,
          capability_id=capability_id,
          reason=rbac.reason_denied(identity, permission, workspace_id=workspace_id),
          correlation_id=correlation_id)


def can(identity: IdentityContext, permission: str, **scope) -> bool:
    """Boolean wrapper for code paths that need to branch on access without
    raising."""
    try:
        enforce(identity, permission, **scope)
        return True
    except OpsError:
        return False


# ── Internal ───────────────────────────────────────────────────────────


def _deny(identity, permission, *, workspace_id, capability_id, reason,
          correlation_id) -> None:
    cid = correlation_id or str(uuid.uuid4())
    payload = {
        "user_id": identity.user_id,
        "authenticated": identity.authenticated,
        "permission": permission,
        "workspace_id": workspace_id,
        "capability_id": capability_id,
        "reason": reason,
    }
    audit_log.record(
        action="enforcement.denied", entity_type="enforcement",
        entity_id=f"{permission}:{capability_id or workspace_id or '-'}",
        actor=identity.as_actor(),
        metadata=payload,
        correlation_id=cid,
    )
    raise OpsError(
        code="ACCESS_DENIED",
        message=f"permission '{permission}' denied: {reason}",
        status_code=403,
        correlation_id=cid,
        details={
            "required_permissions": [permission],
            "workspace_id": workspace_id,
            "capability_id": capability_id,
        },
    )
