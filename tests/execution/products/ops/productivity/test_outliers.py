"""Outlier-robust team math: median headline, winsorized trends, floored baselines,
volume tiers, and the BREAK cases (empty store, mega-outlier, all-unknown, div-by-zero,
idempotency)."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from execution.products.ops.productivity import aggregate
from execution.products.ops.productivity.aggregate import AiSignals, build_scorecard

NOW = datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)
DAY = 86400


def _todo(bc_id, **kw):
    base = dict(bc_id=bc_id, status="active", completed_at="", completed_by_name="",
                cycle_seconds=0, assignee_names=[], bc_created_at="2026-05-01T00:00:00Z",
                bc_updated_at="2026-06-19T10:00:00Z", due_on=None, is_dismissed=False,
                category="unscored", bc_project_name="Gov Contracts")
    base.update(kw)
    return SimpleNamespace(**base)


def _completion(bc_id, person, *, cycle_days=2):
    return _todo(bc_id, status="completed", completed_at="2026-06-18T10:00:00Z",
                 completed_by_name=person, assignee_names=[person], cycle_seconds=cycle_days * DAY)


def _person_block(start_id, person, n_complete, n_ai):
    """n_complete completions for `person`; the first n_ai of them carry an AI session join."""
    todos, session_ids = [], set()
    for i in range(n_complete):
        bid = start_id + i
        todos.append(_completion(bid, person))
        if i < n_ai:
            session_ids.add(bid)
    return todos, session_ids


# ── outlier robustness ──────────────────────────────────────────────


def _team_fixture(include_mega=True):
    todos, sessions = [], set()
    # Five "core" operators, each 4 completions with 2 AI -> point share 0.5.
    for k, name in enumerate(("Ann", "Ben", "Cy", "Di", "Ed")):
        block, sess = _person_block(1000 + k * 10, name, 4, 2)
        todos += block
        sessions |= sess
    if include_mega:
        # One mega-operator closes 247 completions, all AI -> point share 1.0.
        block, sess = _person_block(5000, "Mega", 247, 247)
        todos += block
        sessions |= sess
    return build_scorecard(todos, baseline={}, now=NOW,
                           ai_signals=AiSignals(session_ticket_ids=sessions))


def test_team_median_unmoved_by_single_mega_outlier():
    with_mega = _team_fixture(include_mega=True).team
    without = _team_fixture(include_mega=False).team
    # Median headline is pinned at the core operators' 0.5 with OR without the outlier.
    assert with_mega.ai_touched_share == 0.5
    assert without.ai_touched_share == 0.5
    # The old completion-weighted number, by contrast, is owned by the outlier.
    assert with_mega.ai_share_weighted > 0.9
    assert abs(without.ai_share_weighted - 0.5) < 0.01
    # So the headline does not swing with the biggest operator; the weighted one does.
    assert abs(with_mega.ai_touched_share - without.ai_touched_share) < 0.05
    assert (with_mega.ai_share_weighted - without.ai_share_weighted) > 0.4


def test_mega_operator_flagged_as_outlier_and_heavy():
    sc = _team_fixture(include_mega=True)
    mega = next(c for c in sc.operators if c.display_name == "Mega")
    core = next(c for c in sc.operators if c.display_name == "Ann")
    assert mega.is_outlier is True
    assert mega.volume_tier == "heavy"
    assert core.is_outlier is False
    assert core.volume_tier in ("core", "occasional")


def test_winsorize_caps_runaway_ratio():
    # 50 completions vs a (well-sampled) baseline of 0.5/week would be +9900%; capped.
    todos, sess = _person_block(1, "Zed", 50, 0)
    baseline = {"Zed": {"median_cycle_days": 2.0, "weekly_throughput": 0.5,
                        "sample_count": 6, "low_confidence": False}}
    sc = build_scorecard(todos, baseline=baseline, now=NOW)
    z = next(c for c in sc.operators if c.display_name == "Zed")
    assert z.throughput_vs_baseline_pct == aggregate.TREND_CAP_PCT  # capped, not +9900%


def test_tiny_baseline_renders_na_not_percentage():
    todos, _ = _person_block(1, "Sam", 5, 0)
    baseline = {"Sam": {"median_cycle_days": 3.0, "weekly_throughput": 1.0,
                        "sample_count": 1, "low_confidence": True}}
    sc = build_scorecard(todos, baseline=baseline, now=NOW)
    s = next(c for c in sc.operators if c.display_name == "Sam")
    assert s.baseline_too_small is True
    assert s.throughput_vs_baseline_pct is None
    assert s.cycle_vs_baseline_pct is None


def test_volume_tiers_bucket_relative_to_median():
    # Heavy (12), core (5), occasional (1) against a small team.
    todos, sess = [], set()
    for name, n in (("Heavy", 12), ("Core", 5), ("Occ", 1)):
        block, s = _person_block(hash(name) % 1000 * 100 + 1, name, n, n)
        todos += block
        sess |= s
    sc = build_scorecard(todos, baseline={}, now=NOW, ai_signals=AiSignals(session_ticket_ids=sess))
    tiers = {c.display_name: c.volume_tier for c in sc.operators}
    assert tiers["Heavy"] == "heavy"
    assert tiers["Occ"] == "occasional"


# ── cycle: dormant backlog split ────────────────────────────────────


def test_dormant_backlog_excluded_from_new_work_cycle():
    todos = [
        _completion(1, "Lee", cycle_days=2),
        _completion(2, "Lee", cycle_days=4),
        _completion(3, "Lee", cycle_days=120),   # long-dormant backlog cleanup
    ]
    sc = build_scorecard(todos, baseline={}, now=NOW)
    lee = next(c for c in sc.operators if c.display_name == "Lee")
    assert lee.median_cycle_days == 3.0          # median(2,4), the 120d backlog excluded
    assert lee.backlog_cycle_days == 120.0       # surfaced separately, not as "slower"


# ── BREAK cases ─────────────────────────────────────────────────────


def test_empty_store():
    sc = build_scorecard([], now=NOW)
    assert sc.operators == []
    assert sc.team.people == 0
    assert sc.team.verdict == "NODATA"


def test_operator_with_zero_completions_no_div_by_zero():
    # Only an open assigned task, never completed -> no completions, no crash.
    sc = build_scorecard([_todo(1, assignee_names=["Pat"])], now=NOW)
    pat = next(c for c in sc.operators if c.display_name == "Pat")
    assert pat.completed_7d == 0
    assert pat.ai_touched_share is None
    assert pat.attribution_confidence is None
    assert pat.verdict == "NODATA"


def test_zero_baseline_does_not_divide_by_zero():
    todos, _ = _person_block(1, "Ray", 3, 0)
    baseline = {"Ray": {"median_cycle_days": 0.0, "weekly_throughput": 0.0, "sample_count": 5}}
    sc = build_scorecard(todos, baseline=baseline, now=NOW)
    ray = next(c for c in sc.operators if c.display_name == "Ray")
    assert ray.throughput_vs_baseline_pct is None   # base 0 -> no ratio, no ZeroDivisionError


def test_all_unknown_is_unknown_verdict():
    todos, _ = _person_block(1, "Uma", 6, 0)         # 6 completions, no AI signal
    sc = build_scorecard(todos, baseline={}, now=NOW)
    uma = next(c for c in sc.operators if c.display_name == "Uma")
    assert uma.attribution_unknown_count == 6
    assert uma.attribution_confidence == 0.0
    assert uma.verdict == "UNKNOWN"


def test_idempotent_on_same_date():
    todos, sess = _person_block(1, "Ivy", 5, 3)
    sig = AiSignals(session_ticket_ids=sess)
    a = build_scorecard(todos, baseline={}, now=NOW, ai_signals=sig)
    b = build_scorecard(todos, baseline={}, now=NOW, ai_signals=sig)
    assert a == b                                    # same inputs + date -> identical output


def test_naive_now_is_coerced_to_utc():
    todos, sess = _person_block(1, "Nat", 3, 3)
    naive = datetime(2026, 6, 20, 12, 0)             # no tzinfo
    sc = build_scorecard(todos, baseline={}, now=naive, ai_signals=AiSignals(session_ticket_ids=sess))
    nat = next(c for c in sc.operators if c.display_name == "Nat")
    assert nat.completed_7d == 3                      # boundary handled, no crash
