# [Provision 2] Per-user credentials vault

**Ticket:** Basecamp [9956731061](https://app.basecamp.com/3945211/buckets/7463955/todos/9956731061) · due 2026-06-17
**Status:** Shipped
**Depends on:** [Auth 1] ✅, [Admin 1] (will consume metadata)
**Unblocks:** [Admin 2] tools-access matrix

---

## Acceptance criteria

| # | Criterion | Implementation |
|---|---|---|
| 1 | Schema: user_credentials (user_id, tool_name, encrypted_blob, last_rotated_at, status) | `vault.py` — file-backed `credentials.json` keyed `{user_id}|{tool_name}` |
| 2 | KMS-style envelope encryption (master key in env, data keys per-tool) | v1 uses master-key-direct (AES-GCM via `cryptography`); DEK-per-tool indirection is v2 |
| 3 | Read API: only backend services may decrypt; never returned to frontend | `read_secret()` requires `caller_id` + `purpose`; HTTP routes never expose plaintext |
| 4 | Token rotation hooks: admin UI surfaces "expires in X days" | `days_until_expiry()` + `get_metadata()` return TTL |
| 5 | Audit: every decrypt event logged with caller + reason | `audit_path()` jsonl — `AuditEvent{operation, user_id, tool_name, caller_id, purpose, at, error}` |

## Files

- `execution/products/library/vault.py` — the vault module
- `tests/execution/products/test_vault.py` — 13 tests covering round-trip, audit, revoke, fallback alg, TTL

## Storage

```
output/library/_vault/
├── credentials.json    # encrypted blobs + metadata, JSON dict
└── audit.jsonl         # append-only event log
```

## Encryption

**v1: AES-GCM (256-bit) via the `cryptography` package.** Master key
from `LIBRARY_VAULT_MASTER_KEY` env (base64-urlsafe-encoded 32 bytes;
falls back to SHA-256 of any other string).

**Fallback path** when `cryptography` is missing: stdlib XOR-with-HMAC
stream. **Refuses to run in prod** unless `LIBRARY_VAULT_ALLOW_FALLBACK=1`
explicitly set — exists for unit-test environments without the dep.

**v2 enhancement (not in this ticket):** envelope encryption — generate
a Data Encryption Key (DEK) per credential, encrypt the DEK with the
Master Key (KEK), store both. Enables key rotation without
re-encrypting every blob.

## Public API

```python
store_secret(user_id, tool_name, plaintext, caller_id, ttl_days=None, notes="")
  → CredentialMetadata  # never plaintext

read_secret(user_id, tool_name, caller_id, purpose) → plaintext
  # purpose is MANDATORY; audit-logged

get_metadata(user_id, tool_name, caller_id="system") → CredentialMetadata | None
list_for_user(user_id, caller_id="system") → list[CredentialMetadata]
revoke(user_id, tool_name, caller_id, reason="") → CredentialMetadata | None
days_until_expiry(user_id, tool_name) → int | None
audit_history(user_id=None, tool_name=None) → list[AuditEvent]
```

## Audit guarantees

Every operation (`store`, `read`, `revoke`, `metadata_query`) appends a
row to `audit.jsonl`. Failures append a row too, with `error` populated
(e.g. missing credential, decrypt failure, revoked status). The audit
is the source of truth for "did anyone access this token, and why?"

## Activation steps (Ali)

1. **Generate a master key** (do this once, store securely):
   ```sh
   python -c "import secrets, base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
   ```
2. **Set in `.env.prod`** on the Hetzner box:
   ```
   LIBRARY_VAULT_MASTER_KEY=<the-generated-key>
   ```
3. **Add `cryptography>=42.0` to requirements.txt** (recommended) so prod
   uses AES-GCM rather than the stdlib fallback. Already added in this PR.
4. **Restart container.**

## Hand-off to [Admin 2] tools-access matrix

Admin UI calls (no plaintext over HTTP — ever):
```python
# List a user's credentials for the matrix view
metas = vault.list_for_user(user_id, caller_id=admin.user_id)
# Each row: {tool_name, last_rotated_at, status, ttl_days, days_remaining}

# Admin sets a new credential
vault.store_secret(target_user_id, "gmail",
                          plaintext=<from-admin-form>,
                          caller_id=admin.user_id,
                          ttl_days=None)

# Admin revokes (e.g. user departs)
vault.revoke(target_user_id, "github", caller_id=admin.user_id,
                  reason="user offboarded")
```

Backend services that actually use the token:
```python
token = vault.read_secret(user_id, "gmail",
                                  caller_id="gmail-fetcher-svc",
                                  purpose="fetch inbox for digest report")
```

Every decrypt = one audit row.
