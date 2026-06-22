"""Tests for enterprise_sync webhook-secret hardening (P1.5)."""

import asyncio

from execution.advisory import enterprise_sync


def test_sign_payload_uses_secret():
    sig = enterprise_sync._sign_payload("body", "s3cret")
    assert sig.startswith("sha256=") and len(sig) > 16


def test_skips_when_secret_unset(monkeypatch):
    """No ENTERPRISE_WEBHOOK_SECRET -> skip (return False), never sign with a default."""
    monkeypatch.delenv("ENTERPRISE_WEBHOOK_SECRET", raising=False)
    result = asyncio.run(enterprise_sync.send_enterprise_event("report.completed", {"x": 1}))
    assert result is False
