"""Tests for the story-driven deep-plan generator (LLM mocked)."""

import json

import pytest

import execution.advisory.deep_plan as dp


@pytest.fixture
def fake_llm(monkeypatch):
    """Mock the single LLM seam (_chat) for every stage of the chain."""
    import execution.llm_client as llm
    monkeypatch.setattr(llm, "is_available", lambda: True)

    def fake_chat(system, user, max_tokens, as_json):
        if "PASS requires" in user:                                   # any checker
            return '{"score": 92, "pass": true, "gaps": []}'
        if "machine-readable REQ catalog" in user:                    # stage 1b extract
            return json.dumps({"reqs": [
                {"id": "REQ-001", "priority": "must", "statement": "book online",
                 "acceptance": ["a", "b"], "cluster": "Booking"},
                {"id": "REQ-002", "priority": "should", "statement": "feedback",
                 "acceptance": ["a", "b"], "cluster": "Feedback"}]})
        if "MULTI-AGENT ORGANIZATION" in user:                        # stage 2
            return json.dumps({"agents": [
                {"name": "Coordinator", "owns": [], "commands": "route", "reacts": "x", "gate": ""},
                {"name": "Booking Agent", "owns": ["REQ-001", "REQ-002"],
                 "commands": "Book", "reacts": "Req", "gate": "overbook → manager"}]})
        if "vertical-slice USER STORIES" in user:                     # stage 3 (per cluster)
            req = "REQ-001" if "REQ-001" in user else "REQ-002"
            return json.dumps({"stories": [
                {"title": f"Story for {req} (a)", "fulfills": [req], "narrative": "As a user I want X so that Y",
                 "slice": "Cmd → Event → Read", "owner_agent": "Booking Agent",
                 "acceptance": [{"scenario": "happy", "trust": False, "given": "g", "when": "w", "then": "t"},
                                {"scenario": "audited", "trust": True, "given": "g", "when": "w", "then": "logged"}],
                 "build": "Supabase + Make.com", "vibe": "build it", "trust": "audit log"},
                {"title": f"Story for {req} (b)", "fulfills": [req], "narrative": "As a user I want X so that Y",
                 "slice": "Cmd → Event → Read", "owner_agent": "Booking Agent",
                 "acceptance": [{"scenario": "happy", "trust": False, "given": "g", "when": "w", "then": "t"},
                                {"scenario": "approval", "trust": True, "given": "g", "when": "w", "then": "held"}],
                 "build": "Retool", "vibe": "build it", "trust": "approval gate"}]})
        if "story map of dynamic RELEASES" in user:                   # stage 4
            return json.dumps({"releases": [
                {"key": "r0", "name": "Walking Skeleton", "goal": "thin slice",
                 "stories": ["STORY-001", "STORY-002"], "demo": "book + audit"},
                {"key": "r1", "name": "Feedback", "goal": "loop",
                 "stories": ["STORY-003", "STORY-004"], "demo": "feedback"}]})
        return "# Document\n\nDeep, comprehensive content with REQ-001 and REQ-002."

    monkeypatch.setattr(dp, "_chat", fake_chat)


def test_generate_guards_bad_idea():
    for bad in ("", "  ", "undefined", "null", "N/A"):
        with pytest.raises(ValueError):
            dp.generate_deep_plan(bad, "choices", "Proj")


def test_generate_returns_story_plan(fake_llm):
    plan = dp.generate_deep_plan("A real product idea for a niche audience", "- cap: choice", "Proj")
    assert plan["project"] == "Proj"
    # structured artifacts
    assert {r["id"] for r in plan["reqs"]} == {"REQ-001", "REQ-002"}
    assert any(a["name"] == "Booking Agent" for a in plan["agents"])
    assert plan["story_count"] == 4 and plan["ticket_count"] == 4
    # every story is well-formed and traceable
    for s in plan["stories"]:
        assert s["id"].startswith("STORY-")
        assert s["fulfills"] and all(f.startswith("REQ-") for f in s["fulfills"])
        assert any(sc["trust"] for sc in s["acceptance"])   # has a trust scenario
        assert s["release"]                                 # placed in a release
    # releases built with keys + week spans
    assert [r["key"] for r in plan["releases"]] == ["r0", "r1"]
    assert all("weeks" in r for r in plan["releases"])
    # deterministic gate ran and every must is covered
    assert plan["trace"]["must_orphans"] == []
    assert "REQ-001" in plan["rtm"]
    # trust is grounded in the canonical framework + shipped as a primer doc
    assert "INPACT" in plan["tbi_primer"] and "Approval gate" in plan["tbi_primer"]


def test_make_check_stops_on_pass(fake_llm, monkeypatch):
    calls = {"n": 0}
    orig = dp._chat

    def counting(system, user, max_tokens, as_json):
        calls["n"] += 1
        return orig(system, user, max_tokens, as_json)

    monkeypatch.setattr(dp, "_chat", counting)
    out = dp._make_check("requirements", "make a doc", dp._rubric("P", "i"), False)
    assert out.startswith("# Document")
    assert calls["n"] == 2                                  # draft + one passing verify


def test_chat_injects_json_word(monkeypatch):
    captured = {}
    import execution.llm_client as llm

    class R:
        content = "{}"

    def chat(**kw):
        captured["user"] = kw["messages"][0]["content"]
        return R()

    monkeypatch.setattr(llm, "chat", chat)
    dp._chat("sys", "give me data", 100, True)
    assert "json" in captured["user"].lower()


def test_assign_release_weeks_skeleton_first():
    spans = dp._assign_release_weeks(6)
    assert spans[0] == (3, 3)                               # r0 = week 3, the skeleton
    assert spans[-1][1] <= 11                               # build ends by week 11
    assert all(a <= b for a, b in spans)                    # well-ordered


def test_normalize_reqs_coerces_ids_and_priority():
    reqs = dp._normalize_reqs([{"statement": "x"}, {"id": "req-5", "priority": "CRITICAL", "acceptance": "one"}])
    assert reqs[0]["id"] == "REQ-001"                       # missing id → positional
    assert reqs[1]["id"] == "REQ-5".upper()
    assert reqs[1]["priority"] == "should"                  # unknown priority → should
    assert reqs[1]["acceptance"] == ["one"]                 # string → list


def test_store_deep_plan_writes_files(tmp_path, monkeypatch):
    monkeypatch.setattr(dp, "OUTPUT_DIR", tmp_path)
    plan = {"project": "P", "requirements": "R", "architecture": "A", "build_guide": "G",
            "rtm": "MATRIX", "reqs": [], "agents": [], "stories": [], "releases": [],
            "trace": {}, "story_count": 0, "ticket_count": 0}
    paths = dp.store_deep_plan("slug-x", plan)
    assert (tmp_path / "slug-x" / "docs" / "REQUIREMENTS.md").read_text(encoding="utf-8") == "R"
    assert (tmp_path / "slug-x" / "docs" / "TRACEABILITY.md").read_text(encoding="utf-8") == "MATRIX"
    assert (tmp_path / "slug-x" / "deep_plan.json").exists()
    assert paths["rtm"].endswith("TRACEABILITY.md")
