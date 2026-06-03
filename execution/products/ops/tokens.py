"""Resolve which Basecamp token to use when syncing a given user.

Resolution order:
  1. Per-user vault entry under tool='basecamp_ai_clone' (Provision 2 vault).
  2. Legacy fallback: the CCPP CB System token (Ali-era; will retire once
     every user has their own AI clone token in the vault).

Returns None when no token is available — sync skips that user with a
clear "token_missing" status instead of crashing.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

# CCPP fallback token cache (same path the BC MCP uses — single source of truth)
_CCPP_CACHE = Path.home() / ".cache" / "bc_mcp" / "token.json"


def _ccpp_token() -> str | None:
    """Read the CB System token from the BC MCP's disk cache. Returns None if
    cache is missing — this is the legacy fallback path, not used in
    production user-by-user sync.
    """
    if not _CCPP_CACHE.exists():
        return None
    try:
        payload = json.loads(_CCPP_CACHE.read_text(encoding="utf-8"))
        return payload.get("token")
    except (json.JSONDecodeError, OSError):
        return None


def get_user_token(user_id: str) -> tuple[str | None, str]:
    """Return (token, source) where source is one of:
        'vault'    — per-user AI clone token from the vault (preferred)
        'ccpp'     — legacy CB System token (Ali-only transition)
        'missing'  — neither available; sync should skip
    """
    # 1. Per-user vault entry
    try:
        from execution.products.library import vault
        t = vault.read_secret(
            user_id=user_id,
            tool_name="basecamp_ai_clone",
            caller_id="ops.sync",
            purpose="Basecamp todo sync for the AI Ops Command Center",
        )
        if t:
            return t, "vault"
    except Exception:
        pass

    # 2. CCPP fallback (transitional)
    t = _ccpp_token()
    if t:
        return t, "ccpp"

    return None, "missing"


def get_user_bc_id(user_id: str) -> int | None:
    """Return the user's Basecamp identity id (the *human* BC id, not the AI
    clone's). Primary source: User.bc_user_id (set via admin AI-clone form).
    Fallback: hardcoded legacy map for Ali so Phase A demos before admin
    setup is done.
    """
    try:
        from execution.products.library import tenancy
        u = tenancy.get_user(user_id)
        if u and getattr(u, "bc_user_id", None):
            return int(u.bc_user_id)
    except Exception:
        pass

    # Legacy fallback — drop once everyone's bc_user_id is populated.
    _LEGACY = {"ali@colaberry.com": 17454835}
    try:
        from execution.products.library import tenancy
        u = tenancy.get_user(user_id)
        if u and u.email in _LEGACY:
            return _LEGACY[u.email]
    except Exception:
        pass

    return None
