"""Tests for the Basecamp token-health preflight + the MCP self-heal/shared
self-refresh plumbing it backstops.

No live Basecamp traffic: the whoami probe, grant metadata, user list, and
SMTP send are all injected/monkeypatched. Covers:
  - probe_token: ok / 401 / http_error / network_error / no_token
  - _classify expiry-window logic (refresh vs no-refresh)
  - check_all aggregation across operator + shared + static-env identities
  - send_alert gating (no_alert when healthy, skipped_no_creds, ok send)
  - mcp_tools._bc_token Tier-3 prefers the shared self-refresh token
  - mcp_tools._bc_request self-heals a single 401 then retries
"""
from __future__ import annotations

import contextlib
import urllib.error

import pytest

from execution.products.ops import bc_token_health as th
from execution.products.library import basecamp_oauth_token as bt
from execution.products.library import mcp_tools


# ── probe_token ──────────────────────────────────────────────────────


@contextlib.contextmanager
def _resp(_data=b"{}"):
    class _R:
        def read(self_inner):
            return _data
    yield _R()


def test_probe_ok():
    res = th.probe_token("tok", _opener=lambda req, timeout: _resp())
    assert res.ok and res.status == "ok"


def test_probe_empty_token():
    res = th.probe_token("")
    assert not res.ok and res.status == "no_token"


def test_probe_401_is_unauthorized():
    def opener(req, timeout):
        raise urllib.error.HTTPError(th.WHOAMI_URL, 401, "Unauthorized", {}, None)
    res = th.probe_token("tok", _opener=opener)
    assert not res.ok and res.status == "unauthorized"


def test_probe_500_is_http_error():
    def opener(req, timeout):
        raise urllib.error.HTTPError(th.WHOAMI_URL, 500, "err", {}, None)
    res = th.probe_token("tok", _opener=opener)
    assert not res.ok and res.status == "http_error"


def test_probe_network_error():
    def opener(req, timeout):
        raise urllib.error.URLError("boom")
    res = th.probe_token("tok", _opener=opener)
    assert not res.ok and res.status == "network_error"


# ── _classify ────────────────────────────────────────────────────────


def test_classify_refresh_near_expiry_is_ok():
    # Has refresh_token -> expiry is a non-event.
    days, sev, _ = th._classify(True, 1000.0, now=1000.0, warn_days=3)
    assert sev == "ok"


def test_classify_no_refresh_near_expiry_is_warn():
    exp = 1000.0 + 2 * th.SECONDS_PER_DAY  # 2 days out
    days, sev, _ = th._classify(False, exp, now=1000.0, warn_days=3)
    assert sev == "warn" and 1.9 < days < 2.1


def test_classify_no_refresh_expired_is_critical():
    days, sev, _ = th._classify(False, 500.0, now=1000.0, warn_days=3)
    assert sev == "critical" and days < 0


def test_classify_no_expiry_no_refresh_is_critical():
    _, sev, _ = th._classify(False, None, now=1000.0, warn_days=3)
    assert sev == "critical"


# ── check_all aggregation ────────────────────────────────────────────


class _U:
    def __init__(self, uid, email):
        self.user_id = uid
        self.email = email


def test_check_all_catalogs_operator_and_shared(monkeypatch):
    now = 1_000_000.0
    op = _U("u-obi", "obi@colaberry.com")

    metas = {
        "u-obi": {"legacy": False, "bc_user_email": "obi-ai@colaberry.com",
                  "bc_user_id": 1, "access_token_expires_at": now + 5 * th.SECONDS_PER_DAY},
        bt.SHARED_CB_SYSTEM_USER_ID: {"legacy": False,
                                      "bc_user_email": "vishnu@colaberry.com",
                                      "bc_user_id": 37708014,
                                      "access_token_expires_at": now + 100},
    }

    monkeypatch.setattr(bt, "get_grant_metadata",
                        lambda p: metas.get(getattr(p, "user_id", None)))
    from execution.products.library import tenancy
    monkeypatch.setattr(tenancy, "list_users", lambda active_only=False: [op])

    report = th.check_all(now=now)
    principals = {p.principal: p for p in report.principals}
    assert "u-obi" in principals
    assert bt.SHARED_CB_SYSTEM_USER_ID in principals
    # Both self-refresh -> healthy despite the shared one being near expiry.
    assert report.worst_severity == "ok"


def test_check_all_flags_unmanaged_static_env(monkeypatch):
    now = 1_000_000.0
    # No grants at all -> falls back to static env probe path.
    monkeypatch.setattr(bt, "get_grant_metadata", lambda p: None)
    from execution.products.library import tenancy
    monkeypatch.setattr(tenancy, "list_users", lambda active_only=False: [])
    monkeypatch.setenv("BASECAMP_ACCESS_TOKEN", "static-tok")
    # Probe says the static token currently works -> standing 'warn' (unmanaged).
    report = th.check_all(now=now, probe=lambda t: th.ProbeResult(ok=True, status="ok"))
    static = [p for p in report.principals if p.tier == "static-env"]
    assert static and static[0].severity == "warn"
    assert th.should_alert(report)


# ── send_alert gating ────────────────────────────────────────────────


def test_send_alert_noop_when_healthy():
    report = th.HealthReport(generated_at=0.0, principals=[
        th.PrincipalHealth("u", "u@x", "operator", True, None, None, "ok", "")])
    assert th.send_alert(report)["status"] == "no_alert"


def test_send_alert_skips_without_creds(monkeypatch):
    monkeypatch.delenv("GMAIL_SMTP_USERNAME", raising=False)
    monkeypatch.delenv("GMAIL_SMTP_APP_PASSWORD", raising=False)
    monkeypatch.delenv("MANDRILL_API_KEY", raising=False)
    report = th.HealthReport(generated_at=0.0, principals=[
        th.PrincipalHealth("cb-system", "CB", "shared", False, None, None,
                           "critical", "no refresh")])
    assert th.send_alert(report)["status"] == "skipped_no_creds"


def test_send_alert_sends_via_smtp(monkeypatch):
    monkeypatch.setenv("GMAIL_SMTP_USERNAME", "x@colaberry.com")
    monkeypatch.setenv("GMAIL_SMTP_APP_PASSWORD", "pw")
    sent = {}

    class _SMTP:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def login(self, u, p): sent["login"] = u
        def sendmail(self, frm, to, body):
            sent["to"] = to
            sent["body"] = body

    report = th.HealthReport(generated_at=0.0, principals=[
        th.PrincipalHealth("cb-system", "CB System", "shared", False, None, None,
                           "critical", "no refresh_token")])
    res = th.send_alert(report, _smtp_factory=_SMTP)
    assert res["status"] == "ok"
    assert "kes@colaberry.com" in sent["to"]  # Kes is wired in
    assert "ali@colaberry.com" in sent["to"]
    # Body content is verified via the renderer (MIME-encoded in sent["body"]).
    assert "CB System" in th.render_alert_html(report)


# ── mcp_tools: shared self-refresh + 401 self-heal ───────────────────


def test_bc_token_prefers_shared_self_refresh(monkeypatch):
    monkeypatch.setattr(bt, "get_shared_cb_system_token", lambda: "shared-fresh")
    monkeypatch.setenv("BASECAMP_ACCESS_TOKEN", "static-stale")
    assert mcp_tools._bc_token(user=None) == "shared-fresh"


def test_bc_token_falls_back_to_static_env(monkeypatch):
    monkeypatch.setattr(bt, "get_shared_cb_system_token", lambda: "")
    monkeypatch.setenv("BASECAMP_ACCESS_TOKEN", "static-tok")
    assert mcp_tools._bc_token(user=None) == "static-tok"


def test_bc_request_self_heals_one_401(monkeypatch):
    calls = {"n": 0, "invalidated": 0}

    monkeypatch.setattr(mcp_tools, "_bc_token", lambda user=None: "tok")
    monkeypatch.setattr(mcp_tools, "_invalidate_bc_token_caches",
                        lambda user=None: calls.__setitem__("invalidated",
                                                             calls["invalidated"] + 1))

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"ok": true}'

    def fake_urlopen(req, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)
        return _Resp()

    monkeypatch.setattr(mcp_tools.urllib.request, "urlopen", fake_urlopen)
    out = mcp_tools._bc_request("GET", "https://example/x")
    assert out == {"ok": True}
    assert calls["n"] == 2 and calls["invalidated"] == 1


def test_bc_request_second_401_raises(monkeypatch):
    monkeypatch.setattr(mcp_tools, "_bc_token", lambda user=None: "tok")
    monkeypatch.setattr(mcp_tools, "_invalidate_bc_token_caches", lambda user=None: None)

    def fake_urlopen(req, timeout):
        raise urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)

    monkeypatch.setattr(mcp_tools.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError):
        mcp_tools._bc_request("GET", "https://example/x")
