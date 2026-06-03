"""[Provision 2] Credentials vault tests."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from execution.products.library import vault


@pytest.fixture
def isolated_vault(tmp_path, monkeypatch):
    monkeypatch.setattr(vault, "VAULT_ROOT", tmp_path / "_vault")
    monkeypatch.setenv("LIBRARY_VAULT_MASTER_KEY", "test-master-key-12345-test-master-key-12345")
    yield tmp_path


# ── Round-trip ──────────────────────────────────────────────────


def test_store_then_read_round_trips(isolated_vault):
    meta = vault.store_secret("usr-1", "gmail", "refresh-token-xyz",
                                       caller_id="ali", ttl_days=30,
                                       notes="initial provision")
    assert meta.user_id == "usr-1"
    assert meta.tool_name == "gmail"
    assert meta.status == "active"
    assert meta.ttl_days == 30
    pt = vault.read_secret("usr-1", "gmail", caller_id="gmail-fetcher",
                                    purpose="fetch inbox for digest")
    assert pt == "refresh-token-xyz"


def test_read_requires_purpose(isolated_vault):
    vault.store_secret("usr-2", "github", "ghp_xyz", caller_id="ali")
    with pytest.raises(ValueError):
        vault.read_secret("usr-2", "github", caller_id="x", purpose="")
    with pytest.raises(ValueError):
        vault.read_secret("usr-2", "github", caller_id="x", purpose="   ")


def test_read_missing_credential_raises_keyerror(isolated_vault):
    with pytest.raises(KeyError):
        vault.read_secret("nobody", "nothing", caller_id="x", purpose="test")


def test_revoked_credential_cannot_be_read(isolated_vault):
    vault.store_secret("usr-3", "slack", "slack-token", caller_id="ali")
    vault.revoke("usr-3", "slack", caller_id="ali", reason="user departed")
    with pytest.raises(PermissionError):
        vault.read_secret("usr-3", "slack", caller_id="x", purpose="x")


# ── Encryption alg ──────────────────────────────────────────────


def test_uses_aes_gcm_when_cryptography_available(isolated_vault):
    if not vault._has_cryptography():
        pytest.skip("cryptography lib not installed")
    meta = vault.store_secret("usr-4", "calendar", "tok", caller_id="ali")
    assert meta.encryption_alg == "aes-gcm-256"


def test_fallback_alg_refuses_decrypt_without_explicit_opt_in(isolated_vault, monkeypatch):
    monkeypatch.setattr(vault, "_has_cryptography", lambda: False)
    vault.store_secret("usr-5", "x", "secret", caller_id="ali")
    # Explicit opt-out (default) — read should fail
    monkeypatch.delenv("LIBRARY_VAULT_ALLOW_FALLBACK", raising=False)
    with pytest.raises(Exception):
        vault.read_secret("usr-5", "x", caller_id="x", purpose="test")


def test_fallback_alg_round_trips_with_opt_in(isolated_vault, monkeypatch):
    monkeypatch.setattr(vault, "_has_cryptography", lambda: False)
    monkeypatch.setenv("LIBRARY_VAULT_ALLOW_FALLBACK", "1")
    vault.store_secret("usr-6", "y", "secret-y", caller_id="ali")
    pt = vault.read_secret("usr-6", "y", caller_id="x", purpose="test")
    assert pt == "secret-y"


# ── Audit ───────────────────────────────────────────────────────


def test_every_operation_audit_logged(isolated_vault):
    vault.store_secret("usr-7", "gmail", "tok", caller_id="ali")
    vault.read_secret("usr-7", "gmail", caller_id="svc-a", purpose="fetch inbox")
    vault.read_secret("usr-7", "gmail", caller_id="svc-b", purpose="rotation check")
    vault.revoke("usr-7", "gmail", caller_id="ali", reason="rotation")
    hist = vault.audit_history(user_id="usr-7", tool_name="gmail")
    ops = [e.operation for e in hist]
    assert "store" in ops
    assert ops.count("read") == 2
    assert "revoke" in ops
    # Each read carries its purpose
    reads = [e for e in hist if e.operation == "read"]
    assert any(e.purpose == "fetch inbox" for e in reads)
    assert any(e.purpose == "rotation check" for e in reads)


def test_failed_read_records_error_in_audit(isolated_vault):
    try:
        vault.read_secret("missing", "x", caller_id="x", purpose="why")
    except KeyError:
        pass
    hist = vault.audit_history(user_id="missing", tool_name="x")
    assert any(e.error for e in hist)


# ── Metadata + listing ─────────────────────────────────────────


def test_metadata_returns_no_plaintext(isolated_vault):
    vault.store_secret("usr-8", "tool", "TOPSECRET", caller_id="ali")
    meta = vault.get_metadata("usr-8", "tool")
    assert "TOPSECRET" not in str(meta)
    assert meta.encryption_alg in ("aes-gcm-256", "stdlib-fallback-NOT-FOR-PROD")


def test_list_for_user_returns_only_their_creds(isolated_vault):
    vault.store_secret("usr-A", "gmail", "a", caller_id="ali")
    vault.store_secret("usr-A", "github", "b", caller_id="ali")
    vault.store_secret("usr-B", "gmail", "c", caller_id="ali")
    a_creds = vault.list_for_user("usr-A")
    tool_names = sorted(c.tool_name for c in a_creds)
    assert tool_names == ["github", "gmail"]


def test_days_until_expiry_with_ttl(isolated_vault):
    vault.store_secret("usr-9", "bc", "tok", caller_id="ali", ttl_days=14)
    d = vault.days_until_expiry("usr-9", "bc")
    assert 13 <= d <= 14


def test_days_until_expiry_none_when_no_ttl(isolated_vault):
    vault.store_secret("usr-10", "github-pat", "tok", caller_id="ali")
    assert vault.days_until_expiry("usr-10", "github-pat") is None
