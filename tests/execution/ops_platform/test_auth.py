"""Tests for execution/ops_platform/auth.py + identity + session_store."""

from unittest.mock import MagicMock

import pytest

from execution.ops_platform import audit_log, auth, cache_bus, session_store
from execution.ops_platform.identity import anonymous_identity, from_session


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(session_store, "_SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(audit_log, "_AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(cache_bus, "_VERSION_DIR", tmp_path / "versions")
    cache_bus.reset_for_tests()
    yield


def _fake_request(headers: dict, *, client_host: str = "127.0.0.1"):
    req = MagicMock()
    req.headers = headers
    req.client.host = client_host
    return req


def test_anonymous_identity_is_unauthenticated():
    identity = anonymous_identity()
    assert identity.authenticated is False
    assert identity.user_id == "anonymous"
    assert "viewer" in identity.roles


def test_login_creates_session_and_audit():
    identity = auth.login(user_id="alice", roles=["operator"], workspace_ids=["sales"])
    assert identity.authenticated
    assert identity.session_id
    audit_rows = audit_log.list_entries(action="auth.login")
    assert any(r["entity_id"] == identity.session_id for r in audit_rows)


def test_logout_removes_session_and_audit():
    identity = auth.login(user_id="alice", roles=["operator"])
    assert auth.logout(identity.session_id) is True
    audit_rows = audit_log.list_entries(action="auth.logout")
    assert any(r["entity_id"] == identity.session_id for r in audit_rows)


def test_session_resolution_from_x_session_id_header():
    identity = auth.login(user_id="bob", roles=["builder"])
    req = _fake_request({"X-Session-Id": identity.session_id})
    resolved = auth.get_identity_context(req)
    assert resolved.user_id == "bob"
    assert resolved.authenticated


def test_header_auth_path_uses_x_user_id():
    req = _fake_request({"X-User-Id": "carol", "X-Roles": "operator,reviewer",
                          "X-Department": "Sales"})
    resolved = auth.get_identity_context(req)
    assert resolved.user_id == "carol"
    assert resolved.authenticated
    assert "operator" in resolved.roles


def test_no_headers_returns_anonymous():
    req = _fake_request({})
    resolved = auth.get_identity_context(req)
    assert resolved.authenticated is False


def test_static_token_admin_role(monkeypatch):
    monkeypatch.setenv("OPS_STATIC_TOKEN", "secret123")
    req = _fake_request({"Authorization": "Bearer secret123"})
    resolved = auth.get_identity_context(req)
    assert resolved.authenticated
    assert "admin" in resolved.roles


def test_expired_session_returns_none(monkeypatch):
    import json
    from datetime import datetime, timedelta, timezone
    identity = auth.login(user_id="dave")
    # Backdate the expiration
    path = session_store._SESSIONS_DIR / f"{identity.session_id}.json"
    row = json.loads(path.read_text())
    row["expires_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    path.write_text(json.dumps(row))
    assert session_store.get_session(identity.session_id) is None


def test_login_failed_records_audit():
    auth.login_failed(user_id="evil", reason="bad password")
    rows = audit_log.list_entries(action="auth.failed")
    assert rows
