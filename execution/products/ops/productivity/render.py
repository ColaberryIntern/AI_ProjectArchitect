"""Email-safe HTML for the productivity report.

Inline CSS only (no external assets, no <script>) so it survives email clients.
Zero em-dashes per the house email contract. Structure:

  - Verdict banner (team GREEN / AMBER / RED / BASELINE + one-line assessment)
  - Team tiles: active operators, completions (AI vs people), AI leverage, cycle
  - Per-operator table: throughput, backlog, AI share of their workload, speed, verdict
  - Assessment notes + transparent assumptions footer
"""
from __future__ import annotations

from .aggregate import ProductivityScorecard, OperatorScorecard, TeamRollup

_VERDICT_COLOR = {"GREEN": "#1a7f37", "AMBER": "#8a5a00", "RED": "#cf222e", "BASELINE": "#57606a"}
_VERDICT_LABEL = {
    "GREEN": "More productive", "AMBER": "Faster, not yet more",
    "RED": "Quality at risk", "BASELINE": "Baseline building",
}


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{round(v * 100)}%"


def _delta(v: float | None) -> str:
    if v is None:
        return "n/a"
    if v > 0:
        return f"&#9650; +{v}%"
    if v < 0:
        return f"&#9660; {v}%"
    return "flat"


def _num(v: float | None, suffix: str = "") -> str:
    return "n/a" if v is None else f"{v}{suffix}"


def _pill(verdict: str) -> str:
    color = _VERDICT_COLOR.get(verdict, "#57606a")
    label = _VERDICT_LABEL.get(verdict, verdict)
    return (f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:999px;'
            f'font-size:11px;font-weight:600;white-space:nowrap;">{label}</span>')


def _tile(label: str, value: str, sub: str = "") -> str:
    sub_html = f'<div style="font-size:11px;color:#57606a;margin-top:2px;">{sub}</div>' if sub else ""
    return (
        '<td style="border:1px solid #d0d7de;border-radius:8px;padding:12px 14px;'
        'vertical-align:top;width:25%;">'
        f'<div style="font-size:11px;letter-spacing:0.5px;color:#57606a;text-transform:uppercase;">{label}</div>'
        f'<div style="font-size:22px;font-weight:700;margin-top:4px;">{value}</div>'
        f'{sub_html}</td>'
    )


def _operator_row(c: OperatorScorecard) -> str:
    ai_sub = (f'{c.ai_assisted_count}/{c.assigned_completed_7d} by AI'
              if c.assigned_completed_7d else "no completions")
    return (
        "<tr>"
        f'<td><strong>{c.display_name}</strong></td>'
        f'<td style="text-align:center;">{c.completed_today}</td>'
        f'<td style="text-align:center;">{c.completed_7d}'
        f'<div style="font-size:11px;color:#57606a;">prior {c.completed_prior_7d}</div></td>'
        f'<td style="text-align:center;">{c.open_count}'
        f'<div style="font-size:11px;color:#57606a;">{c.overdue_count} overdue</div></td>'
        f'<td style="text-align:center;">{_pct(c.ai_touched_share)}'
        f'<div style="font-size:11px;color:#57606a;">{ai_sub}</div></td>'
        f'<td style="text-align:center;">{_num(c.median_cycle_days, "d")}'
        f'<div style="font-size:11px;color:#57606a;">{_delta(c.cycle_vs_baseline_pct)}</div></td>'
        f'<td style="text-align:center;">${c.est_dollars_saved_7d:,.0f}</td>'
        f'<td>{_pill(c.verdict)}<div style="font-size:11px;color:#57606a;margin-top:4px;">{c.verdict_reason}</div></td>'
        "</tr>"
    )


def render_html(sc: ProductivityScorecard) -> str:
    t: TeamRollup = sc.team
    date_str = sc.generated_at[:10]
    banner_color = _VERDICT_COLOR.get(t.verdict, "#57606a")
    banner = (
        f'<div style="background:{banner_color};color:#fff;padding:14px 18px;border-radius:8px;'
        f'margin-bottom:18px;">'
        f'<div style="font-size:12px;letter-spacing:0.6px;opacity:0.85;">TEAM ASSESSMENT</div>'
        f'<div style="font-size:18px;font-weight:700;margin:2px 0 4px;">{_VERDICT_LABEL.get(t.verdict, t.verdict)}</div>'
        f'<div style="font-size:13px;">{t.verdict_reason}</div></div>'
    )

    low_conf = ""
    if sc.low_confidence:
        low_conf = (
            '<div style="background:#fff8c5;border:1px solid #d4a72c;color:#54470f;padding:10px 14px;'
            'border-radius:8px;margin-bottom:18px;font-size:13px;">'
            f'New system went live {sc.launch_date}. Per-person trend calls are low-confidence until '
            'enough post-launch completions accrue (typically 2 to 3 weeks). The counts and AI-leverage '
            'numbers below are real now; the verdicts sharpen as the after-window fills.</div>'
        )

    tiles = (
        "<table style='border-collapse:separate;border-spacing:8px;width:100%;margin-bottom:8px;'><tr>"
        + _tile("Active operators", f"{t.active_operators_7d}/{t.people}", "people who closed work, 7d")
        + _tile("Completed (7d)", str(t.completed_7d),
                f"{t.ai_completions_7d} by AI, {t.human_completions_7d} by people")
        + _tile("AI leverage", _pct(t.ai_touched_share), "AI share of all completions")
        + _tile("Median cycle", _num(t.median_cycle_days, "d"), "created to done")
        + "</tr></table>"
    )

    rows = "\n".join(_operator_row(c) for c in sc.operators) or (
        '<tr><td colspan="8" style="text-align:center;color:#57606a;padding:20px;">'
        "No operators with activity found.</td></tr>"
    )

    a = sc.assumptions
    footer = (
        '<div style="color:#57606a;font-size:12px;margin-top:22px;line-height:1.6;">'
        "<strong>How to read this.</strong> "
        "<em>Done 7d</em> is what each person personally closed (completions are attributed to "
        "whoever actually completed the task, deduplicated across shared projects). "
        "<em>AI share</em> is, of the tasks assigned to that person and completed this week, the "
        f"portion closed by the AI ({', '.join(a.get('ai_actors', []))}). "
        "<em>Cycle vs base</em> compares median created-to-done time against each person's "
        "pre-launch baseline; a down arrow means faster. The verdict: GREEN = more output without "
        "slowing down; AMBER = faster per task but not yet doing more; RED = speed may be costing "
        "quality.<br><br>"
        f"<strong>Assumptions.</strong> AI-completed work is counted from tasks closed by "
        f"{', '.join(a.get('ai_actors', []))}. Estimated savings use {a.get('minutes_saved_per_ai_task')} "
        f"min per AI-completed task at ${a.get('dollars_per_hour'):,.0f}/hr. A verdict needs at least "
        f"{a.get('min_sample_for_verdict')} completions and a +/-{a.get('trend_band_pct')}% move to call a trend."
        "</div>"
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Productivity and AI leverage report - {date_str}</title>
</head>
<body style="font-family:-apple-system,system-ui,Arial,sans-serif;max-width:880px;margin:24px auto;padding:0 20px;color:#1f2328;">
<h1 style="margin-bottom:2px;font-size:22px;">Productivity &amp; AI leverage</h1>
<div style="color:#57606a;font-size:13px;margin-bottom:18px;">
  Daily report &middot; generated {sc.generated_at} &middot; window: last {sc.window_days} days &middot;
  system live since {sc.launch_date}
</div>
{banner}
{low_conf}
{tiles}
<table style="border-collapse:collapse;width:100%;font-size:13px;">
  <thead>
    <tr style="background:#f6f8fa;">
      <th style="text-align:left;padding:8px;border-bottom:2px solid #d0d7de;font-size:11px;letter-spacing:0.5px;">OPERATOR</th>
      <th style="padding:8px;border-bottom:2px solid #d0d7de;font-size:11px;">TODAY</th>
      <th style="padding:8px;border-bottom:2px solid #d0d7de;font-size:11px;">DONE 7D</th>
      <th style="padding:8px;border-bottom:2px solid #d0d7de;font-size:11px;">OPEN</th>
      <th style="padding:8px;border-bottom:2px solid #d0d7de;font-size:11px;">AI SHARE</th>
      <th style="padding:8px;border-bottom:2px solid #d0d7de;font-size:11px;">CYCLE vs BASE</th>
      <th style="padding:8px;border-bottom:2px solid #d0d7de;font-size:11px;">EST $ SAVED</th>
      <th style="text-align:left;padding:8px;border-bottom:2px solid #d0d7de;font-size:11px;">ASSESSMENT</th>
    </tr>
  </thead>
  <tbody>
{rows}
  </tbody>
</table>
{footer}
</body>
</html>
"""
