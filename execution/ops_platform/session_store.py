"""Session persistence — one JSON per session under
``output/ops_platform/sessions/{session_id}.json``.

Schema-validated on write. Expiration is checked on read; expired rows
return None and are deleted lazily on the next sweep.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jsonschema

from config.settings import OUTPUT_DIR, SCHEMAS_DIR

logger = logging.getLogger(__name__)

_SESSIONS_DIR = OUTPUT_DIR / "ops_platform" / "sessions"
_SCHEMA_PATH = SCHEMAS_DIR / "ops" / "session.schema.json"

DEFAULT_TTL_HOURS = 12


def create_session(
    *,
    user_id: str,
    display_name: str = "",
    email: str = "",
    department: str = "",
    roles: list | None = None,
    workspace_ids: list | None = None,
    auth_provider: str = "HEADER_AUTH",
    ttl_hours: int = DEFAULT_TTL_HOURS,
    ip: str | None = None,
) -> dict:
    session_id = secrets.token_urlsafe(24)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=ttl_hours)
    row = {
        "session_id": session_id,
        "user_id": user_id,
        "display_name": display_name or user_id,
        "email": email or "",
        "department": department or "",
        "roles": list(roles or []),
        "workspace_ids": list(workspace_ids or []),
        "issued_at": now.isoformat(),
        "expires_at": expires.isoformat(),
        "auth_provider": auth_provider,
    }
    if ip:
        row["ip"] = ip
    _validate_or_raise(row)
    _persist(row)
    return row


def get_session(session_id: str) -> dict | None:
    if not session_id:
        return None
    path = _SESSIONS_DIR / f"{session_id}.json"
    if not path.exists():
        return None
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if _is_expired(row):
        try:
            path.unlink()
        except OSError:
            pass
        return None
    return row


def delete_session(session_id: str) -> bool:
    path = _SESSIONS_DIR / f"{session_id}.json"
    if not path.exists():
        return False
    try:
        path.unlink()
        return True
    except OSError:
        return False


def list_sessions(*, user_id: str | None = None) -> list[dict]:
    if not _SESSIONS_DIR.exists():
        return []
    out: list[dict] = []
    for p in _SESSIONS_DIR.glob("*.json"):
        try:
            row = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if _is_expired(row):
            try:
                p.unlink()
            except OSError:
                pass
            continue
        if user_id and row.get("user_id") != user_id:
            continue
        out.append(row)
    out.sort(key=lambda r: r.get("issued_at", ""), reverse=True)
    return out


# ── Internal ───────────────────────────────────────────────────────────


def _is_expired(row: dict) -> bool:
    try:
        return datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc)
    except (TypeError, ValueError, KeyError):
        return True


_SCHEMA_CACHE: dict | None = None


def _load_schema() -> dict:
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is None:
        with open(_SCHEMA_PATH, "r", encoding="utf-8") as f:
            _SCHEMA_CACHE = json.load(f)
    return _SCHEMA_CACHE


def _validate_or_raise(row: dict) -> None:
    schema = _load_schema()
    validator = jsonschema.Draft202012Validator(schema)
    errors = [
        f"{'.'.join(str(p) for p in err.absolute_path) or '<root>'}: {err.message}"
        for err in sorted(validator.iter_errors(row), key=lambda e: e.absolute_path)
    ]
    if errors:
        raise ValueError(f"session schema invalid: {errors[:2]}")


def _persist(row: dict) -> None:
    _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    path = _SESSIONS_DIR / f"{row['session_id']}.json"
    path.write_text(json.dumps(row, indent=2, ensure_ascii=False), encoding="utf-8")
