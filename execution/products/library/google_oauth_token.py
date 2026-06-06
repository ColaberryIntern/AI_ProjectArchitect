"""Google OAuth token resolution for per-operator API access.

Per the colaberry-attachment-fetch directive: refresh tokens live in the
advisor's per-user vault (NOT CCPP -- they're advisor-only, single-consumer,
not BC-style 14-day-rotating). Access tokens are derived from refresh tokens
on demand and cached in-process for ~50 minutes; never written to disk.

Public API:
    get_refresh_token_for_operator(user) -> str | None
    get_access_token_for_operator(user) -> str           # raises OAuthError
    invalidate_access_token_cache(user) -> None

Failure-first per the directive:
    - 15s timeout on outbound /token calls
    - One automatic re-exchange on 401 from the downstream API call
      (called externally; this module just provides invalidate)
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

VAULT_TOOL_NAME = "google_oauth_refresh"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
DEFAULT_TIMEOUT = 15.0
# Google access tokens are typically 3600s (1h); refresh ~5 min before
# expiry to avoid mid-call invalidation.
ACCESS_TOKEN_REFRESH_BUFFER_SEC = 300


class OAuthError(Exception):
    """Machine-readable Google OAuth failure.

    Code is a stable string for the tool-result `error` field; message is
    human-readable. NEVER include token material in either field.
    """
    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(f"{code}: {message}" if message else code)


@dataclass(frozen=True)
class _CachedAccessToken:
    access_token: str
    expires_at_epoch: float


# In-process cache keyed by user_id. Access tokens are NOT durable -- we
# don't want to leak even encrypted access tokens to disk; refresh tokens
# do that job. A worker restart re-derives.
_ACCESS_TOKEN_CACHE: dict[str, _CachedAccessToken] = {}
_CACHE_LOCK = threading.Lock()


def _client_credentials() -> tuple[str, str]:
    """Read the OAuth client id + secret from env.

    Prefers GOOGLE_OAUTH_ATTACHMENT_* (the Desktop OAuth client created
    specifically for the attachment-fetch flow -- supports dynamic
    localhost callback ports per Desktop client semantics). Falls back to
    GOOGLE_OAUTH_CLIENT_ID/_SECRET (the SSO Web client) only so existing
    deployments don't break if the new vars haven't been set yet.

    The SSO Web client is the wrong type for this flow -- a Web client
    requires registering each redirect URI exactly, and the bootstrap
    script picks a random localhost port. Use the Desktop client.
    """
    cid = (
        os.environ.get("GOOGLE_OAUTH_ATTACHMENT_CLIENT_ID")
        or os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
        or ""
    )
    secret = (
        os.environ.get("GOOGLE_OAUTH_ATTACHMENT_CLIENT_SECRET")
        or os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
        or ""
    )
    if not cid or not secret:
        raise OAuthError(
            "google_oauth_app_not_configured",
            "GOOGLE_OAUTH_ATTACHMENT_CLIENT_ID / _SECRET env vars missing on advisor",
        )
    return cid, secret


def get_refresh_token_for_operator(user) -> Optional[str]:
    """Resolve the operator's stored Google refresh token. Returns None when
    no vault entry exists -- caller should return the
    `no_google_oauth_grant` error code so the operator knows to bootstrap.
    """
    try:
        plain = vault.read_secret(
            user.user_id,
            VAULT_TOOL_NAME,
            caller_id="mcp_attachment_fetch",
            purpose="exchange for Google access token to fetch attachment",
        )
    except KeyError:
        return None
    except Exception as e:
        # Surface vault internal errors with a stable code; don't include the
        # exception message (might leak path/file info from the vault impl).
        logger.warning("vault read failed for user=%s tool=%s err_type=%s",
                                  user.user_id, VAULT_TOOL_NAME, type(e).__name__)
        raise OAuthError("vault_read_failed", "could not read stored credential")
    return plain or None


def invalidate_access_token_cache(user) -> None:
    """Drop any cached access token for this user. Call before retrying a
    downstream API call that just returned 401 -- the in-cache token might
    be the rejected one and we want to force a fresh exchange.
    """
    with _CACHE_LOCK:
        _ACCESS_TOKEN_CACHE.pop(user.user_id, None)


def get_access_token_for_operator(user) -> str:
    """Return a fresh (or cached, if not near-expiry) Google API access token.

    Flow:
      1. Cache hit + not near expiry -> return cached
      2. Otherwise: read refresh token from vault, POST to Google /token,
         cache + return the new access token.

    Raises OAuthError on any failure.
    """
    with _CACHE_LOCK:
        cached = _ACCESS_TOKEN_CACHE.get(user.user_id)
    if cached and cached.expires_at_epoch > time.time() + ACCESS_TOKEN_REFRESH_BUFFER_SEC:
        return cached.access_token

    refresh_token = get_refresh_token_for_operator(user)
    if not refresh_token:
        raise OAuthError(
            "no_google_oauth_grant",
            "operator needs to run scripts/bootstrap_google_oauth.py",
        )

    client_id, client_secret = _client_credentials()
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
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
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # Google returns 400 with body `{"error":"invalid_grant"}` for
        # revoked/rotated refresh tokens. Surface specifically -- the only
        # fix is re-bootstrap; don't auto-retry.
        body_text = ""
        try:
            body_text = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        if e.code == 400 and "invalid_grant" in body_text:
            raise OAuthError(
                "google_grant_invalid",
                "operator needs to re-run scripts/bootstrap_google_oauth.py",
            )
        # Don't include body_text in the log -- it can contain token state.
        logger.warning("google /token HTTP %s for user=%s", e.code, user.user_id)
        raise OAuthError("google_token_http_error", f"HTTP {e.code} from /token")
    except urllib.error.URLError as e:
        logger.warning("google /token network error for user=%s: %s", user.user_id, type(e).__name__)
        raise OAuthError("google_token_network_error", "could not reach Google /token")
    except json.JSONDecodeError:
        raise OAuthError("google_token_malformed_response", "/token returned non-JSON body")

    access_token = payload.get("access_token")
    if not access_token:
        # Don't echo the payload (might have an `error_description` with
        # token-shaped substrings); just raise the generic code.
        raise OAuthError(
            "google_token_missing_access_token",
            "/token response had no access_token",
        )
    expires_in = int(payload.get("expires_in", 3600))
    expires_at = time.time() + expires_in

    with _CACHE_LOCK:
        _ACCESS_TOKEN_CACHE[user.user_id] = _CachedAccessToken(
            access_token=access_token,
            expires_at_epoch=expires_at,
        )
    return access_token


def store_refresh_token_for_operator(user, refresh_token: str,
                                                                actor_id: str = "bootstrap_script") -> None:
    """Write/overwrite the operator's refresh token in the vault.

    Called by scripts/bootstrap_google_oauth.py after the interactive consent
    flow. ttl_days=180 = 6 months; Google revokes idle refresh tokens at
    that point so it doubles as a re-consent reminder.

    Refresh token text is NEVER logged. The actor_id appears in the vault's
    audit log; the token does not.
    """
    if not refresh_token or not refresh_token.strip():
        raise ValueError("refresh_token must be non-empty")
    vault.store_secret(
        user.user_id,
        VAULT_TOOL_NAME,
        refresh_token,
        caller_id=actor_id,
        ttl_days=180,
    )
    # Clear any cached access token tied to the old refresh token.
    invalidate_access_token_cache(user)
