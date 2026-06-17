"""The HTML renderer is email-safe and surfaces every pillar."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from execution.products.ops.productivity.aggregate import OperatorInput, build_scorecard
from execution.products.ops.productivity.render import render_html

NOW = datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)
DAY = 86400


def _todo(bc_id, **kw):
    base = dict(bc_id=bc_id, status="active", completed_at="", cycle_seconds=0,
                bc_created_at="2026-05-01T00:00:00Z", bc_updated_at="2026-06-19T10:00:00Z",
                due_on=None, is_dismissed=False, category="unscored")
    base.update(kw)
    return SimpleNamespace(**base)


def _scorecard(low_conf_baseline=False):
    todos = [
        _todo(1, status="completed", completed_at="2026-06-18T10:00:00Z", cycle_seconds=2 * DAY),
        _todo(2, status="completed", completed_at="2026-06-19T10:00:00Z", cycle_seconds=4 * DAY),
        _todo(10, due="2026-06-30"),
    ]
    op = OperatorInput(user_id="alice@colaberry.com", display_name="Alice", todos=todos,
                       ai_touched_ids={1}, ai_action_count=2, human_action_count=2, syncs=5)
    baseline = {} if low_conf_baseline else {
        "alice@colaberry.com": {"median_cycle_days": 5.0, "weekly_throughput": 1.0}}
    return build_scorecard([op], baseline=baseline, now=NOW)


def test_html_is_well_formed_and_email_safe():
    html = render_html(_scorecard())
    assert html.startswith("<!doctype html>")
    assert "<script" not in html.lower()           # no scripts in email
    # House email contract: zero em-dashes (literal or entity).
    assert "—" not in html
    assert "&mdash;" not in html


def test_all_pillars_present():
    html = render_html(_scorecard())
    assert "TEAM ASSESSMENT" in html
    assert "Active operators" in html              # adoption
    assert "Completed (7d)" in html                # throughput
    assert "AI leverage" in html and "activity" in html  # both AI views
    assert "Median cycle" in html                  # speed
    assert "CYCLE vs BASE" in html
    assert "Alice" in html                         # per-operator row
    assert "alice@colaberry.com" in html


def test_assumptions_footer_is_transparent():
    html = render_html(_scorecard())
    assert "Assumptions" in html
    assert "saved per AI-touched task" in html
    assert "estimated" in html.lower()


def test_low_confidence_banner_shows_when_baseline_missing():
    html = render_html(_scorecard(low_conf_baseline=True))
    assert "low-confidence" in html
    assert "went live" in html
