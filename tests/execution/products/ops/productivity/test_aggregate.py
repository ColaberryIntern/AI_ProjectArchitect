"""KPI math: completed_by attribution, dedupe, and CB-System AI leverage."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from execution.products.ops.productivity import aggregate
from execution.products.ops.productivity.aggregate import build_scorecard

NOW = datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)
DAY = 86400


def _todo(bc_id, *, status="active", completed_at="", completed_by="", cycle_seconds=0,
          assignees=(), created="2026-05-01T00:00:00Z", updated="2026-06-19T10:00:00Z",
          due=None, dismissed=False, category="unscored"):
    return SimpleNamespace(
        bc_id=bc_id, status=status, completed_at=completed_at, completed_by_name=completed_by,
        cycle_seconds=cycle_seconds, assignee_names=list(assignees), bc_created_at=created,
        bc_updated_at=updated, due_on=due, is_dismissed=dismissed, category=category,
    )


def _todos():
    return [
        # Alice closed these herself
        _todo(1, status="completed", completed_at="2026-06-18T10:00:00Z", completed_by="Alice",
              cycle_seconds=2 * DAY, assignees=["Alice"]),
        _todo(2, status="completed", completed_at="2026-06-19T10:00:00Z", completed_by="Alice",
              cycle_seconds=4 * DAY, assignees=["Alice"]),
        _todo(3, status="completed", completed_at="2026-06-20T09:00:00Z", completed_by="Alice",
              cycle_seconds=6 * DAY, assignees=["Bob"]),          # Alice closed Bob's task
        _todo(4, status="completed", completed_at="2026-06-09T10:00:00Z", completed_by="Alice",
              cycle_seconds=3 * DAY, assignees=["Alice"]),        # prior 7d
        # AI (CB System) closed tasks assigned to Alice
        _todo(5, status="completed", completed_at="2026-06-17T10:00:00Z", completed_by="CB System",
              cycle_seconds=1 * DAY, assignees=["Alice"]),
        _todo(6, status="completed", completed_at="2026-06-18T10:00:00Z", completed_by="CB System",
              cycle_seconds=1 * DAY, assignees=["Alice"]),
        # Alice's open backlog
        _todo(10, assignees=["Alice"], due="2026-06-15", created="2026-06-16T00:00:00Z",
              updated="2026-06-19T00:00:00Z"),                    # overdue, created in-window
        _todo(11, assignees=["Alice"], due="2026-06-30", updated="2026-05-01T00:00:00Z",
              category="human_required"),                         # stale + human-required
        _todo(12, assignees=["Alice"], dismissed=True),           # dismissed -> not open
    ]


def _build():
    baseline = {"Alice": {"median_cycle_days": 5.0, "weekly_throughput": 1.5}}
    return build_scorecard(_todos(), baseline=baseline, now=NOW)


def _alice(sc):
    return next(c for c in sc.operators if c.display_name == "Alice")


def test_throughput_attributed_by_completed_by():
    a = _alice(_build())
    assert a.completed_today == 1          # task 3 at 06-20T09:00
    assert a.completed_7d == 3             # tasks 1,2,3 (she closed them), NOT the AI ones
    assert a.completed_prior_7d == 1       # task 4
    assert a.active_days_7d == 3


def test_ai_leverage_from_cb_system_completions():
    a = _alice(_build())
    # of Alice-assigned tasks completed this week (1,2,5,6) the AI closed 5,6
    assert a.assigned_completed_7d == 4
    assert a.ai_assisted_count == 2
    assert a.ai_touched_share == 0.5


def test_backlog_and_quality_from_assigned_open():
    a = _alice(_build())
    assert a.open_count == 2               # tasks 10,11 (12 dismissed)
    assert a.overdue_count == 1            # task 10
    assert a.stale_count == 1             # task 11
    assert a.overdue_rate == 0.5
    assert a.human_required_count == 1
    assert a.delegatable_count == 1
    assert a.net_flow_7d == 2              # 3 closed - 1 created in window


def test_speed_vs_baseline():
    a = _alice(_build())
    assert a.median_cycle_days == 4.0      # median(2,4,6) of her own completions
    assert a.cycle_vs_baseline_pct == -20.0
    assert a.throughput_vs_baseline_pct == 100.0


def test_savings_from_ai_completed_tasks():
    a = _alice(_build())
    assert a.est_hours_saved_7d == round(2 * aggregate.MINUTES_SAVED_PER_AI_TASK / 60, 1)
    assert a.est_dollars_saved_7d == a.est_hours_saved_7d * aggregate.DOLLARS_PER_HOUR


def test_verdict_coloured_by_ai_share_not_overdue():
    # Alice has 50% AI share -> GREEN (heavy AI use), even though half her backlog
    # is overdue. Colour tracks adoption, not ticket hygiene.
    a = _alice(_build())
    assert a.overdue_rate == 0.5
    assert a.verdict == "GREEN"
    assert "ai system" in a.verdict_reason.lower()


def test_spark_series_has_one_bucket_per_day():
    from execution.products.ops.productivity.aggregate import SPARK_DAYS
    a = _alice(_build())
    assert len(a.spark_completed) == SPARK_DAYS
    assert sum(a.spark_completed) == a.completed_7d + a.completed_prior_7d  # 3 + 1, all within 14d


def test_ai_actor_excluded_from_operator_list():
    names = {c.display_name for c in _build().operators}
    assert "CB System" not in names
    assert {"Alice", "Bob"} <= names


def test_team_ai_share_is_real():
    t = _build().team
    assert t.completed_7d == 5             # tasks 1,2,3,5,6 (4 is prior)
    assert t.ai_completions_7d == 2        # 5,6
    assert t.human_completions_7d == 3     # 1,2,3
    assert t.ai_touched_share == 0.4       # 2 of 5


def test_dedupe_collapses_shared_mirror_rows():
    # same task id appears twice (two mirrors) -> counted once
    dup = _todos() + [_todo(1, status="completed", completed_at="2026-06-18T10:00:00Z",
                            completed_by="Alice", cycle_seconds=2 * DAY, assignees=["Alice"])]
    sc = build_scorecard(dup, baseline={"Alice": {"median_cycle_days": 5.0, "weekly_throughput": 1.5}},
                         now=NOW)
    assert _alice(sc).completed_7d == 3    # not 4


def test_empty_is_low_confidence():
    sc = build_scorecard([], now=NOW)
    assert sc.operators == []
    assert sc.low_confidence is True
    assert sc.team.people == 0


def test_filter_scope_drops_excluded_projects():
    from execution.products.ops.productivity.aggregate import filter_scope

    def _p(bc_id, project):
        return SimpleNamespace(bc_id=bc_id, bc_project_name=project, status="active",
                               completed_at="", completed_by_name="", assignee_names=["X"],
                               cycle_seconds=0, bc_created_at="", bc_updated_at="", due_on=None,
                               is_dismissed=False, category="unscored")
    todos = [_p(1, "Gov Contracts"), _p(2, "Power BI - Center of Excellence"),
             _p(3, "RMG Mortgage Project"), _p(4, "Ali Personal")]
    kept = {t.bc_id for t in filter_scope(todos)}
    assert kept == {1, 4}                  # Gov Contracts + employee work kept


def test_build_scorecard_applies_exclude_projects():
    p = lambda i, proj: SimpleNamespace(
        bc_id=i, bc_project_name=proj, status="completed", completed_at="2026-06-18T10:00:00Z",
        completed_by_name="Pat", assignee_names=["Pat"], cycle_seconds=DAY,
        bc_created_at="2026-05-01T00:00:00Z", bc_updated_at="2026-06-18T10:00:00Z",
        due_on=None, is_dismissed=False, category="unscored")
    sc = build_scorecard([p(1, "Power BI - Center of Excellence"), p(2, "Gov Contracts")],
                         now=NOW, exclude_projects=["power bi", "center of excellence", "rmg"])
    assert sc.team.completed_7d == 1       # only the Gov Contracts completion survives
