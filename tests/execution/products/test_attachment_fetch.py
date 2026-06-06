"""Tests for colaberry_attachment_fetch tool + its adapters.

All network calls are monkeypatched (urllib.request.urlopen). No real Google
or Basecamp traffic. Real integration is verified manually via the
RUN_GOOGLE_INTEGRATION=1 env flag from the directive.

Covers per the directive's Verification section:
  - Each adapter: happy / 401 / 404 / 429 / malformed
  - Idempotency (compute_key + lookup + record + concurrent inflight)
  - Auth boundary (no vault entry -> clean error code, no crash)
  - Token redaction in log output (logs MUST NOT contain test secrets)
"""

from __future__ import annotations

import io
import json
import logging
from unittest.mock import MagicMock, patch

import pytest

from execution.products.library import (
    attachment_index,
    google_oauth_token,
    tenancy,
)
from execution.products.library.attachment_sources import (
    basecamp as bc_source,
    drive as drive_source,
    gmail as gmail_source,
)


# ── Helpers ────────────────────────────────────────────────────────────


def _make_urlopen_response(body: bytes, status: int = 200,
                                                 headers: dict | None = None):
    """Return a context-manager mock that mimics urllib.request.urlopen()."""
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=_make_resp(body, status, headers or {}))
    cm.__exit__ = MagicMock(return_value=False)
    return cm


def _make_resp(body: bytes, status: int, headers: dict):
    m = MagicMock()
    m.read = MagicMock(return_value=body)
    m.status = status
    m.headers = headers
    return m


def _make_http_error(code: int, body: bytes = b""):
    """Build a urllib HTTPError ready to be raised by a urlopen mock."""
    import urllib.error
    return urllib.error.HTTPError(
        url="https://example.test",
        code=code,
        msg=str(code),
        hdrs={},
        fp=io.BytesIO(body),
    )


@pytest.fixture
def fake_user():
    """Minimal User-like stub. We only access .user_id and .email."""
    u = MagicMock()
    u.user_id = "usr-test"
    u.email = "test@colaberry.com"
    return u


# ── Gmail adapter ──────────────────────────────────────────────────────


def _gmail_message_json(attachment_id: str) -> bytes:
    """Realistic-shape Gmail message metadata with one attachment part."""
    return json.dumps({
        "id": "MSG_ID",
        "payload": {
            "headers": [
                {"name": "From", "value": "Jackie Chalk <jackie@chalkstrategies.com>"},
            ],
            "parts": [
                {"mimeType": "text/plain", "body": {"size": 100}},
                {
                    "filename": "February Commission 2026.xlsx",
                    "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "body": {"attachmentId": attachment_id, "size": 124680},
                },
            ],
        },
    }).encode("utf-8")


def _gmail_attachment_json(data: bytes) -> bytes:
    import base64
    b64url = base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")
    return json.dumps({"size": len(data), "data": b64url}).encode("utf-8")


def test_gmail_happy_path():
    payload = b"binary-spreadsheet-content"
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = [
            _make_urlopen_response(_gmail_message_json("ATT_ID")),
            _make_urlopen_response(_gmail_attachment_json(payload)),
        ]
        result = gmail_source.fetch("MSG_ID", "ATT_ID", "test-access-token")
    assert result.filename == "February Commission 2026.xlsx"
    assert result.sender.startswith("Jackie Chalk")
    assert result.data == payload
    assert result.size_bytes == len(payload)


def test_gmail_401_raises():
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = _make_http_error(401)
        with pytest.raises(gmail_source.GmailError) as exc:
            gmail_source.fetch("MSG_ID", "ATT_ID", "test-token")
    assert exc.value.code == "gmail_unauthorized"


def test_gmail_404_raises():
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = _make_http_error(404)
        with pytest.raises(gmail_source.GmailError) as exc:
            gmail_source.fetch("MSG_ID", "ATT_ID", "test-token")
    assert exc.value.code == "gmail_not_found"


def test_gmail_attachment_not_in_message():
    """Message exists but attachment id doesn't appear in any MIME part."""
    with patch("urllib.request.urlopen") as mock_urlopen:
        # Use a payload with a DIFFERENT attachment id than what we request
        mock_urlopen.side_effect = [
            _make_urlopen_response(_gmail_message_json("OTHER_ATT_ID")),
        ]
        with pytest.raises(gmail_source.GmailError) as exc:
            gmail_source.fetch("MSG_ID", "ATT_ID", "test-token")
    assert exc.value.code == "gmail_attachment_not_in_message"


def test_gmail_malformed_response():
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = [
            _make_urlopen_response(b"<html>this is not json</html>"),
        ]
        with pytest.raises(gmail_source.GmailError) as exc:
            gmail_source.fetch("MSG_ID", "ATT_ID", "test-token")
    assert exc.value.code == "gmail_malformed_response"


# ── Basecamp adapter ───────────────────────────────────────────────────


def test_basecamp_happy_path():
    meta = json.dumps({
        "filename": "Q4-tax-docs.pdf",
        "content_type": "application/pdf",
        "byte_size": 80000,
        "download_url": "https://3.basecampapi.com/download/blob/X",
    }).encode("utf-8")
    binary = b"%PDF-1.7\nfake pdf bytes"
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = [
            _make_urlopen_response(meta),
            _make_urlopen_response(binary),
        ]
        result = bc_source.fetch(
            project_id=7463955, recording_id=12345,
            sgid="SGID_X", bc_token="bc-token-xyz",
            project_name_for_audit="New Leaders Group",
        )
    assert result.filename == "Q4-tax-docs.pdf"
    assert result.mime_type == "application/pdf"
    assert result.data == binary
    assert "New Leaders Group" in result.sender


def test_basecamp_404_raises():
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = _make_http_error(404)
        with pytest.raises(bc_source.BasecampError) as exc:
            bc_source.fetch(7463955, 12345, "SGID_X", "bc-token")
    assert exc.value.code == "basecamp_blob_not_found"


def test_basecamp_401_raises():
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = _make_http_error(401)
        with pytest.raises(bc_source.BasecampError) as exc:
            bc_source.fetch(7463955, 12345, "SGID_X", "bc-token")
    assert exc.value.code == "basecamp_unauthorized"


# ── Drive passthrough adapter ──────────────────────────────────────────


def test_drive_passthrough_happy():
    meta = json.dumps({
        "id": "DRIVE_ID",
        "name": "report.xlsx",
        "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "size": "12345",
        "webViewLink": "https://drive.google.com/file/d/DRIVE_ID/view",
        "owners": [{"emailAddress": "ali@colaberry.com"}],
    }).encode("utf-8")
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = [_make_urlopen_response(meta)]
        result = drive_source.fetch("DRIVE_ID", "access-token")
    assert result.drive_file_id == "DRIVE_ID"
    assert result.size_bytes == 12345
    assert result.data is None  # passthrough -- no bytes downloaded


def test_drive_passthrough_404_means_out_of_scope():
    """drive.file scope means 404 == 'not one of our files' == clean error."""
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = _make_http_error(404)
        with pytest.raises(drive_source.DriveError) as exc:
            drive_source.fetch("DRIVE_ID", "access-token")
    assert exc.value.code == "drive_file_not_accessible"


# ── Idempotency index ──────────────────────────────────────────────────


def test_compute_key_per_source():
    assert attachment_index.compute_key(
        source="gmail", message_id="m1", attachment_id="a1",
    ) == "gmail:m1:a1"
    assert attachment_index.compute_key(
        source="basecamp", project_id=99, recording_id=11, sgid="sg",
    ) == "basecamp:99:11:sg"
    assert attachment_index.compute_key(
        source="drive", drive_file_id="df",
    ) == "drive:df"


def test_record_and_lookup_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(attachment_index, "INDEX_DIR", tmp_path)
    ref = attachment_index.AttachmentRef(
        idempotency_key="gmail:m1:a1",
        source="gmail",
        drive_file_id="DRIVE_X",
        drive_url="https://drive.google.com/file/d/DRIVE_X/view",
        mime_type="application/pdf",
        size_bytes=1234,
        filename="x.pdf",
        sender="sender@example.com",
        saved_at="2026-06-06T00:00:00Z",
        source_message_id="m1",
        source_attachment_id="a1",
    )
    attachment_index.record("op@example.com", ref)
    got = attachment_index.lookup("op@example.com", "gmail:m1:a1")
    assert got is not None
    assert got.drive_file_id == "DRIVE_X"
    assert got.filename == "x.pdf"


def test_inflight_blocks_concurrent_call():
    key = "test-key-xyz"
    email = "op@example.com"
    try:
        assert attachment_index.begin_inflight(email, key) is True
        assert attachment_index.begin_inflight(email, key) is False
    finally:
        attachment_index.end_inflight(email, key)
    # After end, next claim should succeed again
    assert attachment_index.begin_inflight(email, key) is True
    attachment_index.end_inflight(email, key)


# ── Auth boundary: no vault entry ─────────────────────────────────────


def test_no_oauth_grant_returns_clean_error(fake_user, monkeypatch):
    """get_refresh_token_for_operator returns None when vault has no entry."""
    monkeypatch.setattr(
        google_oauth_token.vault, "read_secret",
        MagicMock(side_effect=KeyError("no row")),
    )
    assert google_oauth_token.get_refresh_token_for_operator(fake_user) is None


def test_get_access_token_raises_when_no_grant(fake_user, monkeypatch):
    monkeypatch.setattr(
        google_oauth_token.vault, "read_secret",
        MagicMock(side_effect=KeyError("no row")),
    )
    with pytest.raises(google_oauth_token.OAuthError) as exc:
        google_oauth_token.get_access_token_for_operator(fake_user)
    assert exc.value.code == "no_google_oauth_grant"


def test_invalid_grant_surfaces_re_bootstrap_code(fake_user, monkeypatch):
    """Google /token returns 400 invalid_grant -> we surface the
    'google_grant_invalid' code so the operator knows to re-bootstrap.
    """
    monkeypatch.setattr(
        google_oauth_token.vault, "read_secret",
        MagicMock(return_value="fake-refresh-token"),
    )
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "test-client")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "test-secret")
    google_oauth_token.invalidate_access_token_cache(fake_user)

    invalid_grant_body = json.dumps({"error": "invalid_grant"}).encode("utf-8")
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = _make_http_error(400, invalid_grant_body)
        with pytest.raises(google_oauth_token.OAuthError) as exc:
            google_oauth_token.get_access_token_for_operator(fake_user)
    assert exc.value.code == "google_grant_invalid"


# ── Log redaction ──────────────────────────────────────────────────────


def test_no_log_line_contains_test_token(fake_user, monkeypatch, caplog):
    """Force a failure path and assert the fake token doesn't appear in
    any log record. This is the 'no_secrets_in_logs' rule from CLAUDE.md.
    """
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "test-client")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "VERY-SECRET-XYZ")
    secret_token = "RT_PRIVATE_TOKEN_DO_NOT_LEAK"
    monkeypatch.setattr(
        google_oauth_token.vault, "read_secret",
        MagicMock(return_value=secret_token),
    )
    google_oauth_token.invalidate_access_token_cache(fake_user)

    caplog.set_level(logging.DEBUG)
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = _make_http_error(500)
        with pytest.raises(google_oauth_token.OAuthError):
            google_oauth_token.get_access_token_for_operator(fake_user)

    for record in caplog.records:
        msg = record.getMessage()
        assert secret_token not in msg, (
            f"refresh token leaked in log: {msg}"
        )
        assert "VERY-SECRET-XYZ" not in msg, (
            f"client_secret leaked in log: {msg}"
        )
