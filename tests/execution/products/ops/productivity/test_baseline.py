"""Pre-launch baseline computation — the 'before' half of before/after."""
from __future__ import annotations

from types import SimpleNamespace

from execution.products.ops.productivity.baseline import compute_baseline

DAY = 86400


def _done(completed_at, cycle_days):
    return SimpleNamespace(status="completed", completed_at=completed_at,
                           cycle_seconds=cycle_days * DAY)


def test_pre_launch_split_and_rate():
    todos = [
        _done("2026-06-01T10:00:00Z", 5),   # pre-launch
        _done("2026-06-05T10:00:00Z", 7),   # pre-launch
        _done("2026-05-20T10:00:00Z", 3),   # pre-launch (earliest)
        _done("2026-06-18T10:00:00Z", 9),   # POST-launch -> ignored
        _done("2026-01-01T10:00:00Z", 4),   # >8 weeks before launch -> ignored
    ]
    b = compute_baseline(todos)
    assert b["sample_count"] == 3
    assert b["median_cycle_days"] == 5.0      # median(5, 7, 3)
    # earliest 2026-05-20T10:00 -> 24.x days before midnight launch -> 3 weeks; 3 / 3 = 1.0
    assert b["window_weeks"] == 3
    assert b["weekly_throughput"] == 1.0
    assert b["low_confidence"] is False


def test_empty_is_low_confidence():
    b = compute_baseline([])
    assert b["sample_count"] == 0
    assert b["median_cycle_days"] is None
    assert b["weekly_throughput"] is None
    assert b["low_confidence"] is True


def test_thin_sample_flagged_low_confidence():
    b = compute_baseline([_done("2026-06-05T10:00:00Z", 4)])
    assert b["sample_count"] == 1
    assert b["low_confidence"] is True
