"""RBAC — role to permission mapping + permission checks.

Permissions are strings of the form ``area.action``. Roles are sets of
permissions. ``has_permission(identity, permission, **scope)`` is the
single check function used by enforcement.

Compatibility mode: when an identity is anonymous AND OPS_ENFORCE_RBAC is
not set to "true", every permission check returns True. This preserves
Phase 1-4 single-user behavior.
"""

from __future__ import annotations

import os

from execution.ops_platform.identity import IdentityContext


PERMISSIONS = {
    "capability.read",
    "capability.execute",
    "capability.publish",
    "pipeline.execute",
    "pipeline.publish",
    "optimizer.apply",
    "discovery.publish",
    "workspace.manage",
    "reporting.view",
    "audit.view",
    "version.promote",
    "version.rollback",
    "marketplace.publish",
    "marketplace.fork",
    "controls.manage",
}


_ROLE_PERMISSIONS: dict[str, set[str]] = {
    "admin": set(PERMISSIONS),
    "operator": {
        "capability.read", "capability.execute", "pipeline.execute",
        "reporting.view", "marketplace.fork",
    },
    "reviewer": {
        "capability.read", "reporting.view", "audit.view",
        "discovery.publish", "version.promote",
    },
    "builder": {
        "capability.read", "capability.execute", "capability.publish",
        "pipeline.execute", "pipeline.publish", "marketplace.publish",
        "marketplace.fork",
    },
    "viewer": {"capability.read", "reporting.view"},
}


def is_enforced() -> bool:
    """When OPS_ENFORCE_RBAC=true, anonymous callers are denied permissions
    they don't hold. Otherwise (default) anonymous bypass keeps Phase 1-4
    single-user flows working."""
    return os.environ.get("OPS_ENFORCE_RBAC", "").lower() == "true"


def permissions_for_role(role: str) -> set[str]:
    return set(_ROLE_PERMISSIONS.get(role, set()))


def effective_permissions(identity: IdentityContext) -> set[str]:
    out: set[str] = set()
    for role in identity.roles:
        out |= permissions_for_role(role)
    return out


def has_permission(identity: IdentityContext, permission: str, *,
                    workspace_id: str | None = None,
                    capability_id: str | None = None) -> bool:
    """Top-level check. Workspace-scoped permissions also require workspace
    membership unless the identity holds ``admin``."""
    if not identity.authenticated and not is_enforced():
        return True
    if permission not in PERMISSIONS:
        return False
    perms = effective_permissions(identity)
    if "admin" in identity.roles:
        return True
    if permission not in perms:
        return False
    if workspace_id and workspace_id not in identity.workspace_ids:
        return False
    return True


def reason_denied(identity: IdentityContext, permission: str, *,
                   workspace_id: str | None = None) -> str:
    if not identity.authenticated and is_enforced():
        return "identity is anonymous and enforcement is on"
    if permission not in PERMISSIONS:
        return f"unknown permission '{permission}'"
    if "admin" in identity.roles:
        return "admin should have access — investigate"
    perms = effective_permissions(identity)
    if permission not in perms:
        return f"none of the user's roles {identity.roles} grant {permission}"
    if workspace_id and workspace_id not in identity.workspace_ids:
        return f"user is not a member of workspace '{workspace_id}'"
    return "denied"
