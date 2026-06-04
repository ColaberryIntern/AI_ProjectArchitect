"""[Karun 3 + Kes 3] Pilot dash SMTP delivery tests.

Covers:
    1. PILOT_DASH_DELIVERY=0 → disabled, no SMTP call
    2. PILOT_DASH_TEST_MODE=1 (default) → only Ali, not the DRI
    3. PILOT_DASH_TEST_MODE=0 → Ali AND DRI
    4. Missing SMTP creds → 'skipped_no_creds' (delivery enabled but
       credentials not yet populated)
    5. Recipient config missing for a DRI → graceful failure
    6. Successful path → SMTP login + sendmail called with the right
       envelope (verified via mock factory)
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from execution.products.pilot import delivery


@pytest.fixture
def tmp_html(tmp_path):
    """Write a tiny placeholder dashboard HTML for the delivery to send."""
    p = tmp_path / "2026-06-04.html"
    p.write_text("<!doctype html><html><body><h1>Test dash</h1></body></html>", encoding="utf-8")
    return str(p)


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("PILOT_DASH_DELIVERY", "1")


@pytest.fixture
def test_mode(monkeypatch):
    monkeypatch.setenv("PILOT_DASH_TEST_MODE", "1")


@pytest.fixture
def live_mode(monkeypatch):
    monkeypatch.setenv("PILOT_DASH_TEST_MODE", "0")


@pytest.fixture
def with_creds(monkeypatch):
    monkeypatch.setenv("GMAIL_SMTP_USERNAME", "ali@colaberry.com")
    monkeypatch.setenv("GMAIL_SMTP_APP_PASSWORD", "test-app-password")


@pytest.fixture
def no_creds(monkeypatch):
    monkeypatch.delenv("GMAIL_SMTP_USERNAME", raising=False)
    monkeypatch.delenv("GMAIL_SMTP_APP_PASSWORD", raising=False)


# ── Disabled by default (safety net) ───────────────────────────────


def test_disabled_by_default(monkeypatch, tmp_html):
    monkeypatch.delenv("PILOT_DASH_DELIVERY", raising=False)
    result = delivery.send_dashboard("karun", tmp_html)
    assert result.status == "disabled"
    assert result.recipients == []


# ── TEST_MODE → only Ali ───────────────────────────────────────────


def test_test_mode_sends_only_to_ali(enabled, test_mode, with_creds, tmp_html):
    smtp = MagicMock()
    factory = MagicMock(return_value=smtp)
    smtp.__enter__ = MagicMock(return_value=smtp)
    smtp.__exit__ = MagicMock(return_value=False)

    result = delivery.send_dashboard("karun", tmp_html, _smtp_factory=factory)

    assert result.status == "ok"
    assert result.recipients == ["ali@colaberry.com"]
    # Verify sendmail was called with Ali only
    args, _ = smtp.sendmail.call_args
    from_addr, to_addrs, body = args
    assert from_addr == "ali@colaberry.com"
    assert to_addrs == ["ali@colaberry.com"]
    assert "karun@colaberry.com" not in body or "To: " not in body.split("\n", 5)[3]


# ── Live mode → Ali AND DRI ────────────────────────────────────────


def test_live_mode_sends_to_ali_and_dri(enabled, live_mode, with_creds, tmp_html):
    smtp = MagicMock()
    factory = MagicMock(return_value=smtp)
    smtp.__enter__ = MagicMock(return_value=smtp)
    smtp.__exit__ = MagicMock(return_value=False)

    result = delivery.send_dashboard("karun", tmp_html, _smtp_factory=factory)

    assert result.status == "ok"
    assert set(result.recipients) == {"ali@colaberry.com", "karun@colaberry.com"}


def test_kes_live_mode_uses_kes_recipient(enabled, live_mode, with_creds, tmp_html):
    smtp = MagicMock()
    factory = MagicMock(return_value=smtp)
    smtp.__enter__ = MagicMock(return_value=smtp)
    smtp.__exit__ = MagicMock(return_value=False)

    result = delivery.send_dashboard("kes", tmp_html, _smtp_factory=factory)

    assert result.status == "ok"
    assert "kes@colaberry.com" in result.recipients
    assert "ali@colaberry.com" in result.recipients


# ── Missing creds (delivery enabled but env not set) ───────────────


def test_enabled_but_no_creds(enabled, test_mode, no_creds, tmp_html):
    result = delivery.send_dashboard("karun", tmp_html)
    assert result.status == "skipped_no_creds"
    assert "GMAIL_SMTP_USERNAME" in result.error


# ── Unknown DRI ────────────────────────────────────────────────────


def test_unknown_dri_fails(enabled, test_mode, with_creds, tmp_html):
    result = delivery.send_dashboard("unknown-person", tmp_html)
    assert result.status == "failed"
    assert "no recipients configured" in result.error


# ── HTML file missing ──────────────────────────────────────────────


def test_missing_html_file_fails(enabled, test_mode, with_creds, tmp_path):
    result = delivery.send_dashboard("karun", str(tmp_path / "does-not-exist.html"))
    assert result.status == "failed"
    assert "could not read html" in result.error


# ── Subject line includes the date ─────────────────────────────────


def test_subject_includes_date(enabled, test_mode, with_creds, tmp_html):
    smtp = MagicMock()
    factory = MagicMock(return_value=smtp)
    smtp.__enter__ = MagicMock(return_value=smtp)
    smtp.__exit__ = MagicMock(return_value=False)

    delivery.send_dashboard("karun", tmp_html, date_iso="2026-06-09T08:30:00Z",
                                          _smtp_factory=factory)

    args, _ = smtp.sendmail.call_args
    body = args[2]
    assert "Subject:" in body
    # MIME-encoded subject; the literal '2026-06-09' substring should appear
    assert "2026-06-09" in body


# ── SMTP raises → graceful failure ─────────────────────────────────


def test_smtp_failure_returns_failed(enabled, test_mode, with_creds, tmp_html):
    smtp = MagicMock()
    factory = MagicMock(return_value=smtp)
    smtp.__enter__ = MagicMock(return_value=smtp)
    smtp.__exit__ = MagicMock(return_value=False)
    smtp.login.side_effect = Exception("auth failed")

    result = delivery.send_dashboard("karun", tmp_html, _smtp_factory=factory)
    assert result.status == "failed"
    assert "auth failed" in result.error
