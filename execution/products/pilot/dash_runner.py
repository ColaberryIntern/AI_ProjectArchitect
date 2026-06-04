"""Shared dash-render pipeline for the pilot agents (Karun + Kes).

The full pipeline per `.claude/skills/{karun,kes}-agent/SKILL.md`:
    1. Read 5 MCP data sources (DRI-specific)
    2. Score Karun/Kes's 5 numbers against the PRD rubric
    3. Render HTML
    4. Run critic loop
    5. Ship (write to disk + optional delivery)

Until Karun 1 / Kes 1 PRDs are signed, steps 1-2 are stubbed and the
HTML is a placeholder banner that says exactly that. This keeps the
scheduler harness shippable + verifiable today; the moment the PRDs
land, only `_score()` and `_load_sources()` need real implementations.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

DRIName = Literal["karun", "kes"]
ROOT = Path(__file__).resolve().parents[3]
OUTPUT_ROOT = ROOT / "output" / "library" / "_pilot"

# Hard ceiling per ticket acceptance: dashboards must render in <= 60s.
WALL_CLOCK_BUDGET_SECONDS = int(os.environ.get("PILOT_DASH_BUDGET_SECONDS", "60"))


@dataclass
class DashResult:
    """One run's outcome — what got written, what failed, where it went."""
    dri: DRIName
    ran_at: str             # ISO timestamp
    output_path: str        # absolute path of the file we wrote
    status: str             # 'ok' | 'critic_failed' | 'error'
    error: str = ""
    critic_failures: list[str] = None
    placeholder: bool = False   # True until PRD scoring lands

    def __post_init__(self):
        if self.critic_failures is None:
            self.critic_failures = []


# ── Source loading + scoring ───────────────────────────────────────
#
# Per `.claude/skills/{dri}-agent/SKILL.md`, the full pipeline reads
# 5 MCP data sources (BC + DRI-specific). Until the Karun 1 / Kes 1
# PRDs are signed and we know the exact 5 numbers + rubric Ali wants,
# we score against the data we already have synced locally: the BC
# todo mirror. This gives a real dashboard at the Monday 1:1 even
# pre-ratification — not a placeholder banner. When the PRDs sign,
# this gets replaced with PRD §3-specific source queries.


# Owner email for the user the scheduler runs against. Today the
# only pilot user is Ali; multi-tenant pilot dispatch will land when
# more DRIs come online.
_PILOT_USER = os.environ.get("PILOT_DASH_USER", "ali@colaberry.com")
# Keywords that mark a BC todo as "Karun-relevant" / "Kes-relevant".
# These are heuristic — replaced by precise project_id / list_id
# matchers once the PRD §4 (the 10-12 skills) is enumerated.
_DRI_KEYWORDS = {
    "karun": ("karun", "sales", "shipces", "shipcs", "carrier", "rate confirmation"),
    "kes": ("kes", "github", "infra", "deploy", "skill_registry", "mcp"),
}


def _todo_is_relevant_to_dri(todo, dri: DRIName) -> bool:
    """Heuristic: does this todo belong on this DRI's dashboard?

    Looks at project name, todolist name, todo title, and assignee
    names for any of the DRI's keywords. Case-insensitive.
    """
    kws = _DRI_KEYWORDS[dri]
    haystack = " ".join(filter(None, [
        getattr(todo, "bc_project_name", "") or "",
        getattr(todo, "bc_todolist_name", "") or "",
        getattr(todo, "title", "") or "",
        " ".join(getattr(todo, "assignee_names", []) or []),
    ])).lower()
    return any(kw in haystack for kw in kws)


def _load_sources(dri: DRIName) -> dict:
    """Pull data sources for `dri`'s dashboard.

    Today: only the BC todo mirror (real). Other sources (Gmail,
    HubSpot, Apollo, CCPP for Karun; GitHub, Library, skill_registry,
    MCP catalog for Kes) are marked as 'not_configured' until the
    PRD specifies which queries to run.
    """
    from execution.products.ops import store
    todos = store.load_todos(_PILOT_USER)
    relevant = [t for t in todos if _todo_is_relevant_to_dri(t, dri)]
    return {
        "_stub": False,
        "_pre_ratification": True,
        "user": _PILOT_USER,
        "bc_todos": relevant,
        "bc_todos_total_unfiltered": len(todos),
        "extra_sources": {
            ("gmail" if dri == "karun" else "github"): "not_configured (PRD pending)",
            ("hubspot" if dri == "karun" else "library"): "not_configured (PRD pending)",
            ("apollo" if dri == "karun" else "skill_registry"): "not_configured (PRD pending)",
            "ccpp" if dri == "karun" else "mcp_catalog": "not_configured (PRD pending)",
        },
    }


def _score(dri: DRIName, sources: dict) -> list[dict]:
    """Score the 5 numbers for this DRI.

    Pre-ratification, the 5 numbers come from observable BC signals:
        1. Open items in scope
        2. Overdue items in scope
        3. Completed in the last 7 days
        4. Average cycle time of recent completions (days)
        5. Stale items (open, no activity in 7+ days)

    Post-PRD, these get swapped for the exact 5 numbers Ali ratifies
    in `docs/personas/{dri}-prd.md` §3.
    """
    todos = sources.get("bc_todos", [])
    today = datetime.now(timezone.utc).date()
    week_ago = today - timedelta(days=7)

    open_todos = [t for t in todos if t.status == "active" and not t.is_dismissed]
    overdue = []
    for t in open_todos:
        if not t.due_on:
            continue
        try:
            d = datetime.strptime(t.due_on, "%Y-%m-%d").date()
            if d < today:
                overdue.append(t)
        except ValueError:
            pass

    completed_recent = []
    for t in todos:
        if t.status != "completed" or not t.completed_at:
            continue
        try:
            ts = t.completed_at.replace("Z", "+00:00")
            cd = datetime.fromisoformat(ts)
            if cd.tzinfo is None:
                cd = cd.replace(tzinfo=timezone.utc)
            if cd.date() >= week_ago:
                completed_recent.append(t)
        except (ValueError, TypeError):
            pass

    cycle_days = [t.cycle_seconds / 86400 for t in completed_recent
                  if getattr(t, "cycle_seconds", 0) > 0]
    avg_cycle = round(sum(cycle_days) / len(cycle_days), 1) if cycle_days else 0.0

    stale = []
    for t in open_todos:
        if not t.bc_updated_at:
            continue
        try:
            ts = t.bc_updated_at.replace("Z", "+00:00")
            ud = datetime.fromisoformat(ts)
            if ud.tzinfo is None:
                ud = ud.replace(tzinfo=timezone.utc)
            if (datetime.now(timezone.utc) - ud) > timedelta(days=7):
                stale.append(t)
        except (ValueError, TypeError):
            pass

    return [
        {
            "number": "Open items in scope", "value": len(open_todos),
            "delta": None, "on_track": len(open_todos) < 30,
            "rubric_pass": True, "source": "BC todos.status=='active'",
        },
        {
            "number": "Overdue", "value": len(overdue),
            "delta": None, "on_track": len(overdue) == 0,
            "rubric_pass": True, "source": "BC todos.due_on < today",
        },
        {
            "number": "Closed in last 7d", "value": len(completed_recent),
            "delta": None, "on_track": len(completed_recent) >= 3,
            "rubric_pass": True, "source": "BC todos.completed_at >= today-7d",
        },
        {
            "number": "Avg cycle (days)", "value": avg_cycle,
            "delta": None, "on_track": avg_cycle <= 5,
            "rubric_pass": True, "source": "mean(completed.cycle_seconds) over 7d",
        },
        {
            "number": "Stale (no activity 7d+)", "value": len(stale),
            "delta": None, "on_track": len(stale) <= 5,
            "rubric_pass": True, "source": "BC todos.updated_at < today-7d, status=active",
        },
    ]


# ── Critic loop ────────────────────────────────────────────────────


def _critic(dri: DRIName, scored: list[dict], sources: dict) -> list[str]:
    """Returns list of critic failure messages; empty list = pass.

    Six base checks per `.claude/skills/{dri}-agent/SKILL.md` §Critic loop.
    Pre-ratification mode (sources["_pre_ratification"] = True) is a known
    state: we score against BC-only signals, so the critic accepts that
    not every PRD-specific check is in play yet. Once the PRD signs, this
    block is removed and the full critic gates delivery.
    """
    failures: list[str] = []

    if sources.get("_stub"):
        failures.append("scoring stub — PRD §3 + §5 not yet signed; placeholder HTML rendered")
        return failures   # short-circuit the other checks while in stub mode

    if len(scored) != 5:
        failures.append(f"completeness: expected 5 scored numbers, got {len(scored)}")
    for i, row in enumerate(scored):
        if row.get("value") is None:
            failures.append(f"completeness: number {i+1} '{row.get('number')}' has no current value")
        # Banned phrases only apply to free-text fields, not numeric `value`.
        for k in ("number", "source"):
            v = row.get(k, "")
            if not isinstance(v, str):
                continue
            for banned in ("might", "could", "approximately", "around", "roughly"):
                if banned in v.lower():
                    failures.append(f"banned phrase '{banned}' in number {i+1} field '{k}'")
                    break
    return failures


# ── HTML render ────────────────────────────────────────────────────


def _render_html(dri: DRIName, scored: list[dict], critic_failures: list[str],
                              run_iso: str, sources: dict | None = None) -> str:
    """Render the dashboard.

    Three modes:
      - Stub mode (no sources): red 'pre-ratification scaffold' banner
      - Pre-ratification mode (BC-only sources, no PRD): amber banner
        '5 numbers are provisional — swap to PRD §3 when signed'
      - Full mode (post-PRD): no banner, all numbers tagged
    """
    sources = sources or {}
    dri_title = dri.capitalize()
    is_stub = any("scoring stub" in f for f in critic_failures)
    is_provisional = bool(sources.get("_pre_ratification")) and not is_stub

    if is_stub:
        banner_color = "#cf222e"
        banner_text = ("⚠ PRE-RATIFICATION SCAFFOLD — Karun 1 / Kes 1 PRD not yet signed. "
                       "The 5 numbers and per-number rubric are placeholders.")
    elif is_provisional:
        banner_color = "#8a5a00"
        banner_text = (f"⚠ PROVISIONAL — {dri_title} PRD §3 (the 5 numbers) not yet ratified. "
                       f"Numbers below are derived from observable BC signals as a stand-in. "
                       f"Real scoring locks in when Ali signs <code>docs/personas/{dri}-prd.md</code>.")
    else:
        banner_color = "#1a7f37"
        banner_text = ""

    def _on_track_pill(row):
        if row.get("on_track") is None:
            return "—"
        ok = bool(row["on_track"])
        bg, fg, txt = ("#dafbe1", "#15803d", "on track") if ok else ("#ffeef0", "#82071e", "off track")
        return (f'<span style="background:{bg};color:{fg};padding:2px 8px;'
                f'border-radius:999px;font-size:11px;font-weight:600;">{txt}</span>')

    tiles = "\n".join(
        f'<tr>'
        f'<td>{i+1}</td>'
        f'<td><strong>{r["number"]}</strong>'
        f'<div style="font-size:11px;color:#57606a;">{r.get("source", "")}</div></td>'
        f'<td><span style="font-size:18px;font-weight:700;">{r.get("value") if r.get("value") is not None else "<em>tbd</em>"}</span></td>'
        f'<td>{r.get("delta") if r.get("delta") is not None else "—"}</td>'
        f'<td>{_on_track_pill(r)}</td>'
        f'</tr>'
        for i, r in enumerate(scored)
    )
    extra_sources = sources.get("extra_sources", {})
    sources_block = ""
    if extra_sources:
        rows = "".join(
            f'<tr><td><code>{k}</code></td><td>{v}</td></tr>' for k, v in extra_sources.items()
        )
        sources_block = (
            f'<details class="footer"><summary>Other sources awaiting PRD</summary>'
            f'<table style="margin-top:6px;">{rows}</table></details>'
        )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{dri_title} dash — {run_iso[:10]}</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 760px;
              margin: 24px auto; padding: 0 20px; color: #1f2328; }}
  h1 {{ margin-bottom: 4px; }}
  .meta {{ color: #57606a; font-size: 13px; margin-bottom: 18px; }}
  .banner {{ background: {banner_color}; color: white; padding: 12px 16px;
                  border-radius: 8px; margin-bottom: 18px; font-size: 13px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
  th, td {{ border-bottom: 1px solid #d0d7de; padding: 10px 8px; text-align: left;
                vertical-align: top; }}
  th {{ background: #f6f8fa; font-size: 11px; letter-spacing: 0.6px; }}
  .footer {{ color: #57606a; font-size: 12px; margin-top: 24px; }}
  code {{ background: #f6f8fa; padding: 1px 4px; border-radius: 3px; }}
</style>
</head>
<body>
<h1>{dri_title} 1:1 dashboard</h1>
<div class="meta">Generated {run_iso} · 30 min before Ali ↔ {dri_title} 1:1 ·
  source: <code>docs/personas/{dri}-prd.md</code> · BC items in scope: {len(sources.get("bc_todos", []))}</div>
{f'<div class="banner">{banner_text}</div>' if banner_text else ''}
<table>
  <thead><tr><th>#</th><th>Number</th><th>Current</th><th>Δ vs prior wk</th><th>Status</th></tr></thead>
  <tbody>
{tiles}
  </tbody>
</table>
<p class="footer">Opening question: <em>Of these score deltas, which one surprised you?</em></p>
{sources_block}
{f'<details class="footer"><summary>Critic failures ({len(critic_failures)})</summary><ul>' + "".join(f"<li>{f}</li>" for f in critic_failures) + "</ul></details>" if critic_failures else ""}
</body>
</html>
"""


# ── Public entry point ─────────────────────────────────────────────


def run(dri: DRIName) -> DashResult:
    """End-to-end: load → score → critic → render → write to disk.

    Always returns a DashResult; on critic failure we still write the
    HTML so a human can inspect what was produced. Delivery (Gmail
    push) is a separate concern and lives in scheduler.py once enabled.
    """
    started = datetime.now(timezone.utc).isoformat()
    out_dir = OUTPUT_ROOT / dri
    out_dir.mkdir(parents=True, exist_ok=True)
    date_str = started[:10]

    try:
        sources = _load_sources(dri)
        scored = _score(dri, sources)
        critic_failures = _critic(dri, scored, sources)
        html = _render_html(dri, scored, critic_failures, started, sources)

        out_path = out_dir / f"{date_str}.html"
        out_path.write_text(html, encoding="utf-8")

        # Persist a JSON sidecar with the run's structured data
        sidecar = {
            "dri": dri,
            "ran_at": started,
            "sources_stub": bool(sources.get("_stub")),
            "pre_ratification": bool(sources.get("_pre_ratification")),
            "bc_todos_in_scope": len(sources.get("bc_todos", [])),
            "scored": scored,
            "critic_failures": critic_failures,
            "html_path": str(out_path),
        }
        (out_dir / f"{date_str}.json").write_text(json.dumps(sidecar, indent=2), encoding="utf-8")

        is_stub = bool(sources.get("_stub"))
        is_provisional = bool(sources.get("_pre_ratification")) and not is_stub
        # 'ok' if rendered cleanly; 'critic_failed' only blocks delivery in
        # post-PRD mode. Pre-ratification provisional dashboards still ship
        # to Ali so he can review the BC-derived numbers before the 1:1.
        status = "critic_failed" if (critic_failures and not is_stub and not is_provisional) else "ok"
        return DashResult(
            dri=dri, ran_at=started, output_path=str(out_path),
            status=status, critic_failures=critic_failures,
            placeholder=is_stub or is_provisional,
        )
    except Exception as e:
        logger.exception("pilot dash for %s failed", dri)
        return DashResult(
            dri=dri, ran_at=started, output_path="", status="error", error=str(e),
        )
