"""Unit tests for the Basecamp build writer (pure logic + BC payloads)."""
from datetime import date
from types import SimpleNamespace

import pytest

from execution.advisory import basecamp_build_writer as bw


# ── pace → due dates ────────────────────────────────────────────────

def test_spread_due_dates_empty():
    assert bw.spread_due_dates(0, "standard") == []


def test_spread_due_dates_single_lands_on_window_edge():
    start = date(2026, 1, 1)
    out = bw.spread_due_dates(1, "sprint", start=start)
    assert out == [date(2026, 1, 8).isoformat()]  # +7 days


def test_spread_due_dates_last_is_window_edge_and_monotonic():
    start = date(2026, 1, 1)
    out = bw.spread_due_dates(5, "standard", start=start)
    assert len(out) == 5
    assert out == sorted(out)               # monotonic non-decreasing
    assert out[-1] == date(2026, 1, 31).isoformat()  # +30 days
    assert out[0] > start.isoformat()       # first is at least 1 day out


def test_spread_due_dates_pace_windows():
    assert bw.PACE_DAYS == {"sprint": 7, "standard": 30, "relaxed": 90}
    start = date(2026, 1, 1)
    # relaxed window is 90 days → single task lands at +90 = 2026-04-01
    assert bw.spread_due_dates(1, "relaxed", start=start)[0] == "2026-04-01"


def test_unknown_pace_defaults_to_standard():
    start = date(2026, 1, 1)
    assert bw.spread_due_dates(1, "whatever", start=start)[0] == date(2026, 1, 31).isoformat()


# ── kind classification ─────────────────────────────────────────────

def test_classify_human_signals():
    assert bw.classify_task_kind({"name": "Decide on pricing model"}) == "human"
    assert bw.classify_task_kind({"name": "Provide API key for Stripe"}) == "human"
    assert bw.classify_task_kind({"name": "Stakeholder sign-off", "description": ""}) == "human"


def test_classify_ai_default():
    assert bw.classify_task_kind({"name": "Implement the pricing layer"}) == "ai"
    assert bw.classify_task_kind({"name": "Scaffold the MCP server", "description": "build it"}) == "ai"


# ── todo payload ────────────────────────────────────────────────────

def test_build_todo_payload_ai():
    req = {
        "name": "Implement pricing layer",
        "description": "Compute sell price from cost",
        "priority": "must",
        "requirement_type": "functional",
        "acceptance_criteria": ["Given a cost, a sell price is returned"],
    }
    p = bw._build_todo_payload(req, "ai", 999, "2026-02-01")
    assert p["content"].startswith(bw.AI_EMOJI)
    assert "Implement pricing layer" in p["content"]
    assert p["assignee_ids"] == [999]
    assert p["due_on"] == "2026-02-01"
    assert "[AI]" in p["description"]
    assert "<li>Given a cost, a sell price is returned</li>" in p["description"]
    assert "Priority:" in p["description"]


def test_build_todo_payload_human_tag():
    p = bw._build_todo_payload({"name": "Approve budget"}, "human", 7, "2026-02-02")
    assert p["content"].startswith(bw.HUMAN_EMOJI)
    assert "[Human]" in p["description"]


def test_build_todo_payload_escapes_html():
    p = bw._build_todo_payload({"name": "x", "description": "a <script> & b"}, "ai", 1, "2026-02-02")
    assert "<script>" not in p["description"]
    assert "&lt;script&gt;" in p["description"]


# ── publish_to_basecamp (BC calls faked) ────────────────────────────

class _FakeBC:
    """Routes mcp_tools._bc_request by URL; records POSTed todos."""

    def __init__(self):
        self.todo_posts = []
        self.created_list = False

    def request(self, method, url, payload=None, user=None, **kw):
        # NOTE: the real todolists_url contains "/todosets/", so the POST
        # branches must be checked before the todoset GET branch.
        if url.endswith("/people.json"):
            return [{"id": 999, "email_address": "ali@colaberry.com", "name": "Ali"}]
        if method == "POST" and url.endswith("/todolists.json"):
            self.created_list = True
            return {"id": 555, "app_url": "https://bc/list/555", "name": payload.get("name")}
        if method == "POST" and "/todos.json" in url:
            self.todo_posts.append((url, payload))
            return {"id": 1000 + len(self.todo_posts), "app_url": "https://bc/todo"}
        if "/todosets/" in url:  # GET the todoset → todolists_url
            return {"todolists_url": "https://3.basecampapi.com/3945211/buckets/1/todosets/9/todolists.json"}
        if method == "GET" and "/projects/" in url and url.endswith(".json"):
            # the project GET inside _tool_create_todolist → dock with todoset
            return {"dock": [{"name": "todoset", "url": "https://3.basecampapi.com/3945211/buckets/1/todosets/9.json"}]}
        raise AssertionError(f"unexpected BC call: {method} {url}")


@pytest.fixture
def fake_bc(monkeypatch):
    fb = _FakeBC()
    monkeypatch.setattr(bw.mcp_tools, "_bc_request", fb.request)
    monkeypatch.setattr(bw.mcp_tools, "_bc_account", lambda: "3945211")
    return fb


def _user():
    return SimpleNamespace(email="ali@colaberry.com", user_id="ali@colaberry.com",
                           bc_user_id=999, display_name="Ali")


def test_publish_creates_list_and_assigned_due_dated_todos(fake_bc):
    reqs = [
        {"name": "Implement A", "build_order": 2, "priority": "should"},
        {"name": "Decide on vendor", "build_order": 1, "priority": "must"},
    ]
    res = bw.publish_to_basecamp(_user(), 1, "Build plan", reqs, "standard")
    assert fake_bc.created_list
    assert res["tasks_created"] == 2
    assert res["todolist_id"] == 555
    assert res["assignee_id"] == 999
    # ordered by build_order: the "Decide" task (order 1) comes first and is human
    first_payload = fake_bc.todo_posts[0][1]
    assert first_payload["content"].startswith(bw.HUMAN_EMOJI)
    # every todo is assigned and due-dated
    for _url, payload in fake_bc.todo_posts:
        assert payload["assignee_ids"] == [999]
        assert payload["due_on"]


def test_publish_raises_when_assignee_unresolvable(monkeypatch):
    fb = _FakeBC()
    # people.json returns nobody matching, and user has no bc_user_id
    fb.request_people_empty = True
    monkeypatch.setattr(bw.mcp_tools, "_bc_account", lambda: "3945211")

    def req(method, url, payload=None, user=None, **kw):
        if url.endswith("/people.json"):
            return []
        raise AssertionError("should not get past assignee resolution")

    monkeypatch.setattr(bw.mcp_tools, "_bc_request", req)
    user = SimpleNamespace(email="nomatch@x.com", user_id="x", bc_user_id=None)
    with pytest.raises(RuntimeError, match="assigned"):
        bw.publish_to_basecamp(user, 1, "Build plan", [{"name": "x"}], "standard")
