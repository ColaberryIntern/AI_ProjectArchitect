"""Unit tests for the deterministic urgency scorer."""
from datetime import datetime, timedelta, timezone

from execution.products.ops.scorer import score_todo
from execution.products.ops.store import OpsTodo


def _make(title="X", desc="", due_on=None, updated_at=None, assignees=(17454835,)):
    return OpsTodo(
        bc_id=1, bc_project_id=1, bc_project_name="P",
        bc_todolist_id=1, bc_todolist_name="L",
        title=title, description=desc, due_on=due_on,
        assignee_ids=list(assignees),
        bc_updated_at=updated_at or "",
    )


def _today():
    return datetime.now(timezone.utc).date()


def _date_offset(days):
    return (_today() + timedelta(days=days)).strftime("%Y-%m-%d")


def test_overdue_scores_high():
    t = _make(due_on=_date_offset(-2), updated_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
    s = score_todo(t)
    assert s["urgency"] >= 60
    assert s["category"] == "human_required"


def test_due_today_scores_high():
    t = _make(due_on=_date_offset(0), updated_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
    s = score_todo(t)
    assert s["urgency"] >= 55


def test_no_due_no_keyword_unassigned_is_low():
    t = _make(due_on=None, updated_at=datetime.now(timezone.utc).isoformat(), assignees=())
    s = score_todo(t)
    # No due (0), no staleness (0), no keyword (0), no assignee (0), project_signal (5) -> 5
    assert s["urgency"] == 5
    assert s["category"] == "unscored"


# ── Generation gate: pending-artifact approval tasks reroute to the drafter ──
# approval-task-dependency-linking.md — an approval/review task whose artifact
# is not yet attached is blocked on the drafter, never an approver delay. It
# must not land in the human_required / CRITICAL escalation band.

def test_pending_artifact_approval_task_is_waiting_dependency_not_human():
    # Overdue + assigned would normally score human_required (the exact shape
    # that caused the 8-day false CRITICAL escalation). The PENDING marker must
    # reclassify it as blocked-on-drafter.
    desc = (
        "<strong>Depends-on:</strong> https://3.basecamp.com/x/buckets/1/todos/9 "
        "<strong>Artifact:</strong> PENDING"
    )
    t = _make(title="Approve the sales call script", desc=desc,
              due_on=_date_offset(-3),
              updated_at=(datetime.now(timezone.utc) - timedelta(days=2)).isoformat())
    s = score_todo(t)
    assert s["category"] == "waiting_dependency"
    assert s["breakdown"]["artifact_gated"] is True


def test_attached_artifact_approval_task_keeps_normal_category():
    # Same task once the artifact is attached (a real URL, not PENDING) is a
    # genuine approver action and keeps its normal human_required scoring.
    desc = (
        "<strong>Depends-on:</strong> https://3.basecamp.com/x/buckets/1/todos/9 "
        "<strong>Artifact:</strong> https://3.basecamp.com/x/buckets/1/uploads/55"
    )
    t = _make(title="Approve the sales call script", desc=desc,
              due_on=_date_offset(-3),
              updated_at=(datetime.now(timezone.utc) - timedelta(days=2)).isoformat())
    s = score_todo(t)
    assert s["category"] == "human_required"
    assert s["breakdown"]["artifact_gated"] is False


def test_urgent_keyword_bumps_score():
    t = _make(title="URGENT: fix the deploy",
              updated_at=(datetime.now(timezone.utc) - timedelta(days=2)).isoformat())
    s = score_todo(t)
    assert s["breakdown"]["keyword_tier"] == "urgent"
    assert s["breakdown"]["components"]["keyword"] == 15


def test_decide_keyword_lower_tier():
    t = _make(title="Please review the doc",
              updated_at=(datetime.now(timezone.utc) - timedelta(days=2)).isoformat())
    s = score_todo(t)
    assert s["breakdown"]["keyword_tier"] == "decide"
    assert s["breakdown"]["components"]["keyword"] == 5


def test_stale_no_due_no_keyword_becomes_waiting_dependency():
    t = _make(updated_at=(datetime.now(timezone.utc) - timedelta(days=14)).isoformat())
    s = score_todo(t)
    assert s["category"] == "waiting_dependency"


def test_assignee_present_adds_15():
    assigned = _make(assignees=(123,))
    orphan = _make(assignees=())
    assert score_todo(assigned)["breakdown"]["components"]["assignee"] == 15
    assert score_todo(orphan)["breakdown"]["components"]["assignee"] == 0


def test_project_weight_multiplies():
    t = _make(title="URGENT review",
              updated_at=(datetime.now(timezone.utc) - timedelta(days=2)).isoformat())
    s_default = score_todo(t, project_weight=1.0)
    s_low = score_todo(t, project_weight=0.4)
    s_high = score_todo(t, project_weight=2.0)
    assert s_low["urgency"] < s_default["urgency"] < s_high["urgency"]
    assert s_high["urgency"] <= 100  # capped


def test_urgency_capped_at_100():
    t = _make(title="URGENT CRITICAL EMERGENCY",
              due_on=_date_offset(-30),
              updated_at=(datetime.now(timezone.utc) - timedelta(days=60)).isoformat(),
              assignees=(1,))
    s = score_todo(t, project_weight=2.0)
    assert s["urgency"] == 100
