"""Tests for the AI/human task-kind parsing + tier override in the scorer.

The My-Day "create a new project" build encodes each task's kind in the
Basecamp todo (🤖 / [AI] vs 🧑 / [Human]). The scorer parses it so the My Day
tier=human / tier=ai split works by design, regardless of numeric urgency.
"""
from execution.products.ops import scorer
from execution.products.ops.store import OpsTodo


def _todo(title, description="", assignee_ids=None, due_on=None):
    return OpsTodo(
        bc_id=1, bc_project_id=2, bc_project_name="P",
        bc_todolist_id=3, bc_todolist_name="L",
        title=title, description=description,
        assignee_ids=assignee_ids or [], due_on=due_on,
    )


# ── task_kind parsing ───────────────────────────────────────────────

def test_task_kind_from_title_emoji():
    assert scorer.task_kind(_todo("🤖 Implement the pricing layer")) == "ai"
    assert scorer.task_kind(_todo("🧑 Approve the budget")) == "human"


def test_task_kind_from_description_tag():
    assert scorer.task_kind(_todo("Implement X", "<p>[AI]</p>")) == "ai"
    assert scorer.task_kind(_todo("Approve X", "<p>[Human]</p>")) == "human"


def test_task_kind_unmarked():
    assert scorer.task_kind(_todo("Some normal todo", "no marker here")) == ""


def test_human_tag_wins_when_both_present():
    assert scorer.task_kind(_todo("🤖 weird", "<p>[Human]</p>")) == "human"


# ── category override ───────────────────────────────────────────────

def test_ai_task_never_human_required_even_when_urgent():
    # Overdue + assigned would normally score into human_required.
    todo = _todo("🤖 Build the MCP server", "<p>[AI]</p>",
                 assignee_ids=[999], due_on="2020-01-01")
    s = scorer.score_todo(todo)
    assert s["urgency"] >= 60          # genuinely urgent
    assert s["category"] != "human_required"  # but stays in the AI tier
    assert s["breakdown"]["kind"] == "ai"


def test_human_task_forced_into_human_tier_even_when_low_urgency():
    # No due date, no keywords → low urgency, normally "unscored".
    todo = _todo("🧑 Approve the rollout", "<p>[Human]</p>", assignee_ids=[999])
    s = scorer.score_todo(todo)
    assert s["urgency"] < 60
    assert s["category"] == "human_required"   # forced by kind
    assert s["breakdown"]["kind"] == "human"


def test_unmarked_todo_unchanged_regression():
    # An ordinary overdue assigned todo still escalates as before.
    todo = _todo("Ship the thing", assignee_ids=[999], due_on="2020-01-01")
    s = scorer.score_todo(todo)
    assert s["category"] == "human_required"
    assert s["breakdown"]["kind"] == ""
