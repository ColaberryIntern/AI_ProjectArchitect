"""SMTP delivery — gating, missing creds, and a successful injected send."""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from execution.products.ops.productivity import delivery


@pytest.fixture
def html_file(tmp_path):
    p = tmp_path / "report.html"
    p.write_text("<html><body>hi</body></html>", encoding="utf-8")
    return str(p)


@pytest.fixture(autouse=True)
def _recipients(monkeypatch):
    monkeypatch.setattr(delivery, "_load_recipients", lambda: {
        "to": ["ali@colaberry.com"], "bcc": ["ops@colaberry.com"],
        "subject_prefix": "Productivity & AI leverage", "from_name": "Report",
    })


def test_disabled_by_default(monkeypatch, html_file):
    monkeypatch.delenv("PRODUCTIVITY_REPORT_DELIVERY", raising=False)
    assert delivery.send_report(html_file).status == "disabled"


def test_enabled_without_creds_is_skipped(monkeypatch, html_file):
    monkeypatch.setenv("PRODUCTIVITY_REPORT_DELIVERY", "1")
    monkeypatch.delenv("GMAIL_SMTP_USERNAME", raising=False)
    monkeypatch.delenv("GMAIL_SMTP_APP_PASSWORD", raising=False)
    assert delivery.send_report(html_file).status == "skipped_no_creds"


def test_successful_send_includes_bcc_in_envelope(monkeypatch, html_file):
    monkeypatch.setenv("PRODUCTIVITY_REPORT_DELIVERY", "1")
    monkeypatch.setenv("GMAIL_SMTP_USERNAME", "bot@colaberry.com")
    monkeypatch.setenv("GMAIL_SMTP_APP_PASSWORD", "secret")

    sent = {}

    class _FakeSMTP:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def login(self, u, p): sent["login"] = (u, p)
        def sendmail(self, frm, to, msg): sent["envelope"] = to

    res = delivery.send_report(html_file, "2026-06-17T07:30:00Z",
                               _smtp_factory=lambda: _FakeSMTP())
    assert res.status == "ok"
    # to + bcc both land in the SMTP envelope
    assert "ali@colaberry.com" in sent["envelope"]
    assert "ops@colaberry.com" in sent["envelope"]
    assert sent["login"] == ("bot@colaberry.com", "secret")
