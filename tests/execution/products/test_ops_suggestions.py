"""Unit tests for action recipe matching + Claude Code prompt generation."""
from execution.products.ops.store import OpsTodo
from execution.products.ops.suggestions import build_suggestion, generate_prompt


def _make(title, desc=""):
    return OpsTodo(
        bc_id=1, bc_project_id=1, bc_project_name="Test Project",
        bc_todolist_id=1, bc_todolist_name="Main",
        title=title, description=desc,
        bc_app_url="https://3.basecamp.com/x/y",
    )


def test_decision_keyword_matches():
    s = build_suggestion(_make("Please approve the budget"))
    assert s["action_kind"] == "decision"


def test_reply_keyword_matches():
    s = build_suggestion(_make("Reply to Karun's email"))
    assert s["action_kind"] == "reply"


def test_meeting_keyword_matches():
    s = build_suggestion(_make("Schedule the kickoff sync"))
    assert s["action_kind"] == "meeting"


def test_research_keyword_matches():
    s = build_suggestion(_make("Investigate the cost overrun"))
    assert s["action_kind"] == "research"


def test_build_keyword_matches():
    s = build_suggestion(_make("Implement the new auth flow"))
    assert s["action_kind"] == "build"


def test_review_keyword_matches():
    s = build_suggestion(_make("QA the new dashboard"))
    assert s["action_kind"] == "review"


def test_default_recipe_when_no_match():
    s = build_suggestion(_make("Generic placeholder"))
    assert s["action_kind"] == "default"


def test_suggestion_has_required_fields():
    s = build_suggestion(_make("Approve the doc"))
    for k in ("action_kind", "one_line", "steps", "resources", "stop_conditions", "urgency_summary"):
        assert k in s
    assert s["steps"]   # non-empty


def test_generate_prompt_includes_title_and_steps():
    t = _make("Approve the budget", desc="Need sign-off by Friday")
    prompt = generate_prompt(t)
    assert "Approve the budget" in prompt
    assert "Test Project" in prompt
    assert "Need sign-off by Friday" in prompt
    assert "decision" in prompt
    assert "https://3.basecamp.com/x/y" in prompt
    # Steps should be numbered
    assert "1." in prompt


def test_generate_prompt_handles_no_description():
    t = _make("Reply to email")
    prompt = generate_prompt(t)
    assert "Reply to email" in prompt
    # description block should be absent (no orphan "## Description" heading)
    assert "## Description" not in prompt


def test_generate_prompt_handles_no_due_date():
    t = _make("Some task")
    prompt = generate_prompt(t)
    assert "no due date" in prompt
