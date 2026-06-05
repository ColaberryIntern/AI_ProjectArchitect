"""MCP token issuance + validation (Phase 8.3 multi-device).

Each user has a LIST of tokens, one per device they install on. Format:

    user.mcp_tokens = [
      {
        "hash": "<sha256(plain_token)>",
        "label": "Work Mac",
        "issued_at": "2026-06-05T22:00:00Z",
        "last_used_at": "2026-06-05T22:14:33Z" | None,
        "revoked_at": None | "<iso>",
        "last_user_agent": "claude-code/1.2.3 (darwin)",  # optional
      },
      ...
    ]

Aggregate status (for the topbar pill, where we can't distinguish device):
  green  - any non-revoked token last_used_at within 24h
  yellow - any non-revoked token issued but never pinged, OR last_used 24h-7d
  red    - no non-revoked tokens, or all stale (>7d)

Per-device status (for the setup page table) returned by list_devices().

Legacy single-token fields on User (mcp_token_hash etc.) auto-migrate into
mcp_tokens on first read / first validate so we don't break existing users
who minted tokens under Phase 8.0.
"""
from __future__ import annotations

import hashlib
import secrets
import time

from . import tenancy

TOKEN_PREFIX = "cmcp_"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _migrate_legacy(user: tenancy.User) -> bool:
    """If the user has legacy mcp_token_hash but no entry in mcp_tokens,
    lift the legacy fields into a new mcp_tokens entry. Returns True if
    a migration happened (so caller can persist).
    """
    if not user.mcp_token_hash:
        return False
    # Already migrated if any entry matches the legacy hash
    if any(t.get("hash") == user.mcp_token_hash for t in (user.mcp_tokens or [])):
        return False
    user.mcp_tokens = user.mcp_tokens or []
    user.mcp_tokens.append({
        "hash": user.mcp_token_hash,
        "label": user.mcp_token_label or "device-1",
        "issued_at": user.mcp_token_issued_at or _now(),
        "last_used_at": user.mcp_token_last_used_at,
        "revoked_at": user.mcp_token_revoked_at,
        "last_user_agent": None,
    })
    return True


# ── Generate / revoke ─────────────────────────────────────────────────


def generate_for_user(user_id_or_email: str, label: str = "device") -> tuple[str, tenancy.User]:
    """Mint a new MCP token for the user and append to their mcp_tokens list.

    Returns (plain_token, updated_user). The plain token is shown ONCE to
    the user; only sha256 hash is persisted.

    If `label` collides with an existing non-revoked token, appends a numeric
    suffix so each device has a unique label.
    """
    user = tenancy.get_user(user_id_or_email)
    if not user:
        raise ValueError(f"unknown user {user_id_or_email!r}")
    _migrate_legacy(user)
    user.mcp_tokens = user.mcp_tokens or []
    used_labels = {t.get("label", "") for t in user.mcp_tokens if not t.get("revoked_at")}
    final_label = label.strip() or "device"
    if final_label in used_labels:
        i = 2
        while f"{final_label} ({i})" in used_labels:
            i += 1
        final_label = f"{final_label} ({i})"
    token = TOKEN_PREFIX + secrets.token_hex(32)
    user.mcp_tokens.append({
        "hash": _hash_token(token),
        "label": final_label,
        "issued_at": _now(),
        "last_used_at": None,
        "revoked_at": None,
        "last_user_agent": None,
    })
    tenancy.upsert_user(user)
    return token, user


def revoke_device(user_id_or_email: str, label: str) -> tenancy.User | None:
    """Revoke a single device's token by its label."""
    user = tenancy.get_user(user_id_or_email)
    if not user:
        return None
    _migrate_legacy(user)
    found = False
    for t in (user.mcp_tokens or []):
        if t.get("label") == label and not t.get("revoked_at"):
            t["revoked_at"] = _now()
            found = True
    if found:
        tenancy.upsert_user(user)
    return user


def revoke_all_for_user(user_id_or_email: str) -> tenancy.User | None:
    """Revoke every active token (panic button)."""
    user = tenancy.get_user(user_id_or_email)
    if not user:
        return None
    _migrate_legacy(user)
    now = _now()
    for t in (user.mcp_tokens or []):
        if not t.get("revoked_at"):
            t["revoked_at"] = now
    # Also clear the legacy single-token fields if any
    user.mcp_token_hash = None
    user.mcp_token_revoked_at = now
    tenancy.upsert_user(user)
    return user


# ── Validate ──────────────────────────────────────────────────────────


def validate_token(token: str, user_agent: str | None = None) -> tenancy.User | None:
    """Look up the user owning this token. Updates last_used_at on the
    matched device entry (and the legacy field for back-compat).
    Returns None if no match or revoked.
    """
    if not token or not token.startswith(TOKEN_PREFIX):
        return None
    h = _hash_token(token)
    for u in tenancy.list_users(active_only=False):
        # Make sure legacy is migrated so list contains the legacy entry
        migrated = _migrate_legacy(u)
        for t in (u.mcp_tokens or []):
            if t.get("hash") == h and not t.get("revoked_at"):
                t["last_used_at"] = _now()
                if user_agent:
                    t["last_user_agent"] = user_agent[:200]
                u.mcp_token_last_used_at = t["last_used_at"]  # legacy mirror
                tenancy.upsert_user(u)
                return u
        if migrated:
            tenancy.upsert_user(u)
    return None


# ── Status ───────────────────────────────────────────────────────────


def _device_status(entry: dict) -> str:
    """Per-device status: green / yellow / red."""
    if entry.get("revoked_at"):
        return "revoked"
    last = entry.get("last_used_at")
    if not last:
        return "yellow"  # issued, never pinged
    try:
        last_secs = time.mktime(time.strptime(last, "%Y-%m-%dT%H:%M:%SZ"))
    except (ValueError, TypeError):
        return "yellow"
    age = time.time() - last_secs
    if age < 24 * 3600:
        return "green"
    if age < 7 * 24 * 3600:
        return "yellow"
    return "red"


def list_devices(user: tenancy.User) -> list[dict]:
    """Return per-device info for the setup page table."""
    _migrate_legacy(user)
    out = []
    for t in (user.mcp_tokens or []):
        out.append({
            "label": t.get("label", "?"),
            "issued_at": t.get("issued_at"),
            "last_used_at": t.get("last_used_at"),
            "revoked_at": t.get("revoked_at"),
            "last_user_agent": t.get("last_user_agent"),
            "status": _device_status(t),
        })
    # Sort: active first (green/yellow), revoked last; within each, newest issued first
    def _key(d):
        rev = 1 if d["status"] == "revoked" else 0
        return (rev, -(time.mktime(time.strptime(d["issued_at"], "%Y-%m-%dT%H:%M:%SZ")))
                            if d.get("issued_at") else 0)
    out.sort(key=_key)
    return out


def status_for_user(user: tenancy.User) -> str:
    """Aggregate status for the topbar pill.

    Note: this is USER-level (we don't know which browser is asking).
    Per-device status is on the setup page table.
    """
    _migrate_legacy(user)
    if not user.mcp_tokens:
        return "red"
    statuses = [_device_status(t) for t in user.mcp_tokens]
    active = [s for s in statuses if s != "revoked"]
    if not active:
        return "red"
    if "green" in active:
        return "green"
    if "yellow" in active:
        return "yellow"
    return "red"
