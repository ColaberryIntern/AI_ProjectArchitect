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


def _sig(**kw):
    return aggregate.AiSignals(
        ai_actor_names=set(kw.get("ai_actor_names", {"CB System"})),
        session_ticket_ids=set(kw.get("session_ticket_ids", set())),
        ai_marked_task_ids=set(kw.get("ai_marked_task_ids", set())),
        human_marked_task_ids=set(kw.get("human_marked_task_ids", set())),
        ai_active_operators=set(kw.get("ai_active_operators", set())),
    )


def _build(ai_signals=None):
    baseline = {"Alice": {"median_cycle_days": 5.0, "weekly_throughput": 1.5}}
    return build_scorecard(_todos(), baseline=baseline, now=NOW, ai_signals=ai_signals)


def _alice(sc):
    return next(c for c in sc.operators if c.display_name == "Alice")


def test_throughput_attributed_by_completed_by():
    a = _alice(_build())
    assert a.completed_today == 1          # task 3 at 06-20T09:00
    assert a.completed_7d == 3             # tasks 1,2,3 (she closed them), NOT the AI ones
    assert a.completed_prior_7d == 1       # task 4
    assert a.active_days_7d == 3


def test_unattributed_human_work_is_unknown_not_zero_ai():
    # With only the default actor-close signal, Alice's three self-closed completions
    # (1,2,3) carry no AI marker -> they are attribution_unknown, NOT a measured 0% AI.
    # This is the core bug: a measurement gap must not read as "does not use AI".
    a = _alice(_build())
    assert a.ai_assisted_count == 0
    assert a.human_only_count == 0
    assert a.attribution_unknown_count == 3
    assert a.attribution_confidence == 0.0     # nothing definitively attributed
    assert a.ai_touched_share == 0.0           # point share, shown with the unknown slice
    assert a.delegated_ai_count == 2           # tasks 5,6 the bot closed for her
    assert a.verdict == "UNKNOWN"              # never "RED / Low AI use"


def test_each_ai_signal_independently_flips_a_completion():
    # Session join on task 1, per-task marker on task 2, leave task 3 unknown.
    sc = _build(ai_signals=_sig(session_ticket_ids={1}, ai_marked_task_ids={2}))
    a = _alice(sc)
    assert a.ai_assisted_count == 2            # tasks 1 (session) + 2 (marker)
    assert a.attribution_unknown_count == 1    # task 3
    assert a.ai_signal_tally.get("session") == 1
    assert a.ai_signal_tally.get("marker") == 1
    assert a.attribution_confidence == round(2 / 3, 3)
    assert a.ai_touched_share == round(2 / 3, 3)
    assert a.ai_share_attributable == 1.0      # of attributable work, all AI


def test_full_ai_attribution_makes_top_tier():
    # All three of Alice's completions join a Claude Code session -> high, confident AI share.
    a = _alice(_build(ai_signals=_sig(session_ticket_ids={1, 2, 3})))
    assert a.ai_assisted_count == 3
    assert a.attribution_confidence == 1.0
    assert a.ai_touched_share == 1.0
    assert a.verdict == "GREEN"


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
    # With her completions attributed to AI sessions, Alice is GREEN (heavy AI use) even
    # though half her backlog is overdue. Colour tracks adoption, not ticket hygiene.
    a = _alice(_build(ai_signals=_sig(session_ticket_ids={1, 2, 3})))
    assert a.overdue_rate == 0.5
    assert a.verdict == "GREEN"
    assert "ai system" in a.verdict_reason.lower()


def test_comment_authorship_drives_ai_share():
    # AI Share = AI comments / all comments. Alice posts 9 AI + 1 human comment -> 90%,
    # which overrides the completion-based attribution and reads GREEN with full confidence.
    sig = _sig()
    sig.comment_counts = {"Alice": {"ai": 9, "human": 1}}
    a = _alice(_build(ai_signals=sig))
    assert a.ai_share_source == "comments"
    assert a.comment_ai_count == 9 and a.comment_human_count == 1
    assert a.ai_touched_share == 0.9
    assert a.attribution_confidence == 1.0
    assert a.verdict == "GREEN"
    assert "comments" in a.verdict_reason.lower()


def test_comment_only_operator_is_scored():
    # Someone who only comments (no completions/assignments) still gets an AI Share.
    sig = _sig()
    sig.comment_counts = {"Zoe": {"ai": 4, "human": 0}}
    sc = build_scorecard(_todos(), baseline={}, now=NOW, ai_signals=sig)
    zoe = next(c for c in sc.operators if c.display_name == "Zoe")
    assert zoe.completed_7d == 0
    assert zoe.ai_touched_share == 1.0
    assert zoe.ai_share_source == "comments"
    assert zoe.verdict == "GREEN"


def test_ai_active_operator_unknown_work_is_not_called_low_use():
    # An operator flagged AI-active (commits/sessions in the window) whose tasks carry no
    # per-task marker stays "attribution incomplete", never "Low AI use".
    a = _alice(_build(ai_signals=_sig(ai_active_operators={"Alice"})))
    assert a.ai_active is True
    assert a.verdict == "UNKNOWN"
    assert "low" not in a.verdict_reason.lower() or "not a low-use" in a.verdict_reason.lower()


def test_spark_series_has_one_bucket_per_day():
    from execution.products.ops.productivity.aggregate import SPARK_DAYS
    a = _alice(_build())
    assert len(a.spark_completed) == SPARK_DAYS
    assert sum(a.spark_completed) == a.completed_7d + a.completed_prior_7d  # 3 + 1, all within 14d


def test_ai_actor_excluded_from_operator_list():
    names = {c.display_name for c in _build().operators}
    assert "CB System" not in names
    assert {"Alice", "Bob"} <= names


def test_team_buckets_and_completion_fallback_headline():
    t = _build().team
    assert t.completed_7d == 5             # tasks 1,2,3,5,6 (4 is prior)
    assert t.ai_completions_7d == 2        # 5,6 closed by CB System (actor signal)
    assert t.human_completions_7d == 0     # no positive human marker present
    assert t.unknown_completions_7d == 3   # 1,2,3 unattributed
    assert t.ai_share_weighted == 0.4      # completion-weighted number, kept as secondary
    assert t.attribution_confidence == 0.4  # (2 ai + 0 human) / 5
    # No comment data -> completion fallback. Alice's own completions are all unknown
    # (unreliable), so she is excluded from the median; the headline falls back to the team
    # attributable share (the only attributed completions were AI) = 1.0, but the verdict is
    # gated UNKNOWN because confidence 0.4 < the 0.5 floor.
    assert t.ai_touched_share == 1.0
    assert t.verdict == "UNKNOWN"


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
