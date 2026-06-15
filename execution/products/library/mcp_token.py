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


def revoke_unidentified_for_user(user_id_or_email: str) -> tuple[tenancy.User | None, int]:
    """Revoke only the tokens whose device never reported a hostname.

    Hostname is the strongest "which physical computer is this?" signal. A
    device with no hostname is either:
      - never installed (awaiting ping, label only)
      - installed with the old command that didn't embed X-MCP-Hostname
    Either way, the row is unidentifiable to a human scanning the table.
    Cleaner to revoke + re-install with the hostname-capturing flow.

    Returns (user, count_revoked).
    """
    user = tenancy.get_user(user_id_or_email)
    if not user:
        return None, 0
    _migrate_legacy(user)
    now = _now()
    count = 0
    for t in (user.mcp_tokens or []):
        if t.get("revoked_at"):
            continue
        if not t.get("hostname"):
            t["revoked_at"] = now
            count += 1
    if count:
        tenancy.upsert_user(user)
    return user, count


# ── Validate ──────────────────────────────────────────────────────────


def _clean_hostname(h: str | None) -> str | None:
    """Reject an unexpanded shell template as a hostname.

    If the operator pastes a setup command meant for a different shell
    (the cmd.exe `%COMPUTERNAME%` snippet into PowerShell, the PowerShell
    `$env:COMPUTERNAME` into cmd, or the `$(hostname)` Unix form into
    either), the X-MCP-Hostname header arrives as the literal template
    instead of the resolved machine name. Storing that makes the device
    list unreadable (real bug: a device registered as "%COMPUTERNAME%").
    Real hostnames are alphanumerics, hyphens, dots, underscores — never
    %, $, (), or backticks — so any of those means "wrong-shell paste,"
    and we treat it as no hostname reported.
    """
    if not h:
        return None
    h = h.strip()
    if not h or any(c in h for c in ("%", "$", "(", ")", "`")):
        return None
    return h[:120]


def validate_token(token: str,
                                user_agent: str | None = None,
                                hostname: str | None = None,
                                client_ip: str | None = None) -> tenancy.User | None:
    """Look up the user owning this token. Updates last_used_at on the
    matched device entry (and the legacy field for back-compat).

    Captures hostname (from X-MCP-Hostname header) + client_ip + user_agent
    so the setup page can identify WHICH physical computer each row
    represents. Hostname is the most reliable identifier; the others are
    backups.
    """
    if not token or not token.startswith(TOKEN_PREFIX):
        return None
    h = _hash_token(token)
    for u in tenancy.list_users(active_only=False):
        migrated = _migrate_legacy(u)
        for t in (u.mcp_tokens or []):
            if t.get("hash") == h and not t.get("revoked_at"):
                t["last_used_at"] = _now()
                if user_agent:
                    t["last_user_agent"] = user_agent[:200]
                hn = _clean_hostname(hostname)
                if hn:
                    t["hostname"] = hn
                if client_ip:
                    t["last_client_ip"] = client_ip[:64]
                u.mcp_token_last_used_at = t["last_used_at"]
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


def purge_revoked_for_user(user_id_or_email: str) -> tuple[tenancy.User | None, int]:
    """Hard-delete revoked entries from mcp_tokens. Returns (user, removed_count).

    The token hash is dropped entirely -- revoked tokens were already
    unusable (validate_token skips revoked entries), but this trims the
    stored list so the user's record stays compact and the UI doesn't have
    to filter out historical noise on every render.
    """
    user = tenancy.get_user(user_id_or_email)
    if not user:
        return None, 0
    _migrate_legacy(user)
    if not user.mcp_tokens:
        return user, 0
    before = len(user.mcp_tokens)
    user.mcp_tokens = [t for t in user.mcp_tokens if not t.get("revoked_at")]
    removed = before - len(user.mcp_tokens)
    if removed:
        tenancy.upsert_user(user)
    return user, removed


def list_devices(user: tenancy.User) -> list[dict]:
    """Return per-device info for the setup page table. Excludes revoked
    entries -- they're noise to the user.
    """
    _migrate_legacy(user)
    out = []
    for t in (user.mcp_tokens or []):
        if t.get("revoked_at"):
            continue
        out.append({
            "label": t.get("label", "?"),
            "issued_at": t.get("issued_at"),
            "last_used_at": t.get("last_used_at"),
            "revoked_at": t.get("revoked_at"),
            "last_user_agent": t.get("last_user_agent"),
            "hostname": t.get("hostname"),
            "last_client_ip": t.get("last_client_ip"),
            "status": _device_status(t),
            # True iff this device has EVER successfully pinged. Separate
            # from `status` (which is freshness-based: green<24h,
            # yellow<7d, red>=7d). Lets the banner distinguish
            # "install never completed" from "installed but laptop has
            # been asleep" -- two very different situations that used to
            # produce the same misleading "never pinged" warning.
            "installed": bool(t.get("last_used_at")) and not t.get("revoked_at"),
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
