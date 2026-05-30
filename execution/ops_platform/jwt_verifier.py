"""JWT verification with JWKS caching.

Scope honesty
-------------
- Real PyJWT validation when ``import jwt`` succeeds.
- When PyJWT is NOT installed, ``verify()`` returns ``VerificationResult(valid=False,
  reason='PyJWT not installed')``. The platform's auth code degrades to LOCAL_DEV /
  HEADER_AUTH / anonymous in that case.
- JWKS cache is in-memory (per process) with TTL. For multi-host, swap the
  cache for ``shared_cache_backend`` — the public verify() API is unchanged.
- Signature algorithms accepted: RS256, RS384, RS512, ES256, ES384, ES512.
  Symmetric algorithms (HS*) are NOT accepted by default because they imply
  shared secrets — wire those in explicitly only when needed.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from dataclasses import asdict, dataclass

logger = logging.getLogger(__name__)


try:
    import jwt as _pyjwt
    _PYJWT_AVAILABLE = True
except Exception:  # ImportError, plus any vendor metadata oddness
    _pyjwt = None
    _PYJWT_AVAILABLE = False


_DEFAULT_ALLOWED_ALGORITHMS = ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512")
JWKS_CACHE_TTL_SECONDS = 3600


@dataclass
class VerificationResult:
    valid: bool
    reason: str = ""
    claims: dict | None = None
    algorithm: str | None = None
    kid: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def is_available() -> bool:
    return _PYJWT_AVAILABLE


def verify(
    token: str,
    *,
    issuer: str,
    audience: str,
    jwks_uri: str,
    allowed_algorithms: tuple = _DEFAULT_ALLOWED_ALGORITHMS,
) -> VerificationResult:
    if not _PYJWT_AVAILABLE:
        return VerificationResult(valid=False, reason="PyJWT not installed; install with `pip install PyJWT[crypto]`")
    if not token:
        return VerificationResult(valid=False, reason="empty token")
    if not (issuer and audience and jwks_uri):
        return VerificationResult(valid=False,
                                    reason="issuer / audience / jwks_uri must be configured")
    try:
        unverified_header = _pyjwt.get_unverified_header(token)
        kid = unverified_header.get("kid")
        algorithm = unverified_header.get("alg")
    except Exception as e:
        return VerificationResult(valid=False, reason=f"unparseable header: {e}")
    if algorithm not in allowed_algorithms:
        return VerificationResult(valid=False,
                                    reason=f"algorithm {algorithm} is not in the allow-list")

    jwks = _fetch_jwks(jwks_uri)
    if not jwks:
        return VerificationResult(valid=False, reason=f"could not fetch JWKS from {jwks_uri}")
    key = _find_key(jwks, kid)
    if key is None:
        return VerificationResult(valid=False, reason=f"no JWKS key matches kid={kid}")
    try:
        signing_key = _pyjwt.PyJWK(key).key
    except Exception as e:
        return VerificationResult(valid=False, reason=f"JWKS key invalid: {e}")
    try:
        claims = _pyjwt.decode(
            token, signing_key,
            algorithms=list(allowed_algorithms),
            audience=audience, issuer=issuer,
            options={"require": ["exp", "iat"]},
        )
    except Exception as e:  # ExpiredSignatureError, InvalidAudienceError, InvalidIssuerError, etc.
        return VerificationResult(valid=False, reason=f"jwt validation failed: {e}",
                                    algorithm=algorithm, kid=kid)
    return VerificationResult(valid=True, claims=claims, algorithm=algorithm, kid=kid)


# ── JWKS cache ─────────────────────────────────────────────────────────


_JWKS_CACHE: dict[str, tuple[float, dict]] = {}
_JWKS_LOCK = threading.Lock()


def _fetch_jwks(jwks_uri: str) -> dict | None:
    with _JWKS_LOCK:
        cached = _JWKS_CACHE.get(jwks_uri)
        if cached and (time.time() - cached[0]) < JWKS_CACHE_TTL_SECONDS:
            return cached[1]
    try:
        with urllib.request.urlopen(jwks_uri, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.warning("JWKS fetch from %s failed: %s", jwks_uri, e)
        return None
    with _JWKS_LOCK:
        _JWKS_CACHE[jwks_uri] = (time.time(), data)
    return data


def _find_key(jwks: dict, kid: str | None) -> dict | None:
    for k in jwks.get("keys") or []:
        if not kid or k.get("kid") == kid:
            return k
    return None


def clear_jwks_cache() -> None:
    with _JWKS_LOCK:
        _JWKS_CACHE.clear()
