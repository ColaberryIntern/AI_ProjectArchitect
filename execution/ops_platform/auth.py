"""Auth — the FastAPI dependency that resolves a Request into an
``IdentityContext``.

Resolution order (first hit wins):
  1. ``X-Session-Id`` request header → session_store lookup
  2. ``X-User-Id`` / ``X-Roles`` / ``X-Department`` request headers
     (HEADER_AUTH mode — handy for reverse-proxy injected identity)
  3. ``OPS_STATIC_TOKEN`` env var match against header ``Authorization: Bearer``
     (STATIC_TOKEN mode — for CI / scripted use)
  4. Fallback to ``anonymous_identity()`` in LOCAL_DEV mode

The dependency function ``get_identity_context()`` is the only public API
in this module. Everything else is internal.

Login / logout
--------------
``login(...)`` creates a session and returns an ``IdentityContext``. It
emits ``auth.login`` to the audit log. ``logout(session_id)`` removes the
session and emits ``auth.logout``. Both are pure — no FastAPI coupling.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from execution.ops_platform import audit_log, session_store
from execution.ops_platform.identity import (
    IdentityContext, anonymous_identity, from_session,
)

logger = logging.getLogger(__name__)


# ── Public: pure functions ────────────────────────────────────────────


def login(
    *,
    user_id: str,
    display_name: str = "",
    email: str = "",
    department: str = "",
    roles: list | None = None,
    workspace_ids: list | None = None,
    auth_provider: str = "HEADER_AUTH",
    ip: str | None = None,
) -> IdentityContext:
    row = session_store.create_session(
        user_id=user_id, display_name=display_name, email=email,
        department=department, roles=roles, workspace_ids=workspace_ids,
        auth_provider=auth_provider, ip=ip,
    )
    audit_log.record(
        action="auth.login", entity_type="session",
        entity_id=row["session_id"], actor={"name": user_id},
        new_state={"auth_provider": auth_provider, "roles": row["roles"]},
    )
    return from_session(row)


def logout(session_id: str) -> bool:
    row = session_store.get_session(session_id)
    if not row:
        return False
    removed = session_store.delete_session(session_id)
    if removed:
        audit_log.record(
            action="auth.logout", entity_type="session",
            entity_id=session_id, actor={"name": row.get("user_id", "anonymous")},
            previous_state={"auth_provider": row.get("auth_provider")},
        )
    return removed


def login_failed(*, user_id: str, reason: str) -> None:
    audit_log.record(
        action="auth.failed", entity_type="session",
        entity_id="-", actor={"name": user_id or "unknown"},
        metadata={"reason": reason},
    )


# ── FastAPI dependency ────────────────────────────────────────────────


def get_identity_context(request) -> IdentityContext:
    """Resolve a FastAPI ``Request`` into an IdentityContext. Always returns
    a context — falls back to anonymous when nothing in the request
    identifies the caller."""
    # 1. Session header
    session_id = request.headers.get("X-Session-Id", "")
    if session_id:
        row = session_store.get_session(session_id)
        if row:
            return from_session(row)

    # 2. Header-injected identity
    user_id = request.headers.get("X-User-Id", "")
    if user_id:
        roles = [r.strip() for r in (request.headers.get("X-Roles", "") or "").split(",") if r.strip()]
        workspaces = [w.strip() for w in (request.headers.get("X-Workspaces", "") or "").split(",") if w.strip()]
        return IdentityContext(
            user_id=user_id,
            display_name=request.headers.get("X-Display-Name", user_id),
            email=request.headers.get("X-Email", ""),
            department=request.headers.get("X-Department", ""),
            roles=roles or ["viewer"],
            workspace_ids=workspaces,
            auth_provider="HEADER_AUTH",
            authenticated=True,
        )

    # 3. Static token (for CI / scripted use)
    token = os.environ.get("OPS_STATIC_TOKEN", "")
    if token:
        bearer = request.headers.get("Authorization", "")
        if bearer == f"Bearer {token}":
            return IdentityContext(
                user_id="static-token",
                display_name="Static Token",
                auth_provider="STATIC_TOKEN",
                authenticated=True,
                roles=["admin"],
                workspace_ids=[],
            )

    # 4. Anonymous fallback
    return anonymous_identity()
