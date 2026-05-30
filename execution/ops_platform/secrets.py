"""Secret management abstraction.

Scope honesty
-------------
- ``EnvProvider`` — reads from os.environ. Real, default.
- ``FileProvider`` — reads from ``output/ops_platform/secrets/{name}.secret``
  files. The file content IS the secret (don't commit). Useful for local dev.
- ``VaultAdapter`` — interface stub for HashiCorp Vault, AWS Secrets Manager,
  Azure Key Vault, etc. Not bundled. Implement ``read(name)`` against your
  client and call ``configure(YourAdapter(...))``.

Important: secrets MUST NEVER be persisted in audit_log. The mask helper
``masked_value(secret)`` returns "***<last4>" for use in any user-facing
explanation.
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from pathlib import Path

from config.settings import OUTPUT_DIR

logger = logging.getLogger(__name__)

_SECRETS_DIR = OUTPUT_DIR / "ops_platform" / "secrets"


class SecretProvider(ABC):
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def read(self, secret_name: str) -> str | None:
        ...


class EnvProvider(SecretProvider):
    def name(self) -> str:
        return "env"

    def read(self, secret_name: str) -> str | None:
        return os.environ.get(secret_name)


class FileProvider(SecretProvider):
    def __init__(self, root: Path | None = None) -> None:
        self._root = root or _SECRETS_DIR

    def name(self) -> str:
        return f"file({self._root})"

    def read(self, secret_name: str) -> str | None:
        path = self._root / f"{secret_name}.secret"
        if not path.exists():
            return None
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            return None


class VaultAdapter(SecretProvider):
    """Interface stub. NOT bundled. Wire your Vault client and override read()."""

    def __init__(self, client=None) -> None:
        if client is None:
            raise NotImplementedError(
                "VaultAdapter requires a vault client. Implement read() against "
                "your client (HashiCorp Vault, AWS Secrets Manager, etc.) and "
                "call secrets.configure(MyVaultAdapter(client))."
            )
        self._client = client

    def name(self) -> str:
        return "vault"

    def read(self, secret_name: str) -> str | None:
        # Subclass / inject implementations override this.
        return None


# ── Module-level singleton ─────────────────────────────────────────────


_PROVIDER: SecretProvider | None = None


def configure(provider: SecretProvider) -> None:
    global _PROVIDER
    _PROVIDER = provider


def get_provider() -> SecretProvider:
    global _PROVIDER
    if _PROVIDER is None:
        choice = os.environ.get("OPS_SECRETS_PROVIDER", "env").lower()
        if choice == "file":
            _PROVIDER = FileProvider()
        else:
            _PROVIDER = EnvProvider()
    return _PROVIDER


def read(secret_name: str) -> str | None:
    return get_provider().read(secret_name)


def masked_value(secret: str | None) -> str:
    """For audit-safe logging. Returns ``***<last4>`` or ``<empty>``."""
    if not secret:
        return "<empty>"
    if len(secret) <= 4:
        return "***"
    return f"***{secret[-4:]}"
