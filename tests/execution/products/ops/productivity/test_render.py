"""The HTML renderer is email-safe and surfaces every pillar."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from execution.products.ops.productivity import render as render_mod
from execution.products.ops.productivity.aggregate import build_scorecard
from execution.products.ops.productivity.render import render_html

NOW = datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)
DAY = 86400


def _todo(bc_id, **kw):
    base = dict(bc_id=bc_id, status="active", completed_at="", completed_by_name="",
                cycle_seconds=0, assignee_names=[], bc_created_at="2026-05-01T00:00:00Z",
                bc_updated_at="2026-06-19T10:00:00Z", due_on=None, is_dismissed=False,
                category="unscored")
    base.update(kw)
    return SimpleNamespace(**base)


def _scorecard(low_conf_baseline=False):
    todos = [
        _todo(1, status="completed", completed_at="2026-06-18T10:00:00Z",
              completed_by_name="Alice", cycle_seconds=2 * DAY, assignee_names=["Alice"]),
        _todo(2, status="completed", completed_at="2026-06-19T10:00:00Z",
              completed_by_name="CB System", cycle_seconds=1 * DAY, assignee_names=["Alice"]),
        _todo(10, assignee_names=["Alice"], due_on="2026-06-30"),
    ]
    baseline = {} if low_conf_baseline else {"Alice": {"median_cycle_days": 5.0, "weekly_throughput": 1.0}}
    return build_scorecard(todos, baseline=baseline, now=NOW)


def test_html_is_well_formed_and_email_safe():
    html = render_html(_scorecard())
    assert html.startswith("<!doctype html>")
    assert "<script" not in html.lower()
    assert "—" not in html              # no literal em-dash
    assert "&mdash;" not in html        # no em-dash entity


def test_all_pillars_present():
    html = render_html(_scorecard())
    assert "TEAM ASSESSMENT" in html
    assert "Active operators" in html
    assert "Completed (7d)" in html
    assert "AI leverage" in html
    assert "AI share of all completions" in html
    assert "Median cycle" in html
    assert "CYCLE vs BASE" in html
    assert "Alice" in html
    assert "by AI" in html              # per-operator AI sub-line


def test_assumptions_footer_names_ai_actor():
    html = render_html(_scorecard())
    assert "Assumptions" in html
    assert "CB System" in html
    assert "AI-completed task" in html


def test_low_confidence_banner_when_baseline_missing():
    html = render_html(_scorecard(low_conf_baseline=True))
    assert "low-confidence" in html
    assert "went live" in html


def test_operator_table_is_capped_with_overflow_note(monkeypatch):
    # three people each closing one task; cap to 1 row -> note about the rest
    todos = []
    for i, name in enumerate(("Ann", "Ben", "Cy"), 1):
        todos.append(_todo(i, status="completed", completed_at="2026-06-18T10:00:00Z",
                           completed_by_name=name, cycle_seconds=DAY, assignee_names=[name]))
    sc = build_scorecard(todos, baseline={}, now=NOW)
    monkeypatch.setattr(render_mod, "MAX_OPERATOR_ROWS", 1)
    html = render_html(sc)
    assert "more active operators not shown" in html
