"""The HTML renderer is email-safe and surfaces the AI-adoption story visually."""
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
                category="unscored", bc_project_name="Gov Contracts")
    base.update(kw)
    return SimpleNamespace(**base)


def _scorecard(low_conf_baseline=False):
    todos = [
        _todo(1, status="completed", completed_at="2026-06-18T10:00:00Z",
              completed_by_name="Alice", cycle_seconds=2 * DAY, assignee_names=["Alice"]),
        _todo(2, status="completed", completed_at="2026-06-19T10:00:00Z",
              completed_by_name="CB System", cycle_seconds=1 * DAY, assignee_names=["Alice"]),
        _todo(10, assignee_names=["Alice"], due_on="2026-06-15"),  # overdue
    ]
    baseline = {} if low_conf_baseline else {"Alice": {"median_cycle_days": 5.0, "weekly_throughput": 1.0}}
    return build_scorecard(todos, baseline=baseline, now=NOW)


def test_html_is_well_formed_and_email_safe():
    html = render_html(_scorecard())
    assert html.startswith("<!doctype html>")
    assert "<script" not in html.lower()
    assert "<svg" not in html.lower()       # Gmail strips SVG; we use table-bar sparklines
    assert "—" not in html                  # no literal em-dash
    assert "&mdash;" not in html


def test_ai_adoption_story_is_visible():
    html = render_html(_scorecard())
    assert "TEAM ASSESSMENT" in html
    assert "coloured by AI adoption" in html
    assert "Team AI leverage" in html
    assert "People using AI" in html
    assert "AI SHARE" in html
    assert "vs BEFORE" in html
    assert "Alice" in html


def test_conditional_formatting_present():
    html = render_html(_scorecard())
    # overdue badge colour, AI-share band colour, and a sparkline table all render
    assert "overdue" in html
    assert "#caf0d4" in html or "#ffeeba" in html or "#ffd6d6" in html   # an AI band bg
    assert 'role="presentation"' in html                                 # sparkline/bar tables


def test_scope_note_and_ai_actor_in_footer():
    html = render_html(_scorecard())
    assert "Employees + Gov Contracts only" in html
    assert "CB System" in html


def test_low_confidence_banner_when_baseline_missing():
    html = render_html(_scorecard(low_conf_baseline=True))
    assert "low-confidence" in html
    assert "went live" in html


def test_unknown_slice_and_attribution_incomplete_shown():
    # Alice's self-closed task carries no AI signal -> the row shows the unknown slice and
    # the verdict reads "Attribution incomplete", never "Low AI use".
    html = render_html(_scorecard())
    assert "unknown" in html.lower()
    assert "Attribution incomplete" in html
    assert "Low AI use" not in html


def test_high_ai_operator_renders_heavy_use():
    from execution.products.ops.productivity.aggregate import AiSignals
    todos = [
        _todo(1, status="completed", completed_at="2026-06-18T10:00:00Z",
              completed_by_name="Alice", cycle_seconds=2 * DAY, assignee_names=["Alice"]),
        _todo(2, status="completed", completed_at="2026-06-19T10:00:00Z",
              completed_by_name="Alice", cycle_seconds=1 * DAY, assignee_names=["Alice"]),
    ]
    sc = build_scorecard(todos, baseline={}, now=NOW,
                         ai_signals=AiSignals(session_ticket_ids={1, 2}))
    html = render_html(sc)
    assert "Heavy AI use" in html
    assert "—" not in html and "&mdash;" not in html


def test_team_headline_is_median_labelled():
    html = render_html(_scorecard())
    assert "median" in html.lower()          # headline framed as a median, not a raw avg


def test_operator_table_capped_with_overflow_note(monkeypatch):
    todos = [_todo(i, status="completed", completed_at="2026-06-18T10:00:00Z",
                   completed_by_name=n, cycle_seconds=DAY, assignee_names=[n])
             for i, n in enumerate(("Ann", "Ben", "Cy"), 1)]
    sc = build_scorecard(todos, baseline={}, now=NOW)
    monkeypatch.setattr(render_mod, "MAX_OPERATOR_ROWS", 1)
    assert "more active operators not shown" in render_html(sc)
