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


def _parse_stored(stored: str) -> tuple[str, str]:
    """Decode a vault-stored credential.

    Two formats coexist for backwards compat:
      - new (wrapped): JSON `{"v":1,"refresh_token":"...","client_type":"web"|"desktop"}`
      - legacy (bare string): just the refresh token; treated as desktop

    Returns (refresh_token, client_type). client_type is always 'web' or
    'desktop'. Unknown values default to desktop so we never accidentally
    swap to a client the operator never consented under.
    """
    if not stored:
        return ("", "desktop")
    s = stored.strip()
    if s.startswith("{"):
        try:
            obj = json.loads(s)
            if isinstance(obj, dict) and obj.get("refresh_token"):
                ct = obj.get("client_type", "desktop")
                if ct not in ("web", "desktop"):
                    ct = "desktop"
                return (obj["refresh_token"], ct)
        except json.JSONDecodeError:
            pass
    return (s, "desktop")


def _client_credentials_for(client_type: str) -> tuple[str, str]:
    """Read OAuth client_id + secret matching the issuing client type.

    Google ties refresh_token validity to the (client_id, client_secret)
    pair that issued it. Mismatched creds at exchange time -> invalid_grant.
    So we must use Web creds for Web-issued tokens and Desktop creds for
    Desktop-issued tokens.

    Web   (in-app /profile/connect-google flow):
        GOOGLE_OAUTH_ATTACHMENT_WEB_CLIENT_ID / _SECRET
    Desktop (CLI bootstrap_google_oauth.py + localhost redirect):
        GOOGLE_OAUTH_ATTACHMENT_CLIENT_ID / _SECRET
        falls back to GOOGLE_OAUTH_CLIENT_ID / _SECRET for legacy compat
    """
    if client_type == "web":
        cid = (os.environ.get("GOOGLE_OAUTH_ATTACHMENT_WEB_CLIENT_ID") or "").strip()
        secret = (os.environ.get("GOOGLE_OAUTH_ATTACHMENT_WEB_CLIENT_SECRET") or "").strip()
        if not cid or not secret:
            raise OAuthError(
                "google_oauth_app_not_configured",
                "GOOGLE_OAUTH_ATTACHMENT_WEB_CLIENT_ID / _SECRET env vars missing on advisor",
            )
        return cid, secret

    cid = (
        os.environ.get("GOOGLE_OAUTH_ATTACHMENT_CLIENT_ID")
        or os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
        or ""
    ).strip()
    secret = (
        os.environ.get("GOOGLE_OAUTH_ATTACHMENT_CLIENT_SECRET")
        or os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
        or ""
    ).strip()
    if not cid or not secret:
        raise OAuthError(
            "google_oauth_app_not_configured",
            "GOOGLE_OAUTH_ATTACHMENT_CLIENT_ID / _SECRET env vars missing on advisor",
        )
    return cid, secret


def _client_credentials() -> tuple[str, str]:
    """Legacy helper. Returns Desktop creds. Kept so existing callers that
    don't know about per-token client_type tagging still resolve. New code
    should use _client_credentials_for(client_type).
    """
    return _client_credentials_for("desktop")


def _read_stored_credential(user, *, caller_id: str, purpose: str) -> Optional[str]:
    """Raw vault read. May return wrapped JSON or legacy bare string.
    Returns None when no vault entry exists.
    """
    try:
        return vault.read_secret(
            user.user_id,
            VAULT_TOOL_NAME,
            caller_id=caller_id,
            purpose=purpose,
        )
    except KeyError:
        return None
    except Exception as e:
        logger.warning("vault read failed for user=%s tool=%s err_type=%s",
                                  user.user_id, VAULT_TOOL_NAME, type(e).__name__)
        raise OAuthError("vault_read_failed", "could not read stored credential")


def get_refresh_token_for_operator(user) -> Optional[str]:
    """Resolve the operator's stored Google refresh token. Returns None when
    no vault entry exists. Transparently unwraps either storage format.

    Callers using this only as a boolean "is this operator connected?"
    check don't need to know about client_type tagging.
    """
    stored = _read_stored_credential(
        user,
        caller_id="mcp_attachment_fetch",
        purpose="exchange for Google access token to fetch attachment",
    )
    if not stored:
        return None
    rt, _ = _parse_stored(stored)
    return rt or None


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

    stored = _read_stored_credential(
        user,
        caller_id="mcp_attachment_fetch",
        purpose="exchange for Google access token to fetch attachment",
    )
    if not stored:
        raise OAuthError(
            "no_google_oauth_grant",
            "operator needs to visit /profile/connect-google or run scripts/bootstrap_google_oauth.py",
        )
    refresh_token, client_type = _parse_stored(stored)
    if not refresh_token:
        raise OAuthError(
            "no_google_oauth_grant",
            "operator needs to visit /profile/connect-google or run scripts/bootstrap_google_oauth.py",
        )

    client_id, client_secret = _client_credentials_for(client_type)
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
                                                                *, client_type: str = "desktop",
                                                                actor_id: str = "bootstrap_script") -> None:
    """Write/overwrite the operator's refresh token in the vault.

    client_type tags which OAuth client issued the refresh_token so the
    runtime knows which env credentials to pair with it at exchange time.
    Web-issued tokens (in-app /profile/connect-google) and Desktop-issued
    tokens (CLI bootstrap) coexist; mixing creds across types fails with
    Google's invalid_grant.

    ttl_days=180 = 6 months; Google revokes idle refresh tokens at
    that point so it doubles as a re-consent reminder.

    Refresh token text is NEVER logged. actor_id appears in the vault's
    audit log; the token does not.
    """
    if not refresh_token or not refresh_token.strip():
        raise ValueError("refresh_token must be non-empty")
    if client_type not in ("web", "desktop"):
        raise ValueError("client_type must be 'web' or 'desktop'")
    blob = json.dumps({
        "v": 1,
        "refresh_token": refresh_token,
        "client_type": client_type,
    })
    vault.store_secret(
        user.user_id,
        VAULT_TOOL_NAME,
        blob,
        caller_id=actor_id,
        ttl_days=180,
    )
    invalidate_access_token_cache(user)
