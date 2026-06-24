"""Tests for the project-plan → Basecamp reconciler (BC HTTP mocked)."""
from types import SimpleNamespace

import pytest

from execution.advisory import bc_manifest, project_plan
from execution.advisory import project_plan_reconciler as rec


class FakeBC:
    """Minimal Basecamp stand-in: assigns ids, records writes, supports re-reads."""

    def __init__(self):
        self.calls = []
        self._next = 1000
        self.todos = {}  # bc_id -> payload (last write)

    def _id(self):
        self._next += 1
        return self._next

    def request(self, method, url, payload=None, user=None, **kw):
        self.calls.append((method, url, payload))
        if method == "GET" and url.endswith(".json") and "/projects/" in url and "/buckets/" not in url:
            return {"dock": [{"name": "todoset", "url": "https://3.basecampapi.com/3945211/buckets/1/todosets/9.json"}]}
        if method == "GET":  # breadcrumb read-backs → empty (fresh project)
            return []
        if method == "POST" and url.endswith("/todolists.json"):
            return {"id": self._id()}
        if method == "POST" and url.endswith("/groups.json"):
            return {"id": self._id()}
        if method == "POST" and url.endswith("/todos.json"):
            tid = self._id()
            self.todos[tid] = payload
            return {"id": tid}
        if method == "PUT":
            return {}
        return {}


@pytest.fixture
def bc(monkeypatch, tmp_path):
    fake = FakeBC()
    monkeypatch.setattr(rec.mcp_tools, "_bc_request", fake.request)
    monkeypatch.setattr(rec.mcp_tools, "_bc_account", lambda: "3945211")
    monkeypatch.setattr(bc_manifest, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(rec, "_THROTTLE_S", 0)
    return fake


def _plan():
    plan = {
        "$schema": project_plan.SCHEMA, "projectSlug": "demo",
        "peopleMap": {}, "designs": [],
        "initiatives": [{
            "title": "Functional Requirements", "order": 4, "status": "active",
            "charter": "Core features.",
            "lists": [{
                "title": "Role Management", "order": 1, "status": "active",
                "todos": [
                    {"title": "CRUD endpoints", "phase": "BUILD", "kind": "ai",
                     "acceptance": "Returns correct codes.", "dueOffsetDays": 5,
                     "order": 1, "status": "active", "deps": []},
                    {"title": "Reject bad payloads", "phase": "BREAK", "kind": "ai",
                     "acceptance": "Invalid → 422.", "dueOffsetDays": 6,
                     "order": 2, "status": "active", "deps": []},
                ],
            }],
        }],
    }
    return project_plan.assign_ids(plan)


def _user():
    return SimpleNamespace(email="ali@colaberry.com", user_id="ali@colaberry.com", bc_user_id=42)


def test_first_run_creates_list_group_todos_and_manifest(bc):
    plan = _plan()
    summary = rec.reconcile(plan, "demo", _user(), 1, creator_id=42, start_date="2026-06-24")
    # 1 todolist + 1 group + 2 todos
    assert summary["created"] == 4
    assert summary["errors"] == []
    # todos assigned to creator + due-dated
    for payload in bc.todos.values():
        assert payload["assignee_ids"] == [42]
        assert payload["due_on"]
        assert payload["content"].startswith("[BUILD]") or payload["content"].startswith("[BREAK]")
    # manifest populated
    m = bc_manifest.load_manifest("demo")
    assert len([e for e in m["entries"].values() if e["bcType"] == "todo"]) == 2


def test_due_on_is_startdate_plus_offset(bc):
    plan = _plan()
    rec.reconcile(plan, "demo", _user(), 1, creator_id=42, start_date="2026-06-24")
    dues = sorted(p["due_on"] for p in bc.todos.values())
    assert dues == ["2026-06-29", "2026-06-30"]  # +5, +6 days


def test_rerun_is_idempotent_no_writes(bc):
    plan = _plan()
    rec.reconcile(plan, "demo", _user(), 1, creator_id=42, start_date="2026-06-24")
    writes_before = sum(1 for m, _u, _p in bc.calls if m in ("POST", "PUT"))
    bc.calls.clear()
    summary = rec.reconcile(plan, "demo", _user(), 1, creator_id=42, start_date="2026-06-24")
    writes_after = sum(1 for m, _u, _p in bc.calls if m in ("POST", "PUT"))
    assert writes_before > 0
    assert writes_after == 0          # hash gate → zero write calls
    assert summary["created"] == 0
    assert summary["skipped"] >= 4


def test_content_change_updates_in_place(bc):
    plan = _plan()
    rec.reconcile(plan, "demo", _user(), 1, creator_id=42, start_date="2026-06-24")
    # edit one todo's acceptance → same id, new hash → PUT update
    plan["initiatives"][0]["lists"][0]["todos"][0]["acceptance"] = "Now logs too."
    bc.calls.clear()
    summary = rec.reconcile(plan, "demo", _user(), 1, creator_id=42, start_date="2026-06-24")
    assert summary["updated"] == 1
    puts = [c for c in bc.calls if c[0] == "PUT" and "/todos/" in c[1]]
    assert len(puts) == 1
    # full field set sent on PUT (content required)
    assert "content" in puts[0][2] and "description" in puts[0][2]


def test_removed_node_is_archived(bc):
    plan = _plan()
    rec.reconcile(plan, "demo", _user(), 1, creator_id=42, start_date="2026-06-24")
    # remove the BREAK todo from the plan → reconciler archives it
    lst = plan["initiatives"][0]["lists"][0]
    lst["todos"] = [t for t in lst["todos"] if t["phase"] != "BREAK"]
    bc.calls.clear()
    summary = rec.reconcile(plan, "demo", _user(), 1, creator_id=42, start_date="2026-06-24")
    assert summary["retired"] == 1
    assert any("status/archived.json" in c[1] for c in bc.calls if c[0] == "PUT")


def test_name_prefix_applied_to_todolist_names(bc):
    plan = _plan()
    rec.reconcile(plan, "demo", _user(), 1, creator_id=42, start_date="2026-06-24",
                  name_prefix="[TEST1] ")
    list_posts = [c for c in bc.calls if c[0] == "POST" and c[1].endswith("/todolists.json")]
    assert list_posts, "expected a todolist POST"
    assert list_posts[0][2]["name"].startswith("[TEST1] ")
    # the plan id is derived from the unprefixed title (prefix doesn't pollute ids)
    assert any(e for e in bc_manifest.load_manifest("demo")["entries"]
               if e.startswith("INIT.ch04-functional-requirements"))


def test_proposed_nodes_are_skipped(bc):
    plan = _plan()
    plan["initiatives"][0]["lists"][0]["todos"][0]["status"] = "proposed"
    rec.reconcile(plan, "demo", _user(), 1, creator_id=42, start_date="2026-06-24")
    # only the BREAK todo (active) created → 1 list + 1 group + 1 todo
    todos = [e for e in bc_manifest.load_manifest("demo")["entries"].values() if e["bcType"] == "todo"]
    assert len(todos) == 1
