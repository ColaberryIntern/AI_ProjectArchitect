"""Identity context — the per-request envelope every Phase 5 module accepts.

Replaces the honor-system actor strings used in Phase 1-4. Callers that
don't have an identity yet (e.g. local-dev scripts) receive
``anonymous_identity()`` which is interchangeable in all downstream APIs but
flags ``authenticated=False`` so enforcement can decide what to do.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


@dataclass
class IdentityContext:
    user_id: str
    display_name: str
    auth_provider: str               # LOCAL_DEV | HEADER_AUTH | STATIC_TOKEN | FUTURE_OIDC_PLACEHOLDER
    authenticated: bool
    email: str = ""
    department: str = ""
    roles: list = field(default_factory=list)
    workspace_ids: list = field(default_factory=list)
    session_id: str = ""
    issued_at: str = ""
    expires_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def has_role(self, role: str) -> bool:
        return role in self.roles

    def as_actor(self) -> dict:
        """Adapter so audit_log + every existing Phase 1-4 mutation point
        keeps working unchanged."""
        out: dict = {"name": self.user_id, "system": not self.authenticated}
        if self.email:
            out["email"] = self.email
        if self.department:
            out["team"] = self.department
        return out


def anonymous_identity() -> IdentityContext:
    """The fallback identity used when no auth header / session is present.
    Downstream calls work identically to the Phase 1-4 anonymous flow."""
    return IdentityContext(
        user_id="anonymous",
        display_name="Anonymous",
        auth_provider="LOCAL_DEV",
        authenticated=False,
        roles=["viewer"],
        workspace_ids=[],
        issued_at=datetime.now(timezone.utc).isoformat(),
    )


def from_session(session: dict) -> IdentityContext:
    """Hydrate an IdentityContext from a persisted session row."""
    return IdentityContext(
        user_id=session["user_id"],
        display_name=session.get("display_name", session["user_id"]),
        email=session.get("email", ""),
        department=session.get("department", ""),
        roles=list(session.get("roles") or []),
        workspace_ids=list(session.get("workspace_ids") or []),
        auth_provider=session["auth_provider"],
        authenticated=True,
        session_id=session["session_id"],
        issued_at=session.get("issued_at", ""),
        expires_at=session.get("expires_at", ""),
    )
