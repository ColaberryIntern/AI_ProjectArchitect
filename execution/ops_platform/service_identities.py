"""Service identities — machine / scheduler / pipeline / system actors.

Distinct from human IdentityContext: ``is_service=True`` lets the audit log
+ policy engine differentiate "alice did this" from "the scheduler did this".

Service identities are NOT created via auth.login(). They are configured at
deployment time (``output/ops_platform/service_identities/{sid}.json``) with
a long-lived API token. Each service identity has scoped permissions and
appears in audit rows with ``actor.system=True``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution.ops_platform import audit_log
from execution.ops_platform.identity import IdentityContext

logger = logging.getLogger(__name__)

_SERVICES_DIR = OUTPUT_DIR / "ops_platform" / "service_identities"


@dataclass
class ServiceIdentity:
    service_id: str
    display_name: str
    token_hash: str           # sha256 hex of the real token (never persist plaintext)
    roles: list
    workspace_ids: list
    description: str
    created_at: str
    created_by: dict
    expires_at: str | None = None
    revoked: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def create(
    *,
    display_name: str,
    roles: list,
    workspace_ids: list | None = None,
    description: str = "",
    created_by: dict | str = "anonymous",
    expires_at: str | None = None,
) -> tuple[ServiceIdentity, str]:
    """Returns (service_identity, plaintext_token). The plaintext is shown
    ONCE — store it securely; the platform persists only the hash."""
    _SERVICES_DIR.mkdir(parents=True, exist_ok=True)
    sid = f"svc_{uuid.uuid4().hex[:12]}"
    token = secrets.token_urlsafe(32)
    actor = created_by if isinstance(created_by, dict) else {"name": str(created_by)}
    si = ServiceIdentity(
        service_id=sid, display_name=display_name,
        token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        roles=list(roles), workspace_ids=list(workspace_ids or []),
        description=description, created_at=datetime.now(timezone.utc).isoformat(),
        created_by=actor, expires_at=expires_at,
    )
    (_SERVICES_DIR / f"{sid}.json").write_text(
        json.dumps(si.to_dict(), indent=2), encoding="utf-8",
    )
    audit_log.record(
        action="service_identity.created", entity_type="service_identity",
        entity_id=sid, actor=actor,
        new_state={"display_name": display_name, "roles": roles},
    )
    return si, token


def revoke(service_id: str, *, actor: dict | str | None = None) -> bool:
    si = get(service_id)
    if si is None or si.revoked:
        return False
    si.revoked = True
    (_SERVICES_DIR / f"{service_id}.json").write_text(
        json.dumps(si.to_dict(), indent=2), encoding="utf-8",
    )
    audit_log.record(
        action="service_identity.revoked", entity_type="service_identity",
        entity_id=service_id, actor=actor or {"name": "anonymous"},
        previous_state={"revoked": False}, new_state={"revoked": True},
    )
    return True


def get(service_id: str) -> ServiceIdentity | None:
    path = _SERVICES_DIR / f"{service_id}.json"
    if not path.exists():
        return None
    try:
        return ServiceIdentity(**json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def list_all(*, include_revoked: bool = False) -> list[ServiceIdentity]:
    if not _SERVICES_DIR.exists():
        return []
    out: list[ServiceIdentity] = []
    for p in _SERVICES_DIR.glob("svc_*.json"):
        try:
            si = ServiceIdentity(**json.loads(p.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        if si.revoked and not include_revoked:
            continue
        out.append(si)
    return out


def authenticate(token: str) -> IdentityContext | None:
    """Resolve a token presented as ``Authorization: Bearer <token>`` against
    the registered service identities. Returns None if no match."""
    if not token:
        return None
    h = hashlib.sha256(token.encode("utf-8")).hexdigest()
    for si in list_all():
        if si.token_hash != h:
            continue
        if si.expires_at:
            try:
                if datetime.fromisoformat(si.expires_at) < datetime.now(timezone.utc):
                    continue
            except ValueError:
                pass
        return IdentityContext(
            user_id=si.service_id,
            display_name=si.display_name,
            auth_provider="STATIC_TOKEN",
            authenticated=True,
            roles=list(si.roles),
            workspace_ids=list(si.workspace_ids),
        )
    return None
