"""Tests for the native BC file-upload MCP tools:
`colaberry_attach_file_to_ticket` and `colaberry_upload_file_to_project`.

These attach a REAL, downloadable file to Basecamp — not a rich-text Document.
Because the Colaberry MCP server is REMOTE (advisor.colaberry.ai) and can't read
the operator's disk, the file's bytes arrive in the call as base64; the server
decodes them and runs BC's two-step attachment flow (POST /attachments.json →
attachable_sgid, then reference the sgid in a ticket comment or a vault Upload).

All BC HTTP is mocked — no network, no token fetch, no operator OAuth.
"""
from __future__ import annotations

import base64
import io
import json
import urllib.error

import pytest

from execution.products.library import mcp_tools

ACCOUNT_ID = int(mcp_tools._bc_account())
REAL_BUCKET = 47502609


class _FakeUser:
    def __init__(self):
        self.display_name = "Ram K"
        self.email = "ram@colaberry.com"
        self.user_id = "u-ram"
        self.personal_bc_project_id = 12345
        self.personal_bc_todolist_id = 678


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


class _FakeResp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


# ── _decode_upload_content ───────────────────────────────────────────
def test_decode_valid_returns_bytes_and_mime():
    data, ctype = mcp_tools._decode_upload_content(_b64(b"%PDF-1.7 x"), "spec.pdf")
    assert data == b"%PDF-1.7 x"
    assert ctype == "application/pdf"


def test_decode_unknown_extension_falls_back_to_octet_stream():
    _data, ctype = mcp_tools._decode_upload_content(_b64(b"x"), "mystery.zzz")
    assert ctype == "application/octet-stream"


def test_decode_missing_content_errors():
    r = mcp_tools._decode_upload_content("", "x.pdf")
    assert r["ok"] is False and "content_base64" in r["error"]


def test_decode_empty_after_decode_errors():
    # A whitespace-only payload is a non-empty string (passes the "required"
    # guard) but base64-decodes to zero bytes → the empty-file guard fires.
    r = mcp_tools._decode_upload_content("   ", "x.pdf")
    assert r["ok"] is False and "empty" in r["error"]


def test_decode_bad_base64_errors():
    r = mcp_tools._decode_upload_content("abcde", "x.pdf")  # length 5 → padding error
    assert r["ok"] is False and "base64" in r["error"]


def test_decode_too_large_errors(monkeypatch):
    monkeypatch.setattr(mcp_tools, "_MAX_UPLOAD_BYTES", 4)
    r = mcp_tools._decode_upload_content(_b64(b"12345"), "x.bin")
    assert r["ok"] is False and "too large" in r["error"]


# ── _bc_create_attachment (raw binary POST + self-heal) ──────────────
def test_create_attachment_uploads_bytes_and_returns_sgid(monkeypatch):
    monkeypatch.setattr(mcp_tools, "_bc_token", lambda user=None: "TOK")
    seen = {}

    def fake_urlopen(req, timeout=0):
        seen["url"] = req.full_url
        seen["method"] = req.get_method()
        seen["data"] = req.data
        seen["ctype"] = req.get_header("Content-type")
        seen["auth"] = req.get_header("Authorization")
        return _FakeResp(json.dumps({"attachable_sgid": "SG", "id": 9}).encode())

    monkeypatch.setattr(mcp_tools.urllib.request, "urlopen", fake_urlopen)

    sgid = mcp_tools._bc_create_attachment("a b.pdf", b"RAW", "application/pdf")

    assert sgid == "SG"
    assert "/attachments.json?name=a%20b.pdf" in seen["url"]
    assert seen["method"] == "POST"
    assert seen["data"] == b"RAW"                    # raw bytes, not JSON
    assert seen["ctype"] == "application/pdf"        # file's own type
    assert seen["auth"] == "Bearer TOK"


def test_create_attachment_self_heals_once_on_401(monkeypatch):
    monkeypatch.setattr(mcp_tools, "_bc_token", lambda user=None: "TOK")
    heals = {"n": 0}
    monkeypatch.setattr(mcp_tools, "_invalidate_bc_token_caches",
                        lambda user=None: heals.__setitem__("n", heals["n"] + 1))
    calls = {"n": 0}

    def fake_urlopen(req, timeout=0):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", None, io.BytesIO(b"x"))
        return _FakeResp(json.dumps({"attachable_sgid": "SG2"}).encode())

    monkeypatch.setattr(mcp_tools.urllib.request, "urlopen", fake_urlopen)

    sgid = mcp_tools._bc_create_attachment("f.pdf", b"X", "application/pdf")

    assert sgid == "SG2"
    assert calls["n"] == 2       # retried exactly once
    assert heals["n"] == 1       # dropped the stale token


def test_create_attachment_raises_without_sgid(monkeypatch):
    monkeypatch.setattr(mcp_tools, "_bc_token", lambda user=None: "TOK")
    monkeypatch.setattr(mcp_tools.urllib.request, "urlopen",
                        lambda req, timeout=0: _FakeResp(json.dumps({"id": 1}).encode()))
    with pytest.raises(RuntimeError):
        mcp_tools._bc_create_attachment("f.pdf", b"X", "application/pdf")


# ── colaberry_attach_file_to_ticket ──────────────────────────────────
def _stub_attach(monkeypatch, sgid="SGID"):
    cap: dict = {}

    def fake_create(name, data, content_type, user=None):
        cap["create"] = (name, data, content_type)
        return sgid

    def fake_req(method, url, payload=None, user=None):
        cap["req"] = (method, url, payload)
        return {"id": 321}

    monkeypatch.setattr(mcp_tools, "_bc_create_attachment", fake_create)
    monkeypatch.setattr(mcp_tools, "_bc_request", fake_req)
    return cap


def test_attach_to_ticket_happy_path(monkeypatch):
    cap = _stub_attach(monkeypatch)
    out = mcp_tools._tool_attach_file_to_ticket(_FakeUser(), {
        "filename": "design-spec.pdf",
        "content_base64": _b64(b"%PDF data"),
        "ticket_id": 555,
        "bc_project_id": REAL_BUCKET,
    })
    assert out["ok"] is True
    assert out["filename"] == "design-spec.pdf"
    assert out["bytes"] == len(b"%PDF data")
    assert out["comment_id"] == 321
    assert out["attachment_kind"] == "native_file"

    name, data, ctype = cap["create"]
    assert (name, data, ctype) == ("design-spec.pdf", b"%PDF data", "application/pdf")

    method, url, payload = cap["req"]
    assert method == "POST"
    assert url.endswith(f"/buckets/{REAL_BUCKET}/recordings/555/comments.json")
    assert '<bc-attachment sgid="SGID"' in payload["content"]
    assert "design-spec.pdf" in payload["content"]
    assert "via Ram K's Claude Code" in payload["content"]     # attribution applied


def test_attach_uses_custom_comment_html(monkeypatch):
    cap = _stub_attach(monkeypatch)
    mcp_tools._tool_attach_file_to_ticket(_FakeUser(), {
        "filename": "x.pdf", "content_base64": _b64(b"x"), "ticket_id": 1,
        "bc_project_id": REAL_BUCKET, "comment_html": "<strong>Here is the spec</strong>",
    })
    _, _, payload = cap["req"]
    assert "<strong>Here is the spec</strong>" in payload["content"]
    assert "Attached file:" not in payload["content"]           # default caption suppressed
    assert '<bc-attachment sgid="SGID"' in payload["content"]


def test_attach_requires_filename(monkeypatch):
    _stub_attach(monkeypatch)
    out = mcp_tools._tool_attach_file_to_ticket(_FakeUser(),
                                                {"content_base64": _b64(b"x"), "ticket_id": 1})
    assert out["ok"] is False and "filename" in out["error"]


def test_attach_requires_ticket_id_and_points_to_vault_tool(monkeypatch):
    _stub_attach(monkeypatch)
    out = mcp_tools._tool_attach_file_to_ticket(_FakeUser(),
                                                {"filename": "x.pdf", "content_base64": _b64(b"x")})
    assert out["ok"] is False
    assert "ticket_id" in out["error"]
    assert "colaberry_upload_file_to_project" in out["error"]


def test_attach_defaults_to_operators_personal_project(monkeypatch):
    cap = _stub_attach(monkeypatch)
    out = mcp_tools._tool_attach_file_to_ticket(_FakeUser(), {
        "filename": "x.pdf", "content_base64": _b64(b"x"), "ticket_id": 9,
    })
    assert out["ok"] is True
    assert out["bc_project_id"] == 12345                        # _FakeUser.personal_bc_project_id
    _, url, _p = cap["req"]
    assert "/buckets/12345/recordings/9/comments.json" in url


def test_attach_rejects_account_id_before_any_upload(monkeypatch):
    monkeypatch.setattr(mcp_tools, "_bc_create_attachment",
                        lambda *a, **k: pytest.fail("must not upload when project id is the account id"))
    monkeypatch.setattr(mcp_tools, "_bc_request",
                        lambda *a, **k: pytest.fail("must not post"))
    out = mcp_tools._tool_attach_file_to_ticket(_FakeUser(), {
        "filename": "x.pdf", "content_base64": _b64(b"x"),
        "ticket_id": 1, "bc_project_id": ACCOUNT_ID,
    })
    assert out["ok"] is False and out["error"] == "bc_project_id_is_account_id"


def test_attach_bad_base64_errors_before_upload(monkeypatch):
    monkeypatch.setattr(mcp_tools, "_bc_create_attachment",
                        lambda *a, **k: pytest.fail("must not upload invalid content"))
    monkeypatch.setattr(mcp_tools, "_bc_request", lambda *a, **k: {"id": 1})
    out = mcp_tools._tool_attach_file_to_ticket(_FakeUser(), {
        "filename": "x.pdf", "content_base64": "abcde",
        "ticket_id": 1, "bc_project_id": REAL_BUCKET,
    })
    assert out["ok"] is False and "base64" in out["error"]


# ── colaberry_upload_file_to_project ─────────────────────────────────
_VAULT_GET_URL = f"https://3.basecampapi.com/{ACCOUNT_ID}/buckets/{REAL_BUCKET}/vaults/22.json"
_UPLOADS_URL = f"https://3.basecampapi.com/{ACCOUNT_ID}/buckets/{REAL_BUCKET}/vaults/22/uploads.json"


def _stub_upload(monkeypatch, sgid="SGID", upload_resp=None):
    cap: dict = {"reqs": []}

    def fake_create(name, data, content_type, user=None):
        cap["create"] = (name, data, content_type)
        return sgid

    def fake_req(method, url, payload=None, user=None):
        cap["reqs"].append((method, url, payload))
        if url.endswith(f"/projects/{REAL_BUCKET}.json"):
            return {"dock": [{"name": "message_board", "url": "https://x/mb.json"},
                             {"name": "vault", "url": _VAULT_GET_URL}]}
        if url == _VAULT_GET_URL:
            return {"id": 22, "uploads_url": _UPLOADS_URL}
        if url == _UPLOADS_URL:
            return upload_resp if upload_resp is not None else {
                "id": 888,
                "app_url": f"https://3.basecamp.com/{ACCOUNT_ID}/buckets/{REAL_BUCKET}/vaults/22/uploads/888",
            }
        raise AssertionError(f"unexpected BC url: {url}")

    monkeypatch.setattr(mcp_tools, "_bc_create_attachment", fake_create)
    monkeypatch.setattr(mcp_tools, "_bc_request", fake_req)
    return cap


def test_upload_to_project_happy_path(monkeypatch):
    cap = _stub_upload(monkeypatch)
    out = mcp_tools._tool_upload_file_to_project(_FakeUser(), {
        "filename": "report.pdf", "content_base64": _b64(b"PDFDATA"), "bc_project_id": REAL_BUCKET,
    })
    assert out["ok"] is True
    assert out["upload_id"] == 888
    assert out["bytes"] == len(b"PDFDATA")
    assert out["attachment_kind"] == "native_file"

    name, data, ctype = cap["create"]
    assert (name, ctype) == ("report.pdf", "application/pdf") and data == b"PDFDATA"

    method, url, payload = cap["reqs"][-1]                      # final call is the Upload POST
    assert method == "POST" and url == _UPLOADS_URL
    assert payload["attachable_sgid"] == "SGID"
    assert payload["base_name"] == "report"                    # extension dropped
    assert "description" not in payload


def test_upload_to_project_includes_description(monkeypatch):
    cap = _stub_upload(monkeypatch)
    mcp_tools._tool_upload_file_to_project(_FakeUser(), {
        "filename": "r.pdf", "content_base64": _b64(b"x"), "bc_project_id": REAL_BUCKET,
        "description_html": "<div>notes</div>",
    })
    _, _, payload = cap["reqs"][-1]
    assert payload["description"] == "<div>notes</div>"


def test_upload_to_project_reports_missing_vault(monkeypatch):
    monkeypatch.setattr(mcp_tools, "_bc_create_attachment", lambda *a, **k: "SGID")
    monkeypatch.setattr(mcp_tools, "_bc_request",
                        lambda method, url, payload=None, user=None: {"dock": [{"name": "message_board", "url": "x"}]})
    out = mcp_tools._tool_upload_file_to_project(_FakeUser(), {
        "filename": "r.pdf", "content_base64": _b64(b"x"), "bc_project_id": REAL_BUCKET,
    })
    assert out["ok"] is False and out["error"] == "project_has_no_vault"


def test_upload_requires_filename():
    out = mcp_tools._tool_upload_file_to_project(_FakeUser(), {"content_base64": _b64(b"x")})
    assert out["ok"] is False and "filename" in out["error"]
