"""Tests for colaberry_read_ticket -- the READ counterpart to the BC
write tools. Lets a Claude session pull a ticket's body + comment thread
instead of asking the operator to copy-paste.

All Basecamp I/O is faked by monkeypatching mcp_tools._bc_request, so no
real network traffic. Covers: happy path (body + paginated comments),
body-only, max_comments truncation, 404 / 403 / unreachable error
mapping, arg validation, the HTML->text helper, and that the tool is
registered + reachable through call_tool().
"""
from __future__ import annotations

import pytest

from execution.products.library import mcp_tools


class _FakeUser:
    def __init__(self, display_name="Swati R", email="swati@colaberry.com",
                 user_id="u-swati"):
        self.display_name = display_name
        self.email = email
        self.user_id = user_id


# ── Fake BC backend ─────────────────────────────────────────────────


def _todo_payload(**over):
    base = {
        "id": 9946498513,
        "title": "Draft weekly TWC registration status email",
        "content": "Draft weekly TWC registration status email",
        "description": "<div>Treat the courses as <strong>Exemption</strong>, "
                       "not Seminar.</div><div>See the other ticket.</div>",
        "completed": False,
        "status": "active",
        "assignees": [{"id": 1, "name": "Swati R"}, {"id": 2, "name": "Ali M"}],
        "due_on": "2026-06-20",
        "creator": {"id": 9, "name": "Ali M"},
        "created_at": "2026-06-15T10:00:00Z",
        "updated_at": "2026-06-17T09:00:00Z",
        "parent": {"id": 555, "title": "TWC", "type": "Todolist"},
        "comments_count": 2,
        "comments_url": "https://3.basecampapi.com/3945211/buckets/47502609/recordings/9946498513/comments.json",
        "app_url": "https://3.basecamp.com/3945211/buckets/47502609/todos/9946498513",
    }
    base.update(over)
    return base


def _comment(cid, name, text):
    return {
        "id": cid,
        "creator": {"id": 9, "name": name},
        "created_at": "2026-06-16T12:00:00Z",
        "content": f"<div>{text}</div>",
        "app_url": f"https://3.basecamp.com/c/{cid}",
    }


def _make_fake_bc(todo=None, comment_pages=None, raise_on_todo=None):
    """Return a fake _bc_request. `comment_pages` is a list of page payloads
    (page 1, page 2, ...); requests past the last page return []."""
    todo = todo if todo is not None else _todo_payload()
    comment_pages = comment_pages or []

    def fake(method, url, payload=None, user=None, **kw):
        if "/todos/" in url and url.endswith(".json"):
            if raise_on_todo:
                raise RuntimeError(raise_on_todo)
            return todo
        if "/comments.json" in url:
            # url has ...comments.json?page=N
            page = 1
            if "page=" in url:
                page = int(url.split("page=")[-1])
            idx = page - 1
            return comment_pages[idx] if 0 <= idx < len(comment_pages) else []
        raise AssertionError(f"unexpected BC call: {method} {url}")

    return fake


# ── Happy path ──────────────────────────────────────────────────────


def test_read_ticket_returns_body_and_comments(monkeypatch):
    pages = [[_comment(1, "Ali M", "Please change the wording."),
              _comment(2, "Swati R", "Done, updated.")]]
    monkeypatch.setattr(mcp_tools, "_bc_request",
                        _make_fake_bc(comment_pages=pages))
    res = mcp_tools._tool_read_ticket(
        _FakeUser(), {"bc_project_id": 47502609, "ticket_id": 9946498513})

    assert res["ok"] is True
    t = res["ticket"]
    assert t["title"] == "Draft weekly TWC registration status email"
    assert "Exemption" in t["description_text"]
    assert "<strong>" in t["description_html"]
    # stripped text drops tags
    assert "<div>" not in t["description_text"]
    assert t["assignees"] == ["Swati R", "Ali M"]
    assert t["todolist"] == "TWC"
    assert t["url"].endswith("/todos/9946498513")

    assert res["comments_returned"] == 2
    assert res["comments_truncated"] is False
    assert res["comments"][0]["author"] == "Ali M"
    assert res["comments"][0]["content_text"] == "Please change the wording."
    assert res["comments"][1]["author"] == "Swati R"


def test_read_ticket_paginates_until_empty(monkeypatch):
    pages = [[_comment(i, "Ali M", f"c{i}") for i in range(1, 4)],
             [_comment(i, "Ali M", f"c{i}") for i in range(4, 6)]]
    monkeypatch.setattr(
        mcp_tools, "_bc_request",
        _make_fake_bc(todo=_todo_payload(comments_count=5), comment_pages=pages))
    res = mcp_tools._tool_read_ticket(
        _FakeUser(), {"bc_project_id": 47502609, "ticket_id": 9946498513})
    assert res["comments_returned"] == 5
    assert res["comments_truncated"] is False


def test_read_ticket_respects_max_comments(monkeypatch):
    pages = [[_comment(i, "Ali M", f"c{i}") for i in range(1, 11)]]
    monkeypatch.setattr(
        mcp_tools, "_bc_request",
        _make_fake_bc(todo=_todo_payload(comments_count=10), comment_pages=pages))
    res = mcp_tools._tool_read_ticket(
        _FakeUser(),
        {"bc_project_id": 47502609, "ticket_id": 9946498513, "max_comments": 3})
    assert res["comments_returned"] == 3
    assert res["comments_truncated"] is True


def test_read_ticket_include_comments_false_skips_thread(monkeypatch):
    def fake(method, url, payload=None, user=None, **kw):
        if "/comments.json" in url:
            raise AssertionError("should not fetch comments when disabled")
        return _todo_payload()
    monkeypatch.setattr(mcp_tools, "_bc_request", fake)
    res = mcp_tools._tool_read_ticket(
        _FakeUser(),
        {"bc_project_id": 47502609, "ticket_id": 9946498513,
         "include_comments": False})
    assert res["ok"] is True
    assert res["comments_returned"] == 0
    assert res["comments"] == []


def test_read_ticket_no_comments_when_count_zero(monkeypatch):
    monkeypatch.setattr(
        mcp_tools, "_bc_request",
        _make_fake_bc(todo=_todo_payload(comments_count=0)))
    res = mcp_tools._tool_read_ticket(
        _FakeUser(), {"bc_project_id": 47502609, "ticket_id": 9946498513})
    assert res["ok"] is True
    assert res["comments_returned"] == 0


# ── Error mapping ───────────────────────────────────────────────────


def test_read_ticket_404_maps_to_not_found(monkeypatch):
    monkeypatch.setattr(
        mcp_tools, "_bc_request",
        _make_fake_bc(raise_on_todo="BC GET ... -> HTTP 404 Not Found: x"))
    res = mcp_tools._tool_read_ticket(
        _FakeUser(), {"bc_project_id": 47502609, "ticket_id": 1})
    assert res["ok"] is False
    assert res["error"] == "ticket_not_found"
    assert "remediation" in res


def test_read_ticket_403_maps_to_forbidden(monkeypatch):
    monkeypatch.setattr(
        mcp_tools, "_bc_request",
        _make_fake_bc(raise_on_todo="BC GET ... -> HTTP 403 Forbidden: nope"))
    res = mcp_tools._tool_read_ticket(
        _FakeUser(), {"bc_project_id": 47502609, "ticket_id": 1})
    assert res["ok"] is False
    assert res["error"] == "ticket_forbidden"


def test_read_ticket_other_error_is_unreachable(monkeypatch):
    monkeypatch.setattr(
        mcp_tools, "_bc_request",
        _make_fake_bc(raise_on_todo="BC GET ... -> HTTP 500 Server Error"))
    res = mcp_tools._tool_read_ticket(
        _FakeUser(), {"bc_project_id": 47502609, "ticket_id": 1})
    assert res["ok"] is False
    assert res["error"] == "ticket_unreachable"


def test_read_ticket_comment_fetch_error_is_non_fatal(monkeypatch):
    def fake(method, url, payload=None, user=None, **kw):
        if "/comments.json" in url:
            raise RuntimeError("BC GET ... -> HTTP 500 transient")
        return _todo_payload()
    monkeypatch.setattr(mcp_tools, "_bc_request", fake)
    res = mcp_tools._tool_read_ticket(
        _FakeUser(), {"bc_project_id": 47502609, "ticket_id": 9946498513})
    # Body still returned; comment error surfaced but ok stays true.
    assert res["ok"] is True
    assert res["ticket"]["title"]
    assert "comments_error" in res["ticket"]


# ── Arg validation ──────────────────────────────────────────────────


@pytest.mark.parametrize("args", [
    {},
    {"bc_project_id": 47502609},
    {"ticket_id": 9946498513},
    {"bc_project_id": 0, "ticket_id": 0},
])
def test_read_ticket_requires_both_ids(args):
    res = mcp_tools._tool_read_ticket(_FakeUser(), args)
    assert res["ok"] is False
    assert "required" in res["error"]


def test_read_ticket_non_integer_ids_rejected():
    res = mcp_tools._tool_read_ticket(
        _FakeUser(), {"bc_project_id": "abc", "ticket_id": "xyz"})
    assert res["ok"] is False
    assert "integers" in res["error"]


def test_read_ticket_accepts_todo_id_alias(monkeypatch):
    monkeypatch.setattr(
        mcp_tools, "_bc_request",
        _make_fake_bc(todo=_todo_payload(comments_count=0)))
    res = mcp_tools._tool_read_ticket(
        _FakeUser(), {"bc_project_id": 47502609, "todo_id": 9946498513})
    assert res["ok"] is True


# ── HTML -> text helper ─────────────────────────────────────────────


def test_html_to_text_handles_blocks_and_entities():
    html = "<div>Line one</div><div>R&amp;D &gt; sales</div>"
    out = mcp_tools._html_to_text(html)
    assert "Line one" in out
    assert "R&D > sales" in out
    assert "<div>" not in out


def test_html_to_text_renders_list_items_as_bullets():
    out = mcp_tools._html_to_text("<ul><li>alpha</li><li>beta</li></ul>")
    assert "- alpha" in out
    assert "- beta" in out
    # rendered as two separate bullet lines
    assert out.count("- ") == 2


def test_html_to_text_empty_is_empty():
    assert mcp_tools._html_to_text("") == ""
    assert mcp_tools._html_to_text(None) == ""


# ── Registration / dispatch ─────────────────────────────────────────


def test_read_ticket_is_registered():
    assert "colaberry_read_ticket" in mcp_tools.TOOL_BY_NAME
    tool = mcp_tools.TOOL_BY_NAME["colaberry_read_ticket"]
    assert tool.input_schema["required"] == ["bc_project_id", "ticket_id"]


def test_read_ticket_reachable_via_call_tool(monkeypatch):
    monkeypatch.setattr(
        mcp_tools, "_bc_request",
        _make_fake_bc(todo=_todo_payload(comments_count=0)))
    res = mcp_tools.call_tool(
        "colaberry_read_ticket", _FakeUser(),
        {"bc_project_id": 47502609, "ticket_id": 9946498513})
    assert res["ok"] is True
    assert res["ticket"]["ticket_id"] == 9946498513
