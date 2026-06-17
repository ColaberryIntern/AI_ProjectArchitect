"""Email-safe HTML for the productivity report — visual edition.

Constraints: inline styles only (no <style>/<script>/SVG — Gmail strips them).
Sparklines and bars are built from table cells with inline backgrounds, which every
major client renders. Zero em-dashes per the house email contract.

The report's question: WHO is using the new AI-paired system, and is that usage
producing more than before. So colour is driven by AI Share (adoption), not ticket
hygiene. Conditional formatting: AI-share cells graded green/amber/red, overdue
highlighted, faster-vs-before in green and slower in red, per-person completion
sparklines.
"""
from __future__ import annotations

import os

from .aggregate import (AI_HIGH_THRESHOLD, AI_LOW_THRESHOLD, ProductivityScorecard,
                        OperatorScorecard, TeamRollup)

MAX_OPERATOR_ROWS = int(os.environ.get("PRODUCTIVITY_MAX_ROWS", "30"))

_VERDICT_COLOR = {"GREEN": "#1a7f37", "AMBER": "#b08800", "RED": "#cf222e", "NODATA": "#8b949e"}
_VERDICT_LABEL = {"GREEN": "Heavy AI use", "AMBER": "Partial AI use",
                  "RED": "Low AI use", "NODATA": "No data"}


# ── small visual primitives ─────────────────────────────────────────


def _sparkline(values: list, color: str = "#2b6cb0", height: int = 26, bar_w: int = 4) -> str:
    if not values:
        return ""
    mx = max(values) or 1
    cells = ""
    for v in values:
        bh = max(2, round(v / mx * height)) if v else 2
        bg = color if v else "#e7ebef"
        cells += (f'<td style="vertical-align:bottom;padding:0 1px;mso-line-height-rule:exactly;">'
                  f'<div style="width:{bar_w}px;height:{bh}px;background:{bg};border-radius:1px;line-height:1px;font-size:1px;">&nbsp;</div></td>')
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" '
            f'style="border-collapse:collapse;height:{height}px;"><tr>{cells}</tr></table>')


def _ai_band(share: float | None) -> tuple[str, str, str]:
    """(background, text-color, verdict-key) for an AI-share value."""
    if share is None:
        return ("#f1f3f5", "#8b949e", "NODATA")
    if share >= AI_HIGH_THRESHOLD:
        return ("#caf0d4", "#0f5132", "GREEN")
    if share >= AI_LOW_THRESHOLD:
        return ("#ffeeba", "#7a5c00", "AMBER")
    return ("#ffd6d6", "#86131f", "RED")


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{round(v * 100)}%"


def _pill(verdict: str) -> str:
    color = _VERDICT_COLOR.get(verdict, "#8b949e")
    label = _VERDICT_LABEL.get(verdict, verdict)
    return (f'<span style="background:{color};color:#fff;padding:3px 9px;border-radius:999px;'
            f'font-size:11px;font-weight:700;white-space:nowrap;">{label}</span>')


def _trend_before(v: float | None) -> str:
    """Throughput vs before: up is good (green), down is bad (red)."""
    if v is None:
        return '<span style="color:#8b949e;">new</span>'
    if v > 0:
        return f'<span style="color:#1a7f37;font-weight:700;">&#9650; +{round(v)}%</span>'
    if v < 0:
        return f'<span style="color:#cf222e;font-weight:700;">&#9660; {round(v)}%</span>'
    return '<span style="color:#57606a;">flat</span>'


def _cycle_cell(days: float | None, vs_base: float | None) -> str:
    if days is None:
        return '<span style="color:#8b949e;">n/a</span>'
    if vs_base is None:
        sub = '<span style="color:#8b949e;">new</span>'
    elif vs_base < 0:
        sub = f'<span style="color:#1a7f37;">&#9660; {round(vs_base)}% faster</span>'
    elif vs_base > 0:
        sub = f'<span style="color:#cf222e;">&#9650; +{round(vs_base)}% slower</span>'
    else:
        sub = '<span style="color:#57606a;">flat</span>'
    return f'{days}d<div style="font-size:11px;">{sub}</div>'


def _overdue_cell(open_count: int, overdue: int) -> str:
    if overdue > 0:
        badge = (f'<span style="background:#cf222e;color:#fff;padding:1px 7px;border-radius:999px;'
                 f'font-size:11px;font-weight:700;">{overdue} overdue</span>')
    else:
        badge = '<span style="color:#1a7f37;font-size:11px;">none overdue</span>'
    return f'{open_count}<div style="margin-top:3px;">{badge}</div>'


def _split_bar(ai: int, human: int) -> str:
    total = ai + human or 1
    ai_w = round(ai / total * 100)
    hum_w = 100 - ai_w
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;margin-top:6px;">'
        f'<tr><td style="width:{ai_w}%;background:#2b6cb0;height:8px;font-size:1px;line-height:1px;border-radius:4px 0 0 4px;">&nbsp;</td>'
        f'<td style="width:{hum_w}%;background:#cbd5e0;height:8px;font-size:1px;line-height:1px;border-radius:0 4px 4px 0;">&nbsp;</td></tr></table>'
        f'<div style="font-size:10px;color:#57606a;margin-top:3px;">'
        f'<span style="color:#2b6cb0;">&#9632; AI {ai}</span> &nbsp; '
        f'<span style="color:#8a94a6;">&#9632; people {human}</span></div>'
    )


# ── tiles + rows ────────────────────────────────────────────────────


def _hero_tile(t: TeamRollup) -> str:
    bg, fg, _ = _ai_band(t.ai_touched_share)
    return (
        f'<td style="border:1px solid #d0d7de;border-radius:10px;padding:14px 16px;vertical-align:top;width:34%;background:{bg};">'
        f'<div style="font-size:11px;letter-spacing:0.5px;color:{fg};text-transform:uppercase;font-weight:700;">Team AI leverage</div>'
        f'<div style="font-size:34px;font-weight:800;color:{fg};line-height:1.1;margin-top:2px;">{_pct(t.ai_touched_share)}</div>'
        f'<div style="font-size:11px;color:{fg};">of {t.completed_7d} completions this week</div>'
        f'{_split_bar(t.ai_completions_7d, t.human_completions_7d)}</td>'
    )


def _tile(label: str, value: str, sub: str = "", spark: str = "") -> str:
    sub_html = f'<div style="font-size:11px;color:#57606a;margin-top:2px;">{sub}</div>' if sub else ""
    spark_html = f'<div style="margin-top:8px;">{spark}</div>' if spark else ""
    return (
        '<td style="border:1px solid #d0d7de;border-radius:10px;padding:14px 16px;vertical-align:top;">'
        f'<div style="font-size:11px;letter-spacing:0.5px;color:#57606a;text-transform:uppercase;">{label}</div>'
        f'<div style="font-size:24px;font-weight:700;margin-top:2px;">{value}</div>'
        f'{sub_html}{spark_html}</td>'
    )


def _operator_row(c: OperatorScorecard) -> str:
    bg, fg, _ = _ai_band(c.ai_touched_share)
    ai_sub = f'{c.ai_assisted_count}/{c.assigned_completed_7d} by AI' if c.assigned_completed_7d else "no completions"
    spark = _sparkline(c.spark_completed)
    return (
        '<tr>'
        f'<td style="padding:10px 8px;border-bottom:1px solid #eaecef;">'
        f'<strong>{c.display_name}</strong>'
        f'<div style="margin-top:4px;">{spark}</div></td>'
        f'<td style="padding:10px 8px;border-bottom:1px solid #eaecef;text-align:center;">{c.completed_7d}'
        f'<div style="font-size:11px;color:#57606a;">prior {c.completed_prior_7d}</div></td>'
        f'<td style="padding:10px 8px;border-bottom:1px solid #eaecef;text-align:center;background:{bg};">'
        f'<span style="font-size:18px;font-weight:800;color:{fg};">{_pct(c.ai_touched_share)}</span>'
        f'<div style="font-size:10px;color:{fg};">{ai_sub}</div></td>'
        f'<td style="padding:10px 8px;border-bottom:1px solid #eaecef;text-align:center;">{_trend_before(c.throughput_vs_baseline_pct)}</td>'
        f'<td style="padding:10px 8px;border-bottom:1px solid #eaecef;text-align:center;">{_cycle_cell(c.median_cycle_days, c.cycle_vs_baseline_pct)}</td>'
        f'<td style="padding:10px 8px;border-bottom:1px solid #eaecef;text-align:center;">{_overdue_cell(c.open_count, c.overdue_count)}</td>'
        f'<td style="padding:10px 8px;border-bottom:1px solid #eaecef;">{_pill(c.verdict)}'
        f'<div style="font-size:11px;color:#57606a;margin-top:4px;">{c.verdict_reason}</div></td>'
        '</tr>'
    )


def render_html(sc: ProductivityScorecard) -> str:
    t: TeamRollup = sc.team
    date_str = sc.generated_at[:10]
    banner_color = _VERDICT_COLOR.get(t.verdict, "#8b949e")

    banner = (
        f'<table role="presentation" cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;'
        f'background:{banner_color};border-radius:10px;margin-bottom:18px;"><tr><td style="padding:16px 20px;color:#fff;">'
        f'<div style="font-size:12px;letter-spacing:0.6px;opacity:0.9;">TEAM ASSESSMENT &middot; coloured by AI adoption</div>'
        f'<div style="font-size:20px;font-weight:800;margin:3px 0 4px;">{_VERDICT_LABEL.get(t.verdict, t.verdict)}</div>'
        f'<div style="font-size:13px;">{t.verdict_reason}</div></td></tr></table>'
    )

    low_conf = ""
    if sc.low_confidence:
        low_conf = (
            '<div style="background:#fff8c5;border:1px solid #d4a72c;color:#54470f;padding:10px 14px;'
            'border-radius:8px;margin-bottom:18px;font-size:13px;">'
            f'New system went live {sc.launch_date}. Per-person trend reads (vs before) are low-confidence '
            'until enough post-launch history accrues. AI-adoption numbers are real now.</div>'
        )

    tiles = (
        "<table role='presentation' cellpadding='0' cellspacing='0' style='border-collapse:separate;border-spacing:8px;width:100%;margin-bottom:8px;'><tr>"
        + _hero_tile(t)
        + _tile("People using AI", f"{sum(1 for c in sc.operators if (c.ai_touched_share or 0) >= AI_LOW_THRESHOLD)}/{t.people}",
                "operators at 20%+ AI share")
        + _tile("Completed (7d)", str(t.completed_7d),
                f"{t.completed_today} today &middot; {t.active_operators_7d} active",
                _sparkline(t.spark_completed, height=30))
        + "</tr></table>"
    )

    active = [c for c in sc.operators if c.completed_7d or c.open_count]
    shown = active[:MAX_OPERATOR_ROWS]
    rows = "\n".join(_operator_row(c) for c in shown) or (
        '<tr><td colspan="7" style="text-align:center;color:#57606a;padding:20px;">No operators in scope.</td></tr>')
    hidden = len(active) - len(shown)
    if hidden > 0:
        rows += (f'\n<tr><td colspan="7" style="text-align:center;color:#57606a;padding:10px;font-size:12px;">'
                 f'+ {hidden} more active operators not shown (top {len(shown)} by AI adoption)</td></tr>')

    a = sc.assumptions
    excl = ", ".join(a.get("excluded_projects", [])) or "none"
    footer = (
        '<div style="color:#57606a;font-size:12px;margin-top:22px;line-height:1.6;">'
        "<strong>How to read this.</strong> This report tracks adoption of the AI-paired task system and "
        "whether it is lifting output. <em>AI share</em> (the coloured column) is how much of a person's "
        "completed work the AI closed: green = heavy use, amber = partial, red = low. <em>vs Before</em> "
        "compares this week's output to that person's pre-launch baseline; green up = producing more than "
        "before. The mini bars are daily completions over the last "
        f"{a.get('spark_days')} days. Overdue counts are flagged in red.<br><br>"
        f"<strong>Scope.</strong> Employees + Gov Contracts only. Excluded projects: {excl}. "
        f"AI-completed work = tasks closed by {', '.join(a.get('ai_actors', []))}. Adoption bands: "
        f"green &ge; {a.get('ai_high_pct')}%, amber &ge; {a.get('ai_low_pct')}%. Estimated savings use "
        f"{a.get('minutes_saved_per_ai_task')} min per AI-completed task at ${a.get('dollars_per_hour'):,.0f}/hr "
        f"(team total ${t.est_dollars_saved_7d:,.0f} this week)."
        "</div>"
    )

    return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8">
<title>Productivity and AI adoption report - {date_str}</title></head>
<body style="font-family:-apple-system,system-ui,Arial,sans-serif;max-width:920px;margin:0 auto;padding:0;color:#1f2328;background:#f6f8fa;">
<table role="presentation" cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;background:#1a365d;background:linear-gradient(90deg,#1a365d,#2b6cb0);">
  <tr><td style="padding:22px 24px;color:#fff;">
    <div style="font-size:23px;font-weight:800;">Productivity &amp; AI adoption</div>
    <div style="font-size:13px;opacity:0.9;margin-top:3px;">Who is using the AI-paired system, and is it producing more than before &middot;
      {sc.generated_at[:16].replace('T', ' ')} UTC &middot; last {sc.window_days} days &middot; live since {sc.launch_date}</div>
  </td></tr>
</table>
<div style="background:#ffffff;padding:20px 24px;">
{banner}
{low_conf}
{tiles}
<table role="presentation" cellpadding="0" cellspacing="0" style="border-collapse:collapse;width:100%;font-size:13px;margin-top:6px;">
  <thead>
    <tr style="background:#1f2328;color:#fff;">
      <th style="text-align:left;padding:9px 8px;font-size:11px;letter-spacing:0.5px;">OPERATOR &middot; 14d completions</th>
      <th style="padding:9px 8px;font-size:11px;">DONE 7D</th>
      <th style="padding:9px 8px;font-size:11px;">AI SHARE</th>
      <th style="padding:9px 8px;font-size:11px;">vs BEFORE</th>
      <th style="padding:9px 8px;font-size:11px;">CYCLE</th>
      <th style="padding:9px 8px;font-size:11px;">OPEN</th>
      <th style="text-align:left;padding:9px 8px;font-size:11px;">ASSESSMENT</th>
    </tr>
  </thead>
  <tbody>
{rows}
  </tbody>
</table>
{footer}
</div>
</body>
</html>
"""
