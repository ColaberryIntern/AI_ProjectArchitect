"""Unit tests for basecamp_oauth_token: refresh-exchange, vault read/write,
cache, error mapping. No network — urllib.request.urlopen is patched.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import urllib.error

import pytest

from execution.products.library import basecamp_oauth_token


class _FakeUser:
    user_id = "usr-test-bcoauth"
    email = "ali@colaberry.com"
    bc_ai_user_email = "ali-ai@colaberry.com"


@pytest.fixture
def fake_user():
    basecamp_oauth_token.invalidate_access_token_cache(_FakeUser())
    return _FakeUser()


def _http_error(code: int, body: bytes = b""):
    return urllib.error.HTTPError(
        "https://launchpad.37signals.com/authorization/token",
        code, f"HTTP {code}", {}, MagicMock(read=lambda: body),
    )


def _wrapped_grant(*, refresh="RT", access="AT", expires_at=0,
                   bc_user_id=111, bc_user_email="ali-ai@colaberry.com"):
    return json.dumps({
        "v": 1, "refresh_token": refresh, "access_token": access,
        "access_token_expires_at": float(expires_at),
        "bc_user_id": bc_user_id, "bc_user_email": bc_user_email,
    })


# ── _client_credentials ────────────────────────────────────────────────


def test_client_credentials_reads_env(monkeypatch):
    monkeypatch.setenv("BASECAMP_OAUTH_CLIENT_ID", "bc-cid")
    monkeypatch.setenv("BASECAMP_OAUTH_CLIENT_SECRET", "bc-sec")
    cid, sec = basecamp_oauth_token._client_credentials()
    assert cid == "bc-cid"
    assert sec == "bc-sec"


def test_client_credentials_missing_raises(monkeypatch):
    monkeypatch.delenv("BASECAMP_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("BASECAMP_OAUTH_CLIENT_SECRET", raising=False)
    with pytest.raises(basecamp_oauth_token.OAuthError) as exc:
        basecamp_oauth_token._client_credentials()
    assert exc.value.code == "basecamp_oauth_app_not_configured"


# ── _read_stored_grant ─────────────────────────────────────────────────


def test_read_stored_grant_none_when_vault_empty(fake_user, monkeypatch):
    monkeypatch.setattr(basecamp_oauth_token.vault, "read_secret",
                        MagicMock(side_effect=KeyError()))
    assert basecamp_oauth_token._read_stored_grant(fake_user) is None


def test_read_stored_grant_wrapped(fake_user, monkeypatch):
    monkeypatch.setattr(basecamp_oauth_token.vault, "read_secret",
                        MagicMock(return_value=_wrapped_grant(expires_at=99999)))
    g = basecamp_oauth_token._read_stored_grant(fake_user)
    assert g["refresh_token"] == "RT"
    assert g["bc_user_email"] == "ali-ai@colaberry.com"


def test_read_stored_grant_legacy_bare_string(fake_user, monkeypatch):
    """Pre-OAuth admin-paste-form entries are bare access tokens with no
    refresh. We surface them as legacy so callers can prompt re-consent."""
    monkeypatch.setattr(basecamp_oauth_token.vault, "read_secret",
                        MagicMock(return_value="bare-legacy-token"))
    g = basecamp_oauth_token._read_stored_grant(fake_user)
    assert g == {"_legacy_access_token": "bare-legacy-token"}


# ── get_access_token_for_operator ──────────────────────────────────────


def test_get_access_token_no_grant_raises(fake_user, monkeypatch):
    monkeypatch.setattr(basecamp_oauth_token.vault, "read_secret",
                        MagicMock(side_effect=KeyError()))
    with pytest.raises(basecamp_oauth_token.OAuthError) as exc:
        basecamp_oauth_token.get_access_token_for_operator(fake_user)
    assert exc.value.code == "no_basecamp_oauth_grant"


def test_get_access_token_legacy_grant_raises(fake_user, monkeypatch):
    monkeypatch.setattr(basecamp_oauth_token.vault, "read_secret",
                        MagicMock(return_value="bare-token"))
    with pytest.raises(basecamp_oauth_token.OAuthError) as exc:
        basecamp_oauth_token.get_access_token_for_operator(fake_user)
    assert exc.value.code == "basecamp_grant_legacy_no_refresh"


def test_get_access_token_uses_cached_when_fresh(fake_user, monkeypatch):
    """If the stored access_token is still well under expiry, we should
    NOT call /token at all. Saves both latency and refresh-token rotation
    pressure on Basecamp."""
    future = 9999999999.0
    monkeypatch.setattr(basecamp_oauth_token.vault, "read_secret",
                        MagicMock(return_value=_wrapped_grant(
                            access="CACHED-AT", expires_at=future)))
    monkeypatch.setenv("BASECAMP_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("BASECAMP_OAUTH_CLIENT_SECRET", "sec")

    with patch("urllib.request.urlopen") as mock_urlopen:
        at = basecamp_oauth_token.get_access_token_for_operator(fake_user)
        assert at == "CACHED-AT"
        mock_urlopen.assert_not_called()


def test_get_access_token_refreshes_when_expired(fake_user, monkeypatch):
    monkeypatch.setattr(basecamp_oauth_token.vault, "read_secret",
                        MagicMock(return_value=_wrapped_grant(
                            access="OLD-AT", expires_at=0)))
    monkeypatch.setenv("BASECAMP_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("BASECAMP_OAUTH_CLIENT_SECRET", "sec")
    store_spy = MagicMock()
    monkeypatch.setattr(basecamp_oauth_token.vault, "store_secret", store_spy)

    captured = {}
    class _FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return json.dumps({
            "access_token": "NEW-AT",
            "refresh_token": "NEW-RT",
            "expires_in": 1209600,
        }).encode()
    def _fake_urlopen(req, timeout):
        captured["data"] = req.data.decode()
        return _FakeResp()

    with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        at = basecamp_oauth_token.get_access_token_for_operator(fake_user)
    assert at == "NEW-AT"
    assert "type=refresh" in captured["data"]
    assert "client_id=cid" in captured["data"]
    assert store_spy.called
    written = json.loads(store_spy.call_args.args[2])
    assert written["access_token"] == "NEW-AT"
    assert written["refresh_token"] == "NEW-RT"


def test_get_access_token_invalid_grant_surfaces_reconnect_code(fake_user, monkeypatch):
    monkeypatch.setattr(basecamp_oauth_token.vault, "read_secret",
                        MagicMock(return_value=_wrapped_grant(expires_at=0)))
    monkeypatch.setenv("BASECAMP_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("BASECAMP_OAUTH_CLIENT_SECRET", "sec")
    body = json.dumps({"error": "invalid_grant"}).encode()
    with patch("urllib.request.urlopen", side_effect=_http_error(400, body)):
        with pytest.raises(basecamp_oauth_token.OAuthError) as exc:
            basecamp_oauth_token.get_access_token_for_operator(fake_user)
    assert exc.value.code == "basecamp_grant_invalid"


# ── store_oauth_grant ──────────────────────────────────────────────────


def test_store_oauth_grant_writes_wrapped(fake_user, monkeypatch):
    captured = {}
    def _fake_store(uid, tool, val, *, caller_id, ttl_days):
        captured["val"] = val
    monkeypatch.setattr(basecamp_oauth_token.vault, "store_secret", _fake_store)
    basecamp_oauth_token.store_oauth_grant(
        fake_user,
        access_token="AT", refresh_token="RT",
        bc_user_id=12345, bc_user_email="ALI-AI@Colaberry.com",
        access_token_expires_at=1700000000.0,
    )
    blob = json.loads(captured["val"])
    assert blob["access_token"] == "AT"
    assert blob["refresh_token"] == "RT"
    assert blob["bc_user_id"] == 12345
    assert blob["bc_user_email"] == "ali-ai@colaberry.com"


def test_store_oauth_grant_rejects_empty_refresh(fake_user, monkeypatch):
    monkeypatch.setattr(basecamp_oauth_token.vault, "store_secret", MagicMock())
    with pytest.raises(ValueError):
        basecamp_oauth_token.store_oauth_grant(
            fake_user, access_token="AT", refresh_token="",
            bc_user_id=1, bc_user_email="x@y", access_token_expires_at=0,
        )


# ── get_grant_metadata ─────────────────────────────────────────────────


def test_get_grant_metadata_returns_none_when_absent(fake_user, monkeypatch):
    monkeypatch.setattr(basecamp_oauth_token.vault, "read_secret",
                        MagicMock(side_effect=KeyError()))
    assert basecamp_oauth_token.get_grant_metadata(fake_user) is None


def test_get_grant_metadata_redacts_tokens(fake_user, monkeypatch):
    monkeypatch.setattr(basecamp_oauth_token.vault, "read_secret",
                        MagicMock(return_value=_wrapped_grant(
                            refresh="VERY-SECRET-RT",
                            access="VERY-SECRET-AT")))
    meta = basecamp_oauth_token.get_grant_metadata(fake_user)
    assert meta["bc_user_email"] == "ali-ai@colaberry.com"
    assert meta["bc_user_id"] == 111
    assert "refresh_token" not in meta
    assert "access_token" not in meta
    assert "VERY-SECRET" not in json.dumps(meta)


def test_get_grant_metadata_flags_legacy(fake_user, monkeypatch):
    monkeypatch.setattr(basecamp_oauth_token.vault, "read_secret",
                        MagicMock(return_value="bare-legacy"))
    meta = basecamp_oauth_token.get_grant_metadata(fake_user)
    assert meta["legacy"] is True
    assert meta["bc_user_email"] is None


# ── Redaction guarantee ────────────────────────────────────────────────


def test_no_log_line_contains_refresh_token(fake_user, monkeypatch, caplog):
    import logging
    monkeypatch.setattr(basecamp_oauth_token.vault, "read_secret",
                        MagicMock(return_value=_wrapped_grant(
                            refresh="LEAK-CANARY-RT", expires_at=0)))
    monkeypatch.setattr(basecamp_oauth_token.vault, "store_secret", MagicMock())
    monkeypatch.setenv("BASECAMP_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("BASECAMP_OAUTH_CLIENT_SECRET", "SECRET-CANARY")

    caplog.set_level(logging.DEBUG)
    with patch("urllib.request.urlopen", side_effect=_http_error(500)):
        with pytest.raises(basecamp_oauth_token.OAuthError):
            basecamp_oauth_token.get_access_token_for_operator(fake_user)
    for record in caplog.records:
        msg = record.getMessage()
        assert "LEAK-CANARY-RT" not in msg
        assert "SECRET-CANARY" not in msg
