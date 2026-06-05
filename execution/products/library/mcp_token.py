"""MCP token issuance + validation.

Tokens are 32 random bytes hex-encoded, prefixed with `cmcp_` for easy
recognition in logs / by humans. We store sha256(token), never the plain
token; the user gets the plain token once at generation and is responsible
for pasting it into their laptop's claude config.

Tokens are PER-USER. One active token per user at a time -- regenerating
revokes the previous one. v1 simplification; a future enhancement could
allow multiple labeled tokens (work laptop / personal laptop / etc.).
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


def generate_for_user(user_id_or_email: str, label: str = "") -> tuple[str, tenancy.User]:
    """Mint a new MCP token for the user. Returns (plain_token, updated_user).

    The plain token is shown to the user ONCE at generation; the User record
    stores only the sha256 hash for later validation.
    """
    user = tenancy.get_user(user_id_or_email)
    if not user:
        raise ValueError(f"unknown user {user_id_or_email!r}")
    token = TOKEN_PREFIX + secrets.token_hex(32)
    user.mcp_token_hash = _hash_token(token)
    user.mcp_token_issued_at = _now()
    user.mcp_token_revoked_at = None
    user.mcp_token_last_used_at = None
    user.mcp_token_label = label or "default"
    tenancy.upsert_user(user)
    return token, user


def revoke_for_user(user_id_or_email: str) -> tenancy.User | None:
    user = tenancy.get_user(user_id_or_email)
    if not user:
        return None
    user.mcp_token_hash = None
    user.mcp_token_revoked_at = _now()
    tenancy.upsert_user(user)
    return user


def validate_token(token: str) -> tenancy.User | None:
    """Look up the user owning this token. Returns None if no match or revoked.

    Side effect: updates `mcp_token_last_used_at` on the matched user so the
    portal's status indicator reflects fresh activity.
    """
    if not token or not token.startswith(TOKEN_PREFIX):
        return None
    h = _hash_token(token)
    for u in tenancy.list_users(active_only=False):
        if u.mcp_token_hash == h and not u.mcp_token_revoked_at:
            u.mcp_token_last_used_at = _now()
            tenancy.upsert_user(u)
            return u
    return None


def status_for_user(user: tenancy.User) -> str:
    """Return 'green', 'yellow', or 'red' for the status indicator."""
    if user.mcp_token_revoked_at:
        return "red"
    if not user.mcp_token_hash or not user.mcp_token_issued_at:
        return "red"
    if not user.mcp_token_last_used_at:
        return "yellow"  # issued but never pinged -> probably not installed yet
    # Parse the timestamp and check freshness
    try:
        last = time.strptime(user.mcp_token_last_used_at, "%Y-%m-%dT%H:%M:%SZ")
        last_secs = time.mktime(last)
    except (ValueError, TypeError):
        return "yellow"
    age = time.time() - last_secs
    if age < 24 * 3600:
        return "green"
    if age < 7 * 24 * 3600:
        return "yellow"
    return "red"
