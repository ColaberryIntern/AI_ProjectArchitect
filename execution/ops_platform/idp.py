"""Identity provider abstraction.

Scope honesty
-------------
- ``LocalDevProvider`` — real, ships in this module. Accepts user_id + roles
  trustingly. Use ONLY for local development.
- ``OIDCProvider`` — real abstraction. Validates JWTs through the JWT verifier
  if PyJWT + JWKS endpoint are configured.
- ``OktaAdapter`` / ``Auth0Adapter`` / ``CognitoAdapter`` — thin subclasses
  of ``OIDCProvider`` that pre-fill the issuer / audience / jwks_uri so
  customers don't fill those in by hand. They are NOT validated end-to-end
  against a live tenant in this codebase. Marked clearly in module docstrings.

Production wiring sets ``OPS_IDP_PROVIDER`` to one of:
  local_dev | oidc | okta | auth0 | cognito
plus the provider-specific env vars (issuer, audience, jwks_uri).
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod

from execution.ops_platform.identity import IdentityContext

logger = logging.getLogger(__name__)


class IdentityProvider(ABC):
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def authenticate(self, *, token: str | None = None, headers: dict | None = None
                      ) -> IdentityContext | None:
        """Resolve a token or headers into an IdentityContext. Returns None
        if the credentials don't match this provider."""


class LocalDevProvider(IdentityProvider):
    """Trust-the-headers provider for local development.

    Real auth providers REJECT requests with unverified identity headers;
    this provider accepts them. Use ONLY when OPS_ENFORCE_RBAC=false or
    when running scripts/tests.
    """

    def name(self) -> str:
        return "local_dev"

    def authenticate(self, *, token=None, headers=None) -> IdentityContext | None:
        headers = headers or {}
        user_id = headers.get("X-User-Id")
        if not user_id:
            return None
        roles = [r.strip() for r in (headers.get("X-Roles", "") or "").split(",") if r.strip()]
        workspaces = [w.strip() for w in (headers.get("X-Workspaces", "") or "").split(",") if w.strip()]
        return IdentityContext(
            user_id=user_id,
            display_name=headers.get("X-Display-Name", user_id),
            email=headers.get("X-Email", ""),
            department=headers.get("X-Department", ""),
            roles=roles or ["viewer"],
            workspace_ids=workspaces,
            auth_provider="LOCAL_DEV",
            authenticated=True,
        )


class OIDCProvider(IdentityProvider):
    """Generic OIDC provider. Validates JWT bearer tokens against a JWKS
    endpoint when the JWT verifier is available."""

    def __init__(self, *, issuer: str, audience: str, jwks_uri: str,
                 provider_name: str = "oidc") -> None:
        self.issuer = issuer
        self.audience = audience
        self.jwks_uri = jwks_uri
        self._name = provider_name

    def name(self) -> str:
        return self._name

    def authenticate(self, *, token=None, headers=None) -> IdentityContext | None:
        from execution.ops_platform import jwt_verifier
        bearer = token
        if not bearer and headers:
            auth_header = headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                bearer = auth_header[len("Bearer "):]
        if not bearer:
            return None
        result = jwt_verifier.verify(
            bearer, issuer=self.issuer, audience=self.audience,
            jwks_uri=self.jwks_uri,
        )
        if not result.valid:
            return None
        claims = result.claims or {}
        return IdentityContext(
            user_id=str(claims.get("sub") or claims.get("email") or "unknown"),
            display_name=str(claims.get("name") or claims.get("preferred_username") or ""),
            email=str(claims.get("email") or ""),
            department=str(claims.get("department") or ""),
            roles=list(claims.get("roles") or claims.get("groups") or ["viewer"]),
            workspace_ids=list(claims.get("workspace_ids") or []),
            auth_provider=self._name.upper(),
            authenticated=True,
        )


class OktaAdapter(OIDCProvider):
    """Okta convenience subclass. Pre-fills jwks_uri from the Okta domain.

    Verification against a real Okta tenant requires tenant configuration
    (OKTA_DOMAIN, OKTA_AUDIENCE) and is NOT validated end-to-end in this
    codebase. Mark as production-ready only after smoke-testing the
    JWKS pull against your tenant.
    """

    def __init__(self, *, okta_domain: str, audience: str) -> None:
        super().__init__(
            issuer=f"https://{okta_domain.rstrip('/')}",
            audience=audience,
            jwks_uri=f"https://{okta_domain.rstrip('/')}/oauth2/default/v1/keys",
            provider_name="okta",
        )


class Auth0Adapter(OIDCProvider):
    """Auth0 convenience subclass. Not end-to-end validated."""

    def __init__(self, *, auth0_domain: str, audience: str) -> None:
        super().__init__(
            issuer=f"https://{auth0_domain.rstrip('/')}/",
            audience=audience,
            jwks_uri=f"https://{auth0_domain.rstrip('/')}/.well-known/jwks.json",
            provider_name="auth0",
        )


class CognitoAdapter(OIDCProvider):
    """AWS Cognito convenience subclass. Not end-to-end validated."""

    def __init__(self, *, region: str, user_pool_id: str, audience: str) -> None:
        base = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"
        super().__init__(
            issuer=base, audience=audience,
            jwks_uri=f"{base}/.well-known/jwks.json",
            provider_name="cognito",
        )


# ── Provider selection ─────────────────────────────────────────────────


_PROVIDER: IdentityProvider | None = None


def configure(provider: IdentityProvider) -> None:
    global _PROVIDER
    _PROVIDER = provider


def get_provider() -> IdentityProvider:
    global _PROVIDER
    if _PROVIDER is not None:
        return _PROVIDER
    choice = os.environ.get("OPS_IDP_PROVIDER", "local_dev").lower()
    if choice == "okta":
        _PROVIDER = OktaAdapter(
            okta_domain=os.environ.get("OKTA_DOMAIN", ""),
            audience=os.environ.get("OKTA_AUDIENCE", ""),
        )
    elif choice == "auth0":
        _PROVIDER = Auth0Adapter(
            auth0_domain=os.environ.get("AUTH0_DOMAIN", ""),
            audience=os.environ.get("AUTH0_AUDIENCE", ""),
        )
    elif choice == "cognito":
        _PROVIDER = CognitoAdapter(
            region=os.environ.get("AWS_REGION", "us-east-1"),
            user_pool_id=os.environ.get("COGNITO_USER_POOL_ID", ""),
            audience=os.environ.get("COGNITO_AUDIENCE", ""),
        )
    elif choice == "oidc":
        _PROVIDER = OIDCProvider(
            issuer=os.environ.get("OIDC_ISSUER", ""),
            audience=os.environ.get("OIDC_AUDIENCE", ""),
            jwks_uri=os.environ.get("OIDC_JWKS_URI", ""),
        )
    else:
        _PROVIDER = LocalDevProvider()
    return _PROVIDER
