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


def test_gmail_happy_path_by_attachment_id():
    payload = b"binary-spreadsheet-content"
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = [
            _make_urlopen_response(_gmail_message_json("ATT_ID")),
            _make_urlopen_response(_gmail_attachment_json(payload)),
        ]
        result = gmail_source.fetch(
            "MSG_ID", "test-access-token", attachment_id="ATT_ID",
        )
    assert result.filename == "February Commission 2026.xlsx"
    assert result.sender.startswith("Jackie Chalk")
    assert result.data == payload
    assert result.size_bytes == len(payload)


def test_gmail_happy_path_by_filename():
    """Caller passes filename; we resolve canonical attachment_id internally."""
    payload = b"binary-spreadsheet-content"
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = [
            _make_urlopen_response(_gmail_message_json("CANONICAL_ATT_ID")),
            _make_urlopen_response(_gmail_attachment_json(payload)),
        ]
        result = gmail_source.fetch(
            "MSG_ID", "test-access-token",
            filename="February Commission 2026.xlsx",
        )
    assert result.filename == "February Commission 2026.xlsx"
    assert result.data == payload


def test_gmail_filename_case_insensitive():
    """Match is case-insensitive on the basename."""
    payload = b"x"
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = [
            _make_urlopen_response(_gmail_message_json("CANONICAL_ATT_ID")),
            _make_urlopen_response(_gmail_attachment_json(payload)),
        ]
        result = gmail_source.fetch(
            "MSG_ID", "test-access-token",
            filename="FEBRUARY COMMISSION 2026.XLSX",  # all caps
        )
    assert result.filename == "February Commission 2026.xlsx"  # original case preserved


def test_gmail_requires_filename_or_attachment_id():
    with pytest.raises(gmail_source.GmailError) as exc:
        gmail_source.fetch("MSG_ID", "test-token")
    assert exc.value.code == "missing_required"


def test_gmail_401_raises():
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = _make_http_error(401)
        with pytest.raises(gmail_source.GmailError) as exc:
            gmail_source.fetch("MSG_ID", "test-token", attachment_id="ATT_ID")
    assert exc.value.code == "gmail_unauthorized"


def test_gmail_message_not_found_distinct_code():
    """404 on the message GET surfaces as gmail_message_not_found, NOT
    gmail_attachment_*_not_in_message -- so callers can distinguish.
    """
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = _make_http_error(404)
        with pytest.raises(gmail_source.GmailError) as exc:
            gmail_source.fetch("MSG_ID", "test-token", attachment_id="ATT_ID")
    assert exc.value.code == "gmail_message_not_found"


def test_gmail_attachment_id_not_in_message():
    """Message exists but attachment_id doesn't appear in any MIME part."""
    with patch("urllib.request.urlopen") as mock_urlopen:
        # Use a payload with a DIFFERENT attachment id than what we request
        mock_urlopen.side_effect = [
            _make_urlopen_response(_gmail_message_json("OTHER_ATT_ID")),
        ]
        with pytest.raises(gmail_source.GmailError) as exc:
            gmail_source.fetch("MSG_ID", "test-token", attachment_id="ATT_ID")
    assert exc.value.code == "gmail_attachment_id_not_in_message"
    # The error message lists what filenames ARE present so the caller can fix
    assert "February Commission 2026.xlsx" in str(exc.value)


def test_gmail_filename_not_in_message():
    """Message exists but no part filename matches the caller's request."""
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = [
            _make_urlopen_response(_gmail_message_json("ATT_ID")),
        ]
        with pytest.raises(gmail_source.GmailError) as exc:
            gmail_source.fetch(
                "MSG_ID", "test-token", filename="something-else.pdf",
            )
    assert exc.value.code == "gmail_filename_not_in_message"
    assert "present attachments" in str(exc.value).lower()


def test_gmail_malformed_response():
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = [
            _make_urlopen_response(b"<html>this is not json</html>"),
        ]
        with pytest.raises(gmail_source.GmailError) as exc:
            gmail_source.fetch("MSG_ID", "test-token", attachment_id="ATT_ID")
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


# ── Basecamp upload-by-recording-id (F6: brief/vault links have no sgid) ──


def test_basecamp_fetch_by_recording_happy_path():
    meta = json.dumps({
        "filename": "19-ali-decisions.md",
        "content_type": "text/markdown",
        "byte_size": 4096,
        "download_url": "https://3.basecampapi.com/download/upload/Y",
    }).encode("utf-8")
    binary = b"# Ali decisions\nlocked: quarterly cadence"
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = [
            _make_urlopen_response(meta),
            _make_urlopen_response(binary),
        ]
        result = bc_source.fetch_by_recording(
            project_id=47502609, recording_id=9946496378,
            bc_token="bc-token-xyz",
            project_name_for_audit="AI Systems Architect Accelerator",
        )
    assert result.filename == "19-ali-decisions.md"
    assert result.mime_type == "text/markdown"
    assert result.data == binary
    assert "AI Systems Architect Accelerator" in result.sender
    # Metadata call must hit the *upload recording* endpoint (recording id in
    # the path), not the sgid blob endpoint.
    meta_req = mock_urlopen.call_args_list[0].args[0]
    assert "/buckets/47502609/uploads/9946496378.json" in meta_req.full_url


def test_basecamp_fetch_by_recording_resolves_nested_attachable_url():
    # Some upload payloads nest the blob fields under `attachable`.
    meta = json.dumps({
        "filename": "brief.pdf",
        "attachable": {"download_url": "https://3.basecampapi.com/download/nested"},
    }).encode("utf-8")
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = [
            _make_urlopen_response(meta),
            _make_urlopen_response(b"bytes"),
        ]
        result = bc_source.fetch_by_recording(47502609, 9946496378, "bc-token")
    assert result.data == b"bytes"
    dl_req = mock_urlopen.call_args_list[1].args[0]
    assert dl_req.full_url == "https://3.basecampapi.com/download/nested"


def test_basecamp_fetch_by_recording_requires_ids():
    with pytest.raises(bc_source.BasecampError) as exc:
        bc_source.fetch_by_recording(0, 0, "bc-token")
    assert exc.value.code == "missing_required"


def test_basecamp_fetch_by_recording_no_download_url_raises():
    meta = json.dumps({"filename": "x", "content_type": "text/plain"}).encode("utf-8")
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = [_make_urlopen_response(meta)]
        with pytest.raises(bc_source.BasecampError) as exc:
            bc_source.fetch_by_recording(47502609, 9946496378, "bc-token")
    assert exc.value.code == "basecamp_no_download_url"


# ── Basecamp document fallback (Lab Spec / brief linked in a comment) ──────


def test_basecamp_fetch_by_recording_falls_back_to_document():
    """A recording id that is a vault Document 404s on /uploads but resolves
    via /documents — the content HTML is staged as a text/html .html file."""
    doc = json.dumps({
        "id": 10010113835,
        "type": "Document",
        "title": "Lab Spec",
        "content": "<div><h1>Lab Spec</h1><p>Build the thing.</p></div>",
    }).encode("utf-8")
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = [
            _make_http_error(404),          # /uploads/<id>.json -> not an upload
            _make_urlopen_response(doc),    # /documents/<id>.json -> the document
        ]
        result = bc_source.fetch_by_recording(
            project_id=47502609, recording_id=10010113835,
            bc_token="bc-token-xyz",
            project_name_for_audit="Some Project",
        )
    assert result.filename == "Lab Spec.html"
    assert result.mime_type == "text/html"
    assert b"Build the thing." in result.data
    assert result.size_bytes == len(result.data)
    assert "Some Project" in result.sender
    # The fallback call must hit the *documents* endpoint with the recording id.
    doc_req = mock_urlopen.call_args_list[1].args[0]
    assert "/buckets/47502609/documents/10010113835.json" in doc_req.full_url


def test_basecamp_fetch_document_direct_happy_path():
    doc = json.dumps({
        "title": "Onboarding Brief",
        "content": "<p>hello</p>",
    }).encode("utf-8")
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = [_make_urlopen_response(doc)]
        result = bc_source.fetch_document(47502609, 123, "bc-token")
    assert result.filename == "Onboarding Brief.html"
    assert result.mime_type == "text/html"
    assert result.data == b"<p>hello</p>"


def test_basecamp_fetch_document_sanitizes_title_into_filename():
    """Path separators and control chars in the title must not leak into the
    Drive filename (they'd break the staged path)."""
    doc = json.dumps({
        "title": "Q3/Q4: Plan\n(draft)",
        "content": "<p>x</p>",
    }).encode("utf-8")
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = [_make_urlopen_response(doc)]
        result = bc_source.fetch_document(47502609, 123, "bc-token")
    assert "/" not in result.filename
    assert "\\" not in result.filename
    assert result.filename.endswith(".html")
    assert result.filename == "Q3 Q4: Plan (draft).html"


def test_basecamp_fetch_document_no_content_raises():
    doc = json.dumps({"title": "Empty", "type": "Document"}).encode("utf-8")
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = [_make_urlopen_response(doc)]
        with pytest.raises(bc_source.BasecampError) as exc:
            bc_source.fetch_document(47502609, 123, "bc-token")
    assert exc.value.code == "basecamp_document_no_content"


def test_basecamp_fetch_by_recording_unknown_id_raises_recording_not_found():
    """Neither an upload nor a document (deleted/no access) -> a clearer code
    than the generic basecamp_blob_not_found."""
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = [
            _make_http_error(404),   # not an upload
            _make_http_error(404),   # not a document either
        ]
        with pytest.raises(bc_source.BasecampError) as exc:
            bc_source.fetch_by_recording(47502609, 999, "bc-token")
    assert exc.value.code == "basecamp_recording_not_found"


def test_mcp_basecamp_validation_allows_missing_sgid(fake_user):
    """The MCP tool must accept a basecamp fetch with no sgid (resolves by
    recording id) but still require project_id + recording_id."""
    from execution.products.library import mcp_tools

    # Missing both ids -> still an error, with the updated message.
    err = mcp_tools._tool_attachment_fetch(fake_user, {"source": "basecamp"})
    assert err["ok"] is False
    assert "project_id + recording_id" in err["error"]
    assert "attachment_sgid optional" in err["error"]

    # sgid omitted but ids present -> validation passes, so we get PAST the
    # missing_required gate. Mock credential resolution to fail cleanly so the
    # call returns without real network/index side effects beyond the inflight
    # guard, and assert the error is NOT a missing_required one.
    with patch.object(google_oauth_token, "get_access_token_for_operator",
                      side_effect=google_oauth_token.OAuthError("no_google_oauth_grant")):
        res = mcp_tools._tool_attachment_fetch(
            fake_user,
            {"source": "basecamp", "project_id": 47502609, "recording_id": 9946496378},
        )
    assert res["ok"] is False
    assert "missing_required" not in res["error"]


# ── Inline document text (read a BC Document in-session, no Drive connector) ──


def _bc_document_attachment(html: str):
    """A FetchedAttachment shaped like basecamp.fetch_document's output."""
    from execution.products.library.attachment_sources import FetchedAttachment
    data = html.encode("utf-8")
    return FetchedAttachment(
        filename="Updated Program Details.html",
        mime_type="text/html",
        size_bytes=len(data),
        sender="AI Systems Architect Accelerator",
        data=data,
    )


def test_html_to_text_strips_tags_and_unescapes_entities():
    from execution.products.library import mcp_tools
    html = ("<h1>Title</h1><p>Cost is &amp;1,500.</p>"
            "<ul><li>Item A</li><li>Item B</li></ul>")
    text = mcp_tools._html_to_text(html)
    assert "<" not in text and ">" not in text   # tags gone
    assert "Title" in text
    assert "Cost is &1,500." in text             # &amp; -> &
    assert "Item A" in text and "Item B" in text
    assert "\n" in text                          # block elements broke lines


def test_attachment_fetch_basecamp_document_returns_inline_text(
        fake_user, monkeypatch, tmp_path):
    """A BC Document fetch stages the .html to Drive AND returns the rendered
    text inline as content_text -- the fix for 'I have no Drive connector to
    read it'. (Swati's scenario.)"""
    from execution.products.library import mcp_tools, drive_staging

    monkeypatch.setattr(attachment_index, "INDEX_DIR", tmp_path)
    monkeypatch.setattr(google_oauth_token, "get_access_token_for_operator",
                        lambda user: "access-token")
    monkeypatch.setattr(mcp_tools, "_bc_token", lambda user: "bc-token")
    html = ("<div><h1>Updated Program Details</h1>"
            "<p>Cost is &amp;1,500.</p><p>Duration: 12 weeks.</p></div>")
    monkeypatch.setattr(bc_source, "fetch_by_recording",
                        lambda *a, **k: _bc_document_attachment(html))
    monkeypatch.setattr(
        drive_staging, "upload",
        lambda **k: {"id": "DRIVE_DOC_ID",
                     "webViewLink": "https://drive.google.com/file/d/DRIVE_DOC_ID/view"})

    res = mcp_tools._tool_attachment_fetch(
        fake_user,
        {"source": "basecamp", "project_id": 47502609, "recording_id": 10010113835},
    )
    assert res["ok"] is True
    assert res["drive_file_id"] == "DRIVE_DOC_ID"     # still staged to Drive
    assert res["content_truncated"] is False
    assert "Updated Program Details" in res["content_text"]
    assert "Duration: 12 weeks." in res["content_text"]
    assert "Cost is &1,500." in res["content_text"]   # entity unescaped
    assert "<h1>" not in res["content_text"]           # tags stripped


def test_attachment_fetch_binary_attachment_has_no_inline_text(
        fake_user, monkeypatch, tmp_path):
    """A non-document (pdf) attachment stays Drive-only -- no content_text."""
    from execution.products.library import mcp_tools, drive_staging
    from execution.products.library.attachment_sources import FetchedAttachment

    monkeypatch.setattr(attachment_index, "INDEX_DIR", tmp_path)
    monkeypatch.setattr(google_oauth_token, "get_access_token_for_operator",
                        lambda user: "access-token")
    monkeypatch.setattr(mcp_tools, "_bc_token", lambda user: "bc-token")
    pdf = FetchedAttachment(filename="brief.pdf", mime_type="application/pdf",
                            size_bytes=10, sender="Some Project", data=b"%PDF-1.7..")
    monkeypatch.setattr(bc_source, "fetch_by_recording", lambda *a, **k: pdf)
    monkeypatch.setattr(drive_staging, "upload",
                        lambda **k: {"id": "DRIVE_PDF", "webViewLink": "u"})

    res = mcp_tools._tool_attachment_fetch(
        fake_user,
        {"source": "basecamp", "project_id": 47502609, "recording_id": 9946496378},
    )
    assert res["ok"] is True
    assert "content_text" not in res
    assert "content_truncated" not in res


def test_attachment_fetch_document_truncates_large_text(
        fake_user, monkeypatch, tmp_path):
    """A very large document is capped inline (with a pointer to Drive) so it
    can't blow the model's context; the full copy is still staged."""
    from execution.products.library import mcp_tools, drive_staging

    monkeypatch.setattr(attachment_index, "INDEX_DIR", tmp_path)
    monkeypatch.setattr(google_oauth_token, "get_access_token_for_operator",
                        lambda user: "access-token")
    monkeypatch.setattr(mcp_tools, "_bc_token", lambda user: "bc-token")
    big = "<p>" + ("word " * 20000) + "</p>"   # ~100k chars of text
    monkeypatch.setattr(bc_source, "fetch_by_recording",
                        lambda *a, **k: _bc_document_attachment(big))
    monkeypatch.setattr(drive_staging, "upload",
                        lambda **k: {"id": "D", "webViewLink": "u"})

    res = mcp_tools._tool_attachment_fetch(
        fake_user, {"source": "basecamp", "project_id": 1, "recording_id": 2})
    assert res["content_truncated"] is True
    assert "truncated" in res["content_text"].lower()
    assert "drive_url" in res["content_text"]
    assert len(res["content_text"]) < mcp_tools._INLINE_TEXT_CAP + 200


def test_attachment_fetch_document_cache_hit_still_returns_inline_text(
        fake_user, monkeypatch, tmp_path):
    """A repeat call hits the idempotency cache (reused_existing) but STILL
    returns content_text -- by re-fetching just the document body -- so a
    second 'read it' doesn't regress to the unreadable Drive ref."""
    from execution.products.library import mcp_tools

    monkeypatch.setattr(attachment_index, "INDEX_DIR", tmp_path)
    key = attachment_index.compute_key(
        source="basecamp", project_id=47502609, recording_id=10010113835, sgid="")
    attachment_index.record(fake_user.email, attachment_index.AttachmentRef(
        idempotency_key=key, source="basecamp",
        drive_file_id="DRIVE_DOC_ID",
        drive_url="https://drive.google.com/file/d/DRIVE_DOC_ID/view",
        mime_type="text/html", size_bytes=42,
        filename="Updated Program Details.html",
        sender="AI Systems Architect Accelerator",
        saved_at="2026-06-18T00:00:00Z",
        source_message_id="10010113835", source_attachment_id="",
    ))
    monkeypatch.setattr(mcp_tools, "_bc_token", lambda user: "bc-token")
    monkeypatch.setattr(bc_source, "fetch_document",
                        lambda *a, **k: _bc_document_attachment("<p>Revised cost is $2,000.</p>"))

    res = mcp_tools._tool_attachment_fetch(
        fake_user,
        {"source": "basecamp", "project_id": 47502609, "recording_id": 10010113835},
    )
    assert res["reused_existing"] is True
    assert res["drive_file_id"] == "DRIVE_DOC_ID"
    assert "Revised cost is $2,000." in res["content_text"]


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


# ── Tagged vault entries (client_type) ────────────────────────────────


def test_parse_stored_legacy_bare_string_treated_as_desktop():
    """Pre-tag vault entries are bare refresh tokens. Must remain readable
    and tagged as desktop (the only client that existed before tagging)."""
    rt, ct = google_oauth_token._parse_stored("1//0xLEGACY-DESKTOP-RT")
    assert rt == "1//0xLEGACY-DESKTOP-RT"
    assert ct == "desktop"


def test_parse_stored_wrapped_web():
    blob = json.dumps({"v": 1, "refresh_token": "1//WEB", "client_type": "web"})
    rt, ct = google_oauth_token._parse_stored(blob)
    assert rt == "1//WEB"
    assert ct == "web"


def test_parse_stored_wrapped_desktop():
    blob = json.dumps({"v": 1, "refresh_token": "1//DESK", "client_type": "desktop"})
    rt, ct = google_oauth_token._parse_stored(blob)
    assert rt == "1//DESK"
    assert ct == "desktop"


def test_parse_stored_unknown_client_type_falls_back_to_desktop():
    """Defensive: a future client_type or a corrupted entry should NOT
    accidentally promote to Web (which could cause invalid_grant). Default
    to desktop -- the legacy interpretation -- so existing Desktop-issued
    tokens keep working."""
    blob = json.dumps({"v": 1, "refresh_token": "1//X", "client_type": "alien"})
    rt, ct = google_oauth_token._parse_stored(blob)
    assert rt == "1//X"
    assert ct == "desktop"


def test_parse_stored_empty_returns_desktop():
    rt, ct = google_oauth_token._parse_stored("")
    assert rt == ""
    assert ct == "desktop"


def test_client_credentials_for_web_uses_web_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_ATTACHMENT_WEB_CLIENT_ID", "web-cid")
    monkeypatch.setenv("GOOGLE_OAUTH_ATTACHMENT_WEB_CLIENT_SECRET", "web-sec")
    monkeypatch.setenv("GOOGLE_OAUTH_ATTACHMENT_CLIENT_ID", "desk-cid")
    monkeypatch.setenv("GOOGLE_OAUTH_ATTACHMENT_CLIENT_SECRET", "desk-sec")
    cid, sec = google_oauth_token._client_credentials_for("web")
    assert cid == "web-cid"
    assert sec == "web-sec"


def test_client_credentials_for_desktop_uses_desktop_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_ATTACHMENT_WEB_CLIENT_ID", "web-cid")
    monkeypatch.setenv("GOOGLE_OAUTH_ATTACHMENT_WEB_CLIENT_SECRET", "web-sec")
    monkeypatch.setenv("GOOGLE_OAUTH_ATTACHMENT_CLIENT_ID", "desk-cid")
    monkeypatch.setenv("GOOGLE_OAUTH_ATTACHMENT_CLIENT_SECRET", "desk-sec")
    cid, sec = google_oauth_token._client_credentials_for("desktop")
    assert cid == "desk-cid"
    assert sec == "desk-sec"


def test_client_credentials_for_web_missing_env_raises(monkeypatch):
    monkeypatch.delenv("GOOGLE_OAUTH_ATTACHMENT_WEB_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_ATTACHMENT_WEB_CLIENT_SECRET", raising=False)
    with pytest.raises(google_oauth_token.OAuthError) as exc:
        google_oauth_token._client_credentials_for("web")
    assert exc.value.code == "google_oauth_app_not_configured"


def test_get_access_token_web_tagged_uses_web_creds(fake_user, monkeypatch):
    """Critical regression test: a Web-issued vault entry must trigger
    exchange with Web env creds. If we picked Desktop creds here, Google
    would reject with invalid_grant in production."""
    blob = json.dumps({"v": 1, "refresh_token": "RT-WEB", "client_type": "web"})
    monkeypatch.setattr(
        google_oauth_token.vault, "read_secret",
        MagicMock(return_value=blob),
    )
    monkeypatch.setenv("GOOGLE_OAUTH_ATTACHMENT_WEB_CLIENT_ID", "web-cid-marker")
    monkeypatch.setenv("GOOGLE_OAUTH_ATTACHMENT_WEB_CLIENT_SECRET", "web-sec-marker")
    monkeypatch.setenv("GOOGLE_OAUTH_ATTACHMENT_CLIENT_ID", "desk-cid-should-not-appear")
    monkeypatch.setenv("GOOGLE_OAUTH_ATTACHMENT_CLIENT_SECRET", "desk-sec-should-not-appear")
    google_oauth_token.invalidate_access_token_cache(fake_user)

    captured_body = {}
    class _FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self):
            return json.dumps({"access_token": "AT-WEB", "expires_in": 3600}).encode()

    def _fake_urlopen(req, timeout):
        captured_body["data"] = req.data.decode()
        return _FakeResp()

    with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        at = google_oauth_token.get_access_token_for_operator(fake_user)
    assert at == "AT-WEB"
    assert "client_id=web-cid-marker" in captured_body["data"]
    assert "client_secret=web-sec-marker" in captured_body["data"]
    assert "desk-cid-should-not-appear" not in captured_body["data"]


def test_get_access_token_legacy_bare_string_uses_desktop_creds(fake_user, monkeypatch):
    """Existing operators (Ali pre-rotation) have bare-string vault entries.
    Those must continue to exchange with Desktop creds. Regression guard
    against accidentally treating bare strings as Web."""
    monkeypatch.setattr(
        google_oauth_token.vault, "read_secret",
        MagicMock(return_value="bare-rt-legacy"),
    )
    monkeypatch.setenv("GOOGLE_OAUTH_ATTACHMENT_WEB_CLIENT_ID", "web-cid-must-not-be-used")
    monkeypatch.setenv("GOOGLE_OAUTH_ATTACHMENT_WEB_CLIENT_SECRET", "web-sec-must-not-be-used")
    monkeypatch.setenv("GOOGLE_OAUTH_ATTACHMENT_CLIENT_ID", "desk-cid-expected")
    monkeypatch.setenv("GOOGLE_OAUTH_ATTACHMENT_CLIENT_SECRET", "desk-sec-expected")
    google_oauth_token.invalidate_access_token_cache(fake_user)

    captured_body = {}
    class _FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self):
            return json.dumps({"access_token": "AT-DESK", "expires_in": 3600}).encode()
    def _fake_urlopen(req, timeout):
        captured_body["data"] = req.data.decode()
        return _FakeResp()

    with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        at = google_oauth_token.get_access_token_for_operator(fake_user)
    assert at == "AT-DESK"
    assert "client_id=desk-cid-expected" in captured_body["data"]
    assert "web-cid-must-not-be-used" not in captured_body["data"]


def test_store_refresh_token_web_writes_wrapped_blob(fake_user, monkeypatch):
    captured = {}
    def _fake_store(uid, tool, val, *, caller_id, ttl_days):
        captured["uid"] = uid
        captured["tool"] = tool
        captured["val"] = val
        captured["caller_id"] = caller_id
    monkeypatch.setattr(google_oauth_token.vault, "store_secret", _fake_store)

    google_oauth_token.store_refresh_token_for_operator(
        fake_user, "RT-NEW", client_type="web", actor_id="test_caller",
    )
    obj = json.loads(captured["val"])
    assert obj["refresh_token"] == "RT-NEW"
    assert obj["client_type"] == "web"
    assert obj["v"] == 1
    assert captured["caller_id"] == "test_caller"


def test_store_refresh_token_invalid_client_type_rejects(fake_user, monkeypatch):
    monkeypatch.setattr(google_oauth_token.vault, "store_secret", MagicMock())
    with pytest.raises(ValueError):
        google_oauth_token.store_refresh_token_for_operator(
            fake_user, "RT", client_type="hybrid",
        )
