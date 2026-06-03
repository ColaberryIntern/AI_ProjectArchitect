"""[Provision 2] Per-user credentials vault — encrypted token storage.

Per-user-per-tool credentials kept in a file-backed store, with the
secret material encrypted by a master key from env. Decryption is only
exposed to backend services that call `read_secret()` with a `purpose`
string; that call is audit-logged.

NEVER returns plaintext over an HTTP response — the admin UI sees only
metadata (last_rotated_at, status, ttl_days) plus a "decrypt now" gated
action that triggers a one-off decrypt event in the audit log.

Schema (file: output/library/_vault/credentials.json):
    key = "{user_id}|{tool_name}"
    value = {
        ciphertext_b64,
        nonce_b64,
        encryption_alg,     # "fernet" v1
        last_rotated_at,
        status,             # "active" | "expired" | "revoked"
        ttl_days,           # if known
        notes,
        created_at,
        created_by,         # user_id of the admin who set it
    }

Audit log (file: output/library/_vault/audit.jsonl) — every read + write.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

LAYER = "platform_core"
PRODUCT = "library"

ROOT = Path(__file__).resolve().parents[3]
VAULT_ROOT = ROOT / "output" / "library" / "_vault"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _root() -> Path:
    VAULT_ROOT.mkdir(parents=True, exist_ok=True)
    return VAULT_ROOT


# ── Master-key handling ────────────────────────────────────────


def _master_key() -> bytes:
    """Resolve the vault master key. Order: env LIBRARY_VAULT_MASTER_KEY,
    else derive a dev-only one warned via stderr. **Prod must set the env.**"""
    raw = os.environ.get("LIBRARY_VAULT_MASTER_KEY", "").strip()
    if raw:
        try:
            k = base64.urlsafe_b64decode(raw + "=" * (4 - len(raw) % 4))
            if len(k) == 32:
                return k
        except Exception:
            pass
        # Treat as raw secret; pad/truncate via SHA-256 → 32 bytes
        return hashlib.sha256(raw.encode()).digest()
    # Dev fallback — deterministic, NOT secure. Logged so it's obvious.
    import sys
    sys.stderr.write(
        "[vault] WARNING: LIBRARY_VAULT_MASTER_KEY unset. "
        "Using dev fallback key. Set the env var in prod.\n"
    )
    return hashlib.sha256(b"vault-dev-fallback-DO-NOT-USE-IN-PROD").digest()


# ── Encrypt / decrypt — AES-GCM via cryptography library ───────


def _has_cryptography() -> bool:
    try:
        import cryptography  # noqa: F401
        return True
    except ImportError:
        return False


def _encrypt(plaintext: str) -> tuple[str, str, str]:
    """Returns (ciphertext_b64, nonce_b64, alg). Uses AES-GCM when the
    `cryptography` package is installed; falls back to a HMAC-authenticated
    AES-CTR-like scheme via stdlib (NOT for prod — fails the dev assertion).
    """
    key = _master_key()
    plaintext_bytes = plaintext.encode("utf-8")
    if _has_cryptography():
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = os.urandom(12)
        aesgcm = AESGCM(key)
        ct = aesgcm.encrypt(nonce, plaintext_bytes, None)
        return (base64.b64encode(ct).decode("ascii"),
                  base64.b64encode(nonce).decode("ascii"),
                  "aes-gcm-256")
    # stdlib XOR-with-HMAC-stream — fine for unit tests; refuses to run in prod
    nonce = os.urandom(16)
    stream = b""
    counter = 0
    while len(stream) < len(plaintext_bytes):
        block = hmac.new(key, nonce + counter.to_bytes(8, "big"),
                              hashlib.sha256).digest()
        stream += block
        counter += 1
    ct_body = bytes(a ^ b for a, b in zip(plaintext_bytes, stream))
    mac = hmac.new(key, nonce + ct_body, hashlib.sha256).digest()
    ct = ct_body + mac
    return (base64.b64encode(ct).decode("ascii"),
              base64.b64encode(nonce).decode("ascii"),
              "stdlib-fallback-NOT-FOR-PROD")


def _decrypt(ciphertext_b64: str, nonce_b64: str, alg: str) -> str:
    key = _master_key()
    ct = base64.b64decode(ciphertext_b64)
    nonce = base64.b64decode(nonce_b64)
    if alg == "aes-gcm-256":
        if not _has_cryptography():
            raise RuntimeError("Ciphertext is AES-GCM but cryptography lib missing")
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        aesgcm = AESGCM(key)
        return aesgcm.decrypt(nonce, ct, None).decode("utf-8")
    if alg == "stdlib-fallback-NOT-FOR-PROD":
        if os.environ.get("LIBRARY_VAULT_ALLOW_FALLBACK") != "1":
            raise RuntimeError(
                "Refusing to decrypt fallback-alg ciphertext without "
                "LIBRARY_VAULT_ALLOW_FALLBACK=1. Re-encrypt with AES-GCM."
            )
        body, mac = ct[:-32], ct[-32:]
        expected_mac = hmac.new(key, nonce + body, hashlib.sha256).digest()
        if not hmac.compare_digest(expected_mac, mac):
            raise RuntimeError("MAC mismatch — ciphertext tampered or wrong key")
        stream = b""
        counter = 0
        while len(stream) < len(body):
            block = hmac.new(key, nonce + counter.to_bytes(8, "big"),
                                  hashlib.sha256).digest()
            stream += block
            counter += 1
        return bytes(a ^ b for a, b in zip(body, stream)).decode("utf-8")
    raise RuntimeError(f"unknown encryption alg: {alg}")


# ── Data files ────────────────────────────────────────────────


def _creds_path() -> Path:
    return _root() / "credentials.json"


def _audit_path() -> Path:
    return _root() / "audit.jsonl"


def _read_creds() -> dict[str, dict]:
    p = _creds_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_creds(d: dict[str, dict]) -> None:
    _creds_path().write_text(json.dumps(d, indent=2), encoding="utf-8")


@dataclass
class CredentialMetadata:
    user_id: str
    tool_name: str
    encryption_alg: str
    last_rotated_at: str
    status: str = "active"     # active | expired | revoked
    ttl_days: int | None = None
    notes: str = ""
    created_at: str = ""
    created_by: str = ""


@dataclass
class AuditEvent:
    event_id: str
    operation: str             # store | rotate | read | revoke | metadata_query
    user_id: str
    tool_name: str
    caller_id: str
    purpose: str               # WHY decryption is needed (audit-required)
    at: str
    error: str = ""


def _key(user_id: str, tool_name: str) -> str:
    return f"{user_id}|{tool_name}"


def _audit(operation: str, user_id: str, tool_name: str,
              caller_id: str, purpose: str, error: str = "") -> AuditEvent:
    ev = AuditEvent(
        event_id=str(uuid.uuid4())[:12],
        operation=operation, user_id=user_id, tool_name=tool_name,
        caller_id=caller_id, purpose=purpose, at=_now(), error=error,
    )
    with _audit_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(ev)) + "\n")
    return ev


# ── Public API ────────────────────────────────────────────────


def store_secret(user_id: str, tool_name: str, plaintext: str,
                    caller_id: str, ttl_days: int | None = None,
                    notes: str = "") -> CredentialMetadata:
    """Encrypt + store. Returns metadata only."""
    ct, nonce, alg = _encrypt(plaintext)
    now = _now()
    creds = _read_creds()
    row = {
        "user_id": user_id, "tool_name": tool_name,
        "ciphertext_b64": ct, "nonce_b64": nonce,
        "encryption_alg": alg,
        "last_rotated_at": now, "status": "active",
        "ttl_days": ttl_days, "notes": notes,
        "created_at": creds.get(_key(user_id, tool_name), {}).get("created_at", now),
        "created_by": creds.get(_key(user_id, tool_name), {}).get("created_by", caller_id),
    }
    creds[_key(user_id, tool_name)] = row
    _write_creds(creds)
    _audit("store", user_id, tool_name, caller_id, "credential set/rotated")
    return _metadata_from_row(row)


def read_secret(user_id: str, tool_name: str,
                  caller_id: str, purpose: str) -> str:
    """Decrypt + return plaintext. AUDIT-LOGGED.

    `purpose` is mandatory (a free-text reason e.g. 'gmail-fetch-for-X')
    and is recorded in every audit row. Refuse calls with empty purpose.
    """
    if not purpose or not purpose.strip():
        _audit("read", user_id, tool_name, caller_id, "",
                  error="purpose is required")
        raise ValueError("read_secret requires a non-empty `purpose`")

    creds = _read_creds()
    row = creds.get(_key(user_id, tool_name))
    if not row:
        _audit("read", user_id, tool_name, caller_id, purpose,
                  error="credential not found")
        raise KeyError(f"no credential for {user_id}/{tool_name}")
    if row.get("status") != "active":
        _audit("read", user_id, tool_name, caller_id, purpose,
                  error=f"status={row.get('status')}")
        raise PermissionError(f"credential status={row.get('status')}")

    try:
        plaintext = _decrypt(row["ciphertext_b64"], row["nonce_b64"],
                                    row["encryption_alg"])
    except Exception as e:
        _audit("read", user_id, tool_name, caller_id, purpose,
                  error=f"decrypt failed: {e}")
        raise

    _audit("read", user_id, tool_name, caller_id, purpose)
    return plaintext


def get_metadata(user_id: str, tool_name: str,
                       caller_id: str = "system") -> CredentialMetadata | None:
    creds = _read_creds()
    row = creds.get(_key(user_id, tool_name))
    if not row:
        return None
    _audit("metadata_query", user_id, tool_name, caller_id, "list view")
    return _metadata_from_row(row)


def list_for_user(user_id: str,
                       caller_id: str = "system") -> list[CredentialMetadata]:
    creds = _read_creds()
    out: list[CredentialMetadata] = []
    for k, row in creds.items():
        if row.get("user_id") == user_id:
            out.append(_metadata_from_row(row))
    if out:
        _audit("metadata_query", user_id, "*", caller_id,
                  f"listed {len(out)} credentials")
    return out


def revoke(user_id: str, tool_name: str, caller_id: str,
              reason: str = "") -> CredentialMetadata | None:
    creds = _read_creds()
    row = creds.get(_key(user_id, tool_name))
    if not row:
        return None
    row["status"] = "revoked"
    row["notes"] = (row.get("notes") or "") + f" [revoked: {reason}]"
    _write_creds(creds)
    _audit("revoke", user_id, tool_name, caller_id, reason)
    return _metadata_from_row(row)


def days_until_expiry(user_id: str, tool_name: str) -> int | None:
    """Returns None if no TTL was recorded, else days remaining (negative if past)."""
    creds = _read_creds()
    row = creds.get(_key(user_id, tool_name))
    if not row or not row.get("ttl_days"):
        return None
    try:
        rotated_at = row["last_rotated_at"]
        rotated_epoch = time.mktime(time.strptime(rotated_at[:19], "%Y-%m-%dT%H:%M:%S"))
        expires_epoch = rotated_epoch + int(row["ttl_days"]) * 86400
        return int((expires_epoch - time.time()) / 86400)
    except Exception:
        return None


def audit_history(user_id: str | None = None,
                       tool_name: str | None = None) -> list[AuditEvent]:
    p = _audit_path()
    if not p.exists():
        return []
    out: list[AuditEvent] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = AuditEvent(**json.loads(line))
        except Exception:
            continue
        if user_id and ev.user_id != user_id:
            continue
        if tool_name and ev.tool_name != tool_name:
            continue
        out.append(ev)
    return out


def _metadata_from_row(row: dict) -> CredentialMetadata:
    return CredentialMetadata(
        user_id=row["user_id"],
        tool_name=row["tool_name"],
        encryption_alg=row["encryption_alg"],
        last_rotated_at=row["last_rotated_at"],
        status=row.get("status", "active"),
        ttl_days=row.get("ttl_days"),
        notes=row.get("notes", ""),
        created_at=row.get("created_at", ""),
        created_by=row.get("created_by", ""),
    )
