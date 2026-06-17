"""Per-operator KPI math for the productivity report.

Drives aggregate.build_scorecard with SimpleNamespace todos (duck-typed exactly
like the runtime OpsTodo) and a fixed clock, then asserts every pillar.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from execution.products.ops.productivity import aggregate
from execution.products.ops.productivity.aggregate import OperatorInput, build_scorecard

NOW = datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)
DAY = 86400


def _todo(bc_id, *, status="active", completed_at="", cycle_seconds=0,
          created="2026-05-01T00:00:00Z", updated="2026-06-19T10:00:00Z",
          due=None, dismissed=False, category="unscored"):
    return SimpleNamespace(
        bc_id=bc_id, status=status, completed_at=completed_at,
        cycle_seconds=cycle_seconds, bc_created_at=created, bc_updated_at=updated,
        due_on=due, is_dismissed=dismissed, category=category,
    )


def _alice() -> OperatorInput:
    todos = [
        # completed in the last 7 days (since 2026-06-13)
        _todo(1, status="completed", completed_at="2026-06-18T10:00:00Z", cycle_seconds=2 * DAY),
        _todo(2, status="completed", completed_at="2026-06-19T10:00:00Z", cycle_seconds=4 * DAY),
        _todo(3, status="completed", completed_at="2026-06-20T09:00:00Z", cycle_seconds=6 * DAY),
        # completed in the prior 7 days (2026-06-06 .. 06-13)
        _todo(4, status="completed", completed_at="2026-06-09T10:00:00Z", cycle_seconds=3 * DAY),
        _todo(5, status="completed", completed_at="2026-06-10T10:00:00Z", cycle_seconds=3 * DAY),
        # open
        _todo(10, due="2026-06-15", created="2026-06-16T00:00:00Z", updated="2026-06-19T00:00:00Z"),  # overdue, created in-window
        _todo(11, due="2026-06-30", updated="2026-05-01T00:00:00Z", category="human_required"),        # stale + human-required
        _todo(12, dismissed=True),  # dismissed -> excluded from open
    ]
    return OperatorInput(
        user_id="alice@colaberry.com", display_name="Alice", todos=todos,
        ai_touched_ids={1}, ai_action_count=3, human_action_count=1, syncs=12,
    )


def _build():
    baseline = {"alice@colaberry.com": {"median_cycle_days": 5.0, "weekly_throughput": 1.5}}
    return build_scorecard([_alice()], baseline=baseline, now=NOW)


def test_adoption_pillar():
    c = _build().operators[0]
    assert c.syncs == 12
    assert c.active_days_7d == 3          # 06-18, 06-19, 06-20


def test_throughput_pillar():
    c = _build().operators[0]
    assert c.completed_today == 1         # 06-20T09:00
    assert c.completed_7d == 3
    assert c.completed_prior_7d == 2
    assert c.open_count == 2              # 10, 11 (12 dismissed)
    assert c.overdue_count == 1           # todo 10
    assert c.stale_count == 1             # todo 11
    assert c.net_flow_7d == 2             # 3 completed - 1 created in window


def test_ai_leverage_pillar_both_views():
    c = _build().operators[0]
    assert c.ai_touched_share == round(1 / 3, 3)   # outcome: 1 of 3 completions
    assert c.ai_action_share == 0.75               # activity: 3 of 4 events
    assert c.human_required_count == 1
    assert c.delegatable_count == 1


def test_speed_pillar_vs_baseline_and_cohort_split():
    c = _build().operators[0]
    assert c.median_cycle_days == 4.0              # median(2,4,6)
    assert c.median_cycle_ai_days == 2.0           # only todo 1
    assert c.median_cycle_human_days == 5.0        # median(4,6)
    assert c.cycle_vs_baseline_pct == -20.0        # 4 vs 5 -> faster
    assert c.throughput_vs_baseline_pct == 100.0   # 3 vs 1.5


def test_quality_pillar_rates():
    c = _build().operators[0]
    assert c.overdue_rate == 0.5
    assert c.stale_rate == 0.5


def test_savings_estimate_uses_configured_assumption():
    c = _build().operators[0]
    # 1 AI-touched completion * 15 min / 60 = 0.25h; * $60 = $15
    assert c.est_hours_saved_7d == round(1 * aggregate.MINUTES_SAVED_PER_AI_TASK / 60, 1)
    assert c.est_dollars_saved_7d == c.est_hours_saved_7d * aggregate.DOLLARS_PER_HOUR


def test_high_overdue_share_forces_red_verdict():
    # overdue_rate 0.5 > threshold -> speed is read as costing quality
    c = _build().operators[0]
    assert c.verdict == "RED"
    assert "overdue" in c.verdict_reason.lower()


def test_empty_input_is_low_confidence():
    sc = build_scorecard([], baseline={}, now=NOW)
    assert sc.operators == []
    assert sc.low_confidence is True
    assert sc.team.operators == 0


def test_team_rollup_aggregates_operators():
    sc = _build()
    assert sc.team.operators == 1
    assert sc.team.completed_7d == 3
    assert sc.team.open_count == 2
    assert sc.team.est_dollars_saved_7d == sc.operators[0].est_dollars_saved_7d
