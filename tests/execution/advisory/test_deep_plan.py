"""Tests for the deep-plan maker/checker generator (LLM mocked)."""

import json

import pytest

import execution.advisory.deep_plan as dp


@pytest.fixture
def fake_llm(monkeypatch):
    """Mock the single LLM seam (_chat) and is_available."""
    import execution.llm_client as llm
    monkeypatch.setattr(llm, "is_available", lambda: True)

    def fake_chat(system, user, max_tokens, as_json):
        if "PASS requires" in user:                      # the checker
            return '{"score": 92, "pass": true, "gaps": []}'
        if as_json:                                       # the tickets draft
            return json.dumps({"sprints": [{"key": "s0", "title": "Foundation", "goal": "g",
                "tickets": [{"title": "t1", "story": "s", "design": "d", "build": "b", "test": "x"}]}]})
        return "# Document\n\nDeep, comprehensive content for this product."

    monkeypatch.setattr(dp, "_chat", fake_chat)


def test_generate_guards_bad_idea():
    for bad in ("", "  ", "undefined", "null", "N/A"):
        with pytest.raises(ValueError):
            dp.generate_deep_plan(bad, "choices", "Proj")


def test_generate_returns_full_plan(fake_llm):
    plan = dp.generate_deep_plan("A real product idea for a niche audience", "- cap: choice", "Proj")
    assert plan["project"] == "Proj"
    assert plan["requirements"].startswith("# Document")
    assert plan["architecture"] and plan["build_guide"]
    assert plan["ticket_count"] == 1
    assert plan["tickets"]["sprints"][0]["key"] == "s0"


def test_make_check_stops_on_pass(fake_llm, monkeypatch):
    calls = {"n": 0}
    orig = dp._chat

    def counting(system, user, max_tokens, as_json):
        calls["n"] += 1
        return orig(system, user, max_tokens, as_json)

    monkeypatch.setattr(dp, "_chat", counting)
    out = dp._make_check("requirements", "make a doc", dp._rubric("P", "i"), False)
    assert out.startswith("# Document")
    # draft + one verify (passes) = 2 calls, no refine loop
    assert calls["n"] == 2


def test_chat_injects_json_word(monkeypatch):
    captured = {}
    import execution.llm_client as llm

    class R:
        content = "{}"

    def chat(**kw):
        captured["user"] = kw["messages"][0]["content"]
        return R()

    monkeypatch.setattr(llm, "chat", chat)
    dp._chat("sys", "give me data", 100, True)          # no 'json' in prompt
    assert "json" in captured["user"].lower()


def test_store_deep_plan_writes_files(tmp_path, monkeypatch):
    monkeypatch.setattr(dp, "OUTPUT_DIR", tmp_path)
    plan = {"project": "P", "requirements": "R", "architecture": "A", "build_guide": "G",
            "tickets": {"sprints": []}, "ticket_count": 0}
    paths = dp.store_deep_plan("slug-x", plan)
    assert (tmp_path / "slug-x" / "docs" / "REQUIREMENTS.md").read_text(encoding="utf-8") == "R"
    assert (tmp_path / "slug-x" / "deep_plan.json").exists()
    assert paths["build_guide"].endswith("BUILD_GUIDE.md")
