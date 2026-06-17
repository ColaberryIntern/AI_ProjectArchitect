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
    for k in ("GMAIL_SMTP_USERNAME", "GMAIL_SMTP_APP_PASSWORD", "MANDRILL_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert delivery.send_report(html_file).status == "skipped_no_creds"


class _FakeSMTP:
    def __init__(self, sink): self.sink = sink
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def ehlo(self): pass
    def starttls(self): pass
    def login(self, u, p): self.sink["login"] = (u, p)
    def sendmail(self, frm, to, msg):
        self.sink["from"] = frm
        self.sink["envelope"] = to
        self.sink["msg"] = msg


def test_gmail_send_includes_bcc_and_sends_from_ali(monkeypatch, html_file):
    monkeypatch.setenv("PRODUCTIVITY_REPORT_DELIVERY", "1")
    monkeypatch.setenv("GMAIL_SMTP_USERNAME", "bot@colaberry.com")
    monkeypatch.setenv("GMAIL_SMTP_APP_PASSWORD", "secret")
    sent = {}
    res = delivery.send_report(html_file, "2026-06-17T07:30:00Z",
                               _smtp_factory=lambda: _FakeSMTP(sent))
    assert res.status == "ok" and res.transport == "gmail"
    assert sent["login"] == ("bot@colaberry.com", "secret")
    assert sent["from"] == "ali@colaberry.com"            # MAIL FROM is ali
    assert "ali@colaberry.com" in sent["envelope"]
    assert "ops@colaberry.com" in sent["envelope"]        # bcc in envelope


def test_mandrill_path_used_when_only_mandrill_creds_present(monkeypatch, html_file):
    monkeypatch.setenv("PRODUCTIVITY_REPORT_DELIVERY", "1")
    for k in ("GMAIL_SMTP_USERNAME", "GMAIL_SMTP_APP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("MANDRILL_API_KEY", "md-key")
    monkeypatch.setenv("MANDRILL_USERNAME", "ali@colaberry.com")
    sent = {}
    res = delivery.send_report(html_file, "2026-06-17T07:30:00Z",
                               _smtp_factory=lambda: _FakeSMTP(sent))
    assert res.status == "ok" and res.transport == "mandrill"
    assert sent["login"] == ("ali@colaberry.com", "md-key")
    assert "X-MC-Track" in sent["msg"]                    # tracking-off header present
