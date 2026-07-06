"""Tests for the story-driven deep-plan Basecamp publisher (BC mapping mocked)."""

from datetime import date

import execution.advisory.deep_plan_publisher as pub


def test_anchor_is_week3_monday():
    # cohort week-1 Monday 2026-07-27 → first due date Mon 2026-08-10 (week 3)
    assert pub.anchor_from_cohort_start(date(2026, 7, 27)) == date(2026, 8, 10)


def test_due_dates_weekday_monotonic_in_window():
    anchor = date(2026, 8, 10)
    dues = pub._due_dates(4, 5, 6, anchor)          # weeks 5-6 → Aug 24 .. Sep 4
    assert len(dues) == 4
    assert dues == sorted(dues)                      # monotonic
    for d in dues:
        dd = date.fromisoformat(d)
        assert dd.weekday() < 5                       # weekday only
        assert date(2026, 8, 24) <= dd <= date(2026, 9, 4)


def test_story_html_has_fields_and_gherkin():
    s = {"narrative": "As a user...", "fulfills": ["REQ-001", "REQ-009"], "owner_agent": "Booker",
         "slice": "Cmd → Event → Read", "build": "b", "vibe": "v", "trust": "audit",
         "acceptance": [{"scenario": "happy", "trust": False, "given": "g", "when": "w", "then": "t"},
                        {"scenario": "audited", "trust": True, "given": "g2", "when": "w2", "then": "logged"}]}
    h = pub._story_html(s, doc_links_html="<div>📎 Project documents:</div>")
    for token in ("Story", "Fulfills", "REQ-001", "REQ-009", "Owner agent", "Booker",
                  "Acceptance", "Given", "Then", "🛡", "Build", "Vibe-code", "Trust", "Loop stop",
                  "Project documents"):
        assert token in h


def test_md_html_converts_basics():
    out = pub._md_html("# Title\n\n- one\n**bold** text")
    assert "<h1>Title</h1>" in out
    assert "<li>one</li>" in out
    assert "<strong>bold</strong>" in out


def test_publish_deep_plan_maps_releases_and_stories(monkeypatch):
    import execution.advisory.basecamp_build_writer as bw
    import execution.advisory.project_plan_reconciler as rec
    from execution.products.library import mcp_tools

    created = {"groups": [], "todos": 0}
    monkeypatch.setattr(bw, "resolve_operator_bc_person_id", lambda u, b: 111)
    monkeypatch.setattr(rec, "_discover_todoset", lambda u, b: 222)
    monkeypatch.setattr(rec, "_create_todolist", lambda u, b, ts, name, desc="": 333)

    def _grp(u, b, lst, name):
        created["groups"].append(name)
        return 400 + len(created["groups"])

    def _todo(u, b, parent, content, desc, assignees, due):
        created["todos"] += 1
        assert assignees == [111] and due           # assigned + due-dated
        assert "Fulfills" in desc                    # carries traceability
        return 1

    monkeypatch.setattr(rec, "_create_group", _grp)
    monkeypatch.setattr(rec, "_create_todo", _todo)
    monkeypatch.setattr(mcp_tools, "_bc_account", lambda: "acct")
    monkeypatch.setattr(mcp_tools, "_bc_request", lambda *a, **k: {"dock": []})  # no vault → no docs

    def _story(sid, req):
        return {"id": sid, "title": f"t{sid}", "narrative": "As a user...", "fulfills": [req],
                "owner_agent": "Booking", "slice": "x",
                "acceptance": [{"scenario": "audited", "trust": True, "given": "g", "when": "w", "then": "logged"}],
                "build": "b", "vibe": "v", "trust": "audit"}

    plan = {"project": "P", "requirements": "R", "architecture": "A", "build_guide": "G", "rtm": "M",
            "story_count": 4,
            "releases": [
                {"key": "r0", "name": "Walking Skeleton", "goal": "g", "weeks": (3, 3),
                 "stories": ["STORY-001", "STORY-002"], "demo": "d"},
                {"key": "r1", "name": "Payments", "goal": "g", "weeks": (4, 4),
                 "stories": ["STORY-003", "STORY-004"], "demo": "d"}],
            "stories": [_story("STORY-001", "REQ-001"), _story("STORY-002", "REQ-002"),
                        _story("STORY-003", "REQ-005"), _story("STORY-004", "REQ-009")]}

    res = pub.publish_deep_plan(plan, object(), 7463955, date(2026, 8, 10), "P - Story-Driven Build", "P")
    assert res["todolist_id"] == 333
    assert res["created"] == 4
    assert len(created["groups"]) == 2
    assert created["groups"][0].startswith("R0 - Walking Skeleton")
    assert "(wk 3)" in created["groups"][0]


def test_publish_requires_assignee(monkeypatch):
    import execution.advisory.basecamp_build_writer as bw
    monkeypatch.setattr(bw, "resolve_operator_bc_person_id", lambda u, b: None)
    import pytest
    with pytest.raises(RuntimeError):
        pub.publish_deep_plan({"project": "P", "releases": [], "stories": []},
                              object(), 7463955, date(2026, 8, 10), "P", "P")


def test_story_html_marks_ai_and_human_kind():
    s = {"narrative": "As a user...", "fulfills": ["REQ-001"], "owner_agent": "Booker",
         "acceptance": [{"scenario": "audited", "trust": True, "given": "g", "when": "w", "then": "logged"}]}
    ai = pub._story_html(s, kind="ai")
    assert "🤖" in ai and "[AI]" in ai
    human = pub._story_html(s, kind="human")
    assert "🧑" in human and "[Human]" in human


def test_publish_splits_ai_vs_human_marker_and_keeps_operator_assignee(monkeypatch):
    """The split is a 🤖/🧑 marker (classifier-driven), NOT a reassignment: every
    story stays assigned to the operator so My Day can tier-split them."""
    import execution.advisory.basecamp_build_writer as bw
    import execution.advisory.project_plan_reconciler as rec
    from execution.products.library import mcp_tools

    todos = []
    monkeypatch.setattr(bw, "resolve_operator_bc_person_id", lambda u, b: 111)
    monkeypatch.setattr(rec, "_discover_todoset", lambda u, b: 222)
    monkeypatch.setattr(rec, "_create_todolist", lambda u, b, ts, name, desc="": 333)
    monkeypatch.setattr(rec, "_create_group", lambda u, b, lst, name: 400)
    monkeypatch.setattr(rec, "_create_todo",
                        lambda u, b, parent, content, desc, assignees, due:
                        todos.append({"content": content, "desc": desc, "assignees": assignees}) or 1)
    monkeypatch.setattr(mcp_tools, "_bc_account", lambda: "acct")
    monkeypatch.setattr(mcp_tools, "_bc_request", lambda *a, **k: {"dock": []})

    ai_story = {"id": "STORY-001", "title": "Ingest booking events", "narrative": "As a system I ingest",
                "fulfills": ["REQ-001"], "owner_agent": "Booker", "build": "wire a webhook",
                "acceptance": [{"scenario": "ok", "trust": True, "then": "logged"}]}
    human_story = {"id": "STORY-002", "title": "Stakeholder approves the budget", "narrative": "the sponsor decides",
                   "fulfills": ["REQ-002"], "owner_agent": "Governance", "build": "meet and sign off",
                   "acceptance": [{"scenario": "ok", "trust": True, "then": "recorded"}]}
    plan = {"project": "P", "story_count": 2,
            "releases": [{"key": "r0", "name": "WS", "goal": "g", "weeks": (3, 3),
                          "stories": ["STORY-001", "STORY-002"], "demo": "d"}],
            "stories": [ai_story, human_story]}

    pub.publish_deep_plan(plan, object(), 7463955, date(2026, 8, 10), "P", "P")
    assert len(todos) == 2
    assert all(t["assignees"] == [111] for t in todos)          # marker, not reassignment
    ai_todo = next(t for t in todos if "STORY-001" in t["content"])
    human_todo = next(t for t in todos if "STORY-002" in t["content"])
    assert ai_todo["content"].startswith("🤖") and "[AI]" in ai_todo["desc"]
    assert human_todo["content"].startswith("🧑") and "[Human]" in human_todo["desc"]
