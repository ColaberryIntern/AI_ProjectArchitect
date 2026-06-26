"""Tests for the deep-plan Basecamp publisher (schedule, html, BC mapping mocked)."""

from datetime import date

import execution.advisory.deep_plan_publisher as pub


def test_anchor_is_week3_monday():
    # cohort week-1 Monday 2026-07-27 → first due date Mon 2026-08-10 (week 3)
    assert pub.anchor_from_cohort_start(date(2026, 7, 27)) == date(2026, 8, 10)


def test_week_span_is_nine_weeks():
    assert pub.WEEK_SPAN["s0"] == (3, 3)
    assert pub.WEEK_SPAN["s5"] == (11, 11)  # build ends week 11; wk12 = presentations


def test_due_dates_weekday_monotonic_in_window():
    anchor = date(2026, 8, 10)
    dues = pub._due_dates(4, "s2", anchor)  # weeks 5-6 → Aug 24 .. Sep 4
    assert len(dues) == 4
    assert dues == sorted(dues)                 # monotonic
    for d in dues:
        dd = date.fromisoformat(d)
        assert dd.weekday() < 5                  # weekday only
        assert date(2026, 8, 24) <= dd <= date(2026, 9, 4)


def test_ticket_html_has_fields():
    h = pub._ticket_html({"story": "As a user...", "agent": "Booker", "design": "d",
                          "build": "b", "test": "x", "vibe": "v", "tbi": "t"})
    for label in ("Story", "Owner agent", "Design", "Build", "Test", "Vibe-code", "Trust"):
        assert label in h


def test_md_html_converts_basics():
    out = pub._md_html("# Title\n\n- one\n**bold** text")
    assert "<h1>Title</h1>" in out
    assert "<li>one</li>" in out
    assert "<strong>bold</strong>" in out


def test_publish_deep_plan_maps_sprints_and_tickets(monkeypatch):
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
        assert assignees == [111] and due  # assigned + due-dated
        return 1

    monkeypatch.setattr(rec, "_create_group", _grp)
    monkeypatch.setattr(rec, "_create_todo", _todo)
    # no docs vault in this unit test
    monkeypatch.setattr(mcp_tools, "_bc_account", lambda: "acct")
    monkeypatch.setattr(mcp_tools, "_bc_request", lambda *a, **k: {"dock": []})

    plan = {"project": "P", "requirements": "R", "architecture": "A", "build_guide": "G",
            "tickets": {"sprints": [
                {"key": "s0", "title": "Foundation", "goal": "g", "tickets": [
                    {"title": "t1", "story": "s", "design": "d", "build": "b", "test": "x", "agent": "A1"}]},
                {"key": "s1", "title": "MVP", "goal": "g", "tickets": [
                    {"title": "t2", "story": "s", "design": "d", "build": "b", "test": "x"}]},
            ]}}
    res = pub.publish_deep_plan(plan, object(), 7463955, date(2026, 8, 10), "P - Sprint Build Plan", "P")
    assert res["todolist_id"] == 333
    assert res["created"] == 2
    assert len(created["groups"]) == 2
    assert created["groups"][0].startswith("S0 - Foundation")
