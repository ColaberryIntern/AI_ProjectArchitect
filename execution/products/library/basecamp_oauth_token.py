"""Basecamp 3 OAuth token resolution for per-operator "X AI" personas.

Parallel to google_oauth_token.py. The per-user "X AI" Basecamp account
issues a refresh_token via Launchpad OAuth that we exchange for
access_tokens on demand, so the 14-day BC access_token TTL never
requires manual rotation.

Vault entry format (single key per user, holds both tokens + identity):

    {
      "v": 1,
      "refresh_token": "<refresh>",
      "access_token": "<access>",          # most recent; cached here for restarts
      "access_token_expires_at": <epoch>,
      "bc_user_id": <int>,                  # the AI persona's BC numeric id
      "bc_user_email": "<x>-ai@colaberry.com"
    }

Public API:
    get_access_token_for_operator(user) -> str               # raises OAuthError
    invalidate_access_token_cache(user) -> None
    store_oauth_grant(user, *, access_token, refresh_token,
                      bc_user_id, bc_user_email,
                      access_token_expires_at, actor_id) -> None
    get_grant_metadata(user) -> dict | None

Failure-first:
    - 15s timeout on outbound calls
    - One automatic re-exchange on 401 (called externally; we provide
      invalidate)
    - No silent swallows; raises OAuthError with machine-readable codes

Never logs the refresh_token, access_token, or client_secret.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

from . import vault

logger = logging.getLogger(__name__)

VAULT_TOOL_NAME = "basecamp_ai_clone"
TOKEN_ENDPOINT = "https://launchpad.37signals.com/authorization/token"
AUTHORIZATION_INFO_ENDPOINT = "https://launchpad.37signals.com/authorization.json"
DEFAULT_TIMEOUT = 15.0
ACCESS_TOKEN_REFRESH_BUFFER_SEC = 300


class OAuthError(Exception):
    """Machine-readable BC OAuth failure."""
    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(f"{code}: {message}" if message else code)


@dataclass(frozen=True)
class _CachedAccessToken:
    access_token: str
    expires_at_epoch: float


_ACCESS_TOKEN_CACHE: dict[str, _CachedAccessToken] = {}
_CACHE_LOCK = threading.Lock()


def _client_credentials() -> tuple[str, str]:
    """OAuth client_id + secret for the Basecamp Integration.

    Registered once at https://integrate.37signals.com/ with redirect URI
    https://advisor.colaberry.ai/auth/basecamp-ai-callback. Distinct from
    BASECAMP_ACCESS_TOKEN (the shared CB System bearer used as fallback
    when per-user grants are absent).
    """
    cid = (os.environ.get("BASECAMP_OAUTH_CLIENT_ID") or "").strip()
    secret = (os.environ.get("BASECAMP_OAUTH_CLIENT_SECRET") or "").strip()
    if not cid or not secret:
        raise OAuthError(
            "basecamp_oauth_app_not_configured",
            "BASECAMP_OAUTH_CLIENT_ID / _SECRET env vars missing on advisor",
        )
    return cid, secret


def _read_stored_grant(user) -> Optional[dict]:
    """Raw vault read. Returns the wrapped dict, or None when absent.

    Legacy bare-string entries (single access_token, no refresh) are
    treated as legacy and returned as a partial dict so callers can
    surface a clear "needs OAuth re-grant" error.
    """
    try:
        stored = vault.read_secret(
            user.user_id,
            VAULT_TOOL_NAME,
            caller_id="mcp_basecamp_oauth",
            purpose="exchange refresh for BC access token",
        )
    except KeyError:
        return None
    except Exception as e:
        logger.warning("vault read failed for user=%s tool=%s err_type=%s",
                       user.user_id, VAULT_TOOL_NAME, type(e).__name__)
        raise OAuthError("vault_read_failed", "could not read stored credential")

    if not stored:
        return None
    s = stored.strip()
    if s.startswith("{"):
        try:
            obj = json.loads(s)
            if isinstance(obj, dict) and obj.get("refresh_token"):
                return obj
        except json.JSONDecodeError:
            pass
    return {"_legacy_access_token": s}


def invalidate_access_token_cache(user) -> None:
    """Drop any cached access token for this user."""
    with _CACHE_LOCK:
        _ACCESS_TOKEN_CACHE.pop(user.user_id, None)


def get_access_token_for_operator(user) -> str:
    """Return a fresh (or cached, if not near-expiry) BC access token.

    Raises OAuthError on any failure. Legacy bare-string vault entries
    (no refresh_token) raise `basecamp_grant_legacy_no_refresh` to
    signal that the operator needs to re-consent via
    /profile/connect-basecamp-ai.
    """
    with _CACHE_LOCK:
        cached = _ACCESS_TOKEN_CACHE.get(user.user_id)
    if cached and cached.expires_at_epoch > time.time() + ACCESS_TOKEN_REFRESH_BUFFER_SEC:
        return cached.access_token

    grant = _read_stored_grant(user)
    if not grant:
        raise OAuthError(
            "no_basecamp_oauth_grant",
            "operator needs to visit /profile/connect-basecamp-ai",
        )
    if "_legacy_access_token" in grant:
        raise OAuthError(
            "basecamp_grant_legacy_no_refresh",
            "operator needs to re-consent via /profile/connect-basecamp-ai",
        )

    refresh_token = grant.get("refresh_token") or ""
    cached_access = grant.get("access_token") or ""
    expires_at = float(grant.get("access_token_expires_at") or 0)

    if cached_access and expires_at > time.time() + ACCESS_TOKEN_REFRESH_BUFFER_SEC:
        with _CACHE_LOCK:
            _ACCESS_TOKEN_CACHE[user.user_id] = _CachedAccessToken(
                access_token=cached_access, expires_at_epoch=expires_at,
            )
        return cached_access

    if not refresh_token:
        raise OAuthError(
            "basecamp_grant_missing_refresh_token",
            "stored grant has no refresh_token; re-consent required",
        )

    client_id, client_secret = _client_credentials()
    body = urllib.parse.urlencode({
        "type": "refresh",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode("utf-8")
    req = urllib.request.Request(
        TOKEN_ENDPOINT,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": "Colaberry MCP per-user BC AI (ali@colaberry.com)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_text = ""
        try:
            body_text = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        if e.code in (400, 401) and ("invalid_grant" in body_text or "invalid_token" in body_text):
            raise OAuthError(
                "basecamp_grant_invalid",
                "operator needs to re-consent via /profile/connect-basecamp-ai",
            )
        logger.warning("basecamp /token HTTP %s for user=%s", e.code, user.user_id)
        raise OAuthError("basecamp_token_http_error", f"HTTP {e.code} from /token")
    except urllib.error.URLError as e:
        logger.warning("basecamp /token network error for user=%s: %s",
                       user.user_id, type(e).__name__)
        raise OAuthError("basecamp_token_network_error",
                         "could not reach launchpad.37signals.com /authorization/token")
    except json.JSONDecodeError:
        raise OAuthError("basecamp_token_malformed_response", "/token returned non-JSON body")

    new_access = payload.get("access_token") or ""
    if not new_access:
        raise OAuthError(
            "basecamp_token_missing_access_token",
            "/token response had no access_token",
        )
    expires_in = int(payload.get("expires_in", 1209600))
    new_expires_at = time.time() + expires_in
    new_refresh = payload.get("refresh_token") or refresh_token

    grant["access_token"] = new_access
    grant["access_token_expires_at"] = new_expires_at
    grant["refresh_token"] = new_refresh
    grant["v"] = 1
    vault.store_secret(
        user.user_id,
        VAULT_TOOL_NAME,
        json.dumps(grant),
        caller_id="basecamp_oauth_token.refresh",
        ttl_days=180,
    )

    with _CACHE_LOCK:
        _ACCESS_TOKEN_CACHE[user.user_id] = _CachedAccessToken(
            access_token=new_access, expires_at_epoch=new_expires_at,
        )
    return new_access


def store_oauth_grant(user, *, access_token: str, refresh_token: str,
                      bc_user_id: int, bc_user_email: str,
                      access_token_expires_at: float,
                      actor_id: str = "basecamp_connect_web_flow") -> None:
    """Write/overwrite the operator's BC AI OAuth grant.

    Called by the callback in app/routers/basecamp_connect.py after
    successful identity verification (the access_token's /my/profile.json
    confirmed bc_user_email matches the user's bc_ai_user_email).
    """
    if not refresh_token or not refresh_token.strip():
        raise ValueError("refresh_token must be non-empty")
    if not access_token or not access_token.strip():
        raise ValueError("access_token must be non-empty")
    blob = json.dumps({
        "v": 1,
        "refresh_token": refresh_token,
        "access_token": access_token,
        "access_token_expires_at": float(access_token_expires_at),
        "bc_user_id": int(bc_user_id) if bc_user_id else 0,
        "bc_user_email": (bc_user_email or "").strip().lower(),
    })
    vault.store_secret(
        user.user_id, VAULT_TOOL_NAME, blob,
        caller_id=actor_id, ttl_days=180,
    )
    invalidate_access_token_cache(user)


def get_grant_metadata(user) -> Optional[dict]:
    """Public-safe view of the stored grant: identity + expiry only.
    Tokens are NOT returned. Used by the /profile/connect-basecamp-ai
    landing page to show "Connected as X AI · expires Y".
    """
    grant = _read_stored_grant(user)
    if not grant:
        return None
    if "_legacy_access_token" in grant:
        return {"legacy": True, "bc_user_email": None,
                "bc_user_id": None, "access_token_expires_at": None}
    return {
        "legacy": False,
        "bc_user_email": grant.get("bc_user_email"),
        "bc_user_id": grant.get("bc_user_id"),
        "access_token_expires_at": grant.get("access_token_expires_at"),
    }
