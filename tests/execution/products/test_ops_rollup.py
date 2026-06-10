"""Unit tests for My Day rollups: 5-band scale, per-person group, BC URLs."""
from datetime import datetime, timedelta, timezone

from execution.products.ops import rollup
from execution.products.ops.store import OpsTodo


def _today():
    return datetime.now(timezone.utc).date()


def _offset(days):
    return (_today() + timedelta(days=days)).strftime("%Y-%m-%d")


def _make(bc_id=1, proj=1, proj_name="P", list_id=1, list_name="L",
          due_on=None, urgency=0, category="unscored", assignees=None,
          app_url=""):
    return OpsTodo(
        bc_id=bc_id, bc_project_id=proj, bc_project_name=proj_name,
        bc_todolist_id=list_id, bc_todolist_name=list_name,
        title=f"T{bc_id}", due_on=due_on,
        assignee_names=assignees or [],
        urgency_score=urgency, category=category,
        bc_app_url=app_url,
    )


# ── score_band: 5 distinct buckets, red -> green ──────────────────

def test_score_band_five_distinct_buckets():
    keys = [rollup.score_band(s)["key"] for s in (10, 47, 60, 78, 92)]
    assert keys == ["b1", "b2", "b3", "b4", "b5"]


def test_score_band_boundaries_inclusive_lower():
    # Boundaries land in the upper band (>= ceiling of the band below).
    assert rollup.score_band(40)["key"] == "b2"
    assert rollup.score_band(55)["key"] == "b3"
    assert rollup.score_band(70)["key"] == "b4"
    assert rollup.score_band(85)["key"] == "b5"
    assert rollup.score_band(39)["key"] == "b1"


def test_score_band_returns_color_and_label():
    band = rollup.score_band(95)
    assert band["label"] == "ON TRACK"
    assert band["color"].startswith("#")


def test_score_band_extremes():
    assert rollup.score_band(0)["key"] == "b1"
    assert rollup.score_band(100)["key"] == "b5"


# ── per_person: third Heat map group ──────────────────────────────

def test_per_person_skips_unassigned():
    todos = [_make(bc_id=1, assignees=[]), _make(bc_id=2, assignees=["Ali"])]
    people = rollup.per_person(todos)
    assert [p.name for p in people] == ["Ali"]


def test_per_person_counts_multi_assignee_for_each():
    todos = [_make(bc_id=1, assignees=["Ali", "Kes"])]
    people = {p.name: p for p in rollup.per_person(todos)}
    assert people["Ali"].open_count == 1
    assert people["Kes"].open_count == 1


def test_per_person_overdue_lowers_score_and_sets_band():
    todos = [
        _make(bc_id=i, due_on=_offset(-3), urgency=75,
              category="human_required", assignees=["Ali"])
        for i in range(4)
    ]
    people = rollup.per_person(todos)
    ali = people[0]
    assert ali.overdue_count == 4
    assert ali.human_count == 4
    # 4 overdue + 4 red heavily penalize: 100 - 48 - 24 - 4 = 24 -> worst band.
    assert ali.score < 40
    assert rollup.score_band(ali.score)["key"] == "b1"


def test_per_person_sorted_worst_first():
    todos = [
        _make(bc_id=1, assignees=["Healthy"]),  # 1 open, score 99
        _make(bc_id=2, due_on=_offset(-5), urgency=80,
              category="human_required", assignees=["AtRisk"]),
    ]
    people = rollup.per_person(todos)
    assert people[0].name == "AtRisk"  # lower score sorts first


def test_per_person_next_blocking_prefers_human():
    todos = [
        _make(bc_id=1, urgency=90, category="unscored", assignees=["Ali"]),
        _make(bc_id=2, urgency=50, category="human_required", assignees=["Ali"]),
    ]
    ali = rollup.per_person(todos)[0]
    assert ali.next_blocking.bc_id == 2  # human picked over higher-urgency AI


def test_per_person_ignores_dismissed_and_completed():
    t_dismissed = _make(bc_id=1, assignees=["Ali"])
    t_dismissed.is_dismissed = True
    t_done = _make(bc_id=2, assignees=["Ali"])
    t_done.status = "completed"
    assert rollup.per_person([t_dismissed, t_done]) == []


# ── BC deep-link derivation ───────────────────────────────────────

def test_per_list_derives_bc_list_url():
    url = "https://3.basecamp.com/4567/buckets/123/todos/999"
    rollups = rollup.per_list([_make(list_id=42, app_url=url)])
    assert rollups[0].bc_list_url == "https://3.basecamp.com/4567/buckets/123/todolists/42"


def test_per_project_derives_bc_project_url():
    url = "https://3.basecamp.com/4567/buckets/123/todos/999"
    rollups = rollup.per_project([_make(app_url=url)])
    assert rollups[0].bc_project_url == "https://3.basecamp.com/4567/buckets/123"


def test_bc_url_helpers_handle_missing_or_malformed():
    assert rollup._bc_list_url("", 1) == ""
    assert rollup._bc_list_url("https://example.com/no-todos-here", 1) == ""
    assert rollup._bc_project_url("") == ""


def test_per_project_next_blocking_prefers_human():
    todos = [
        _make(bc_id=1, urgency=95, category="unscored"),
        _make(bc_id=2, urgency=40, category="human_required"),
    ]
    proj = rollup.per_project(todos)[0]
    assert proj.next_blocking.bc_id == 2
