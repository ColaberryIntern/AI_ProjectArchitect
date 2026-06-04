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
from datetime import datetime, timezone
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


# ── Stubs (real implementations land once PRDs are signed) ─────────


def _load_sources(dri: DRIName) -> dict:
    """STUB: real impl reads BC + DRI-specific MCP sources.

    Karun: Gmail + HubSpot + Apollo + CCPP.
    Kes:   GitHub + Library + skill_registry + MCP catalog.
    """
    return {
        "_stub": True,
        "_message": f"{dri}-agent data sources not yet wired — see "
                    f".claude/skills/{dri}-agent/SKILL.md",
    }


def _score(dri: DRIName, sources: dict) -> list[dict]:
    """STUB: real impl scores the 5 numbers per `docs/personas/{dri}-prd.md` §3.

    Returns one tile row per number.
    """
    return [
        {"number": "PRD §3 number 1", "value": None, "delta": None, "on_track": None, "rubric_pass": None},
        {"number": "PRD §3 number 2", "value": None, "delta": None, "on_track": None, "rubric_pass": None},
        {"number": "PRD §3 number 3", "value": None, "delta": None, "on_track": None, "rubric_pass": None},
        {"number": "PRD §3 number 4", "value": None, "delta": None, "on_track": None, "rubric_pass": None},
        {"number": "PRD §3 number 5", "value": None, "delta": None, "on_track": None, "rubric_pass": None},
    ]


# ── Critic loop ────────────────────────────────────────────────────


def _critic(dri: DRIName, scored: list[dict], sources: dict) -> list[str]:
    """Returns list of critic failure messages; empty list = pass.

    Six base checks per `.claude/skills/{dri}-agent/SKILL.md` §Critic loop.
    Until PRD scoring is real, the placeholder path triggers a known
    'scoring not implemented' failure — caught explicitly upstream.
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
        text = " ".join(str(v) for v in row.values() if isinstance(v, str))
        for banned in ("might", "could", "approximately", "around", "roughly"):
            if banned in text.lower():
                failures.append(f"banned phrase '{banned}' in number {i+1}")
                break
    return failures


# ── HTML render ────────────────────────────────────────────────────


def _render_html(dri: DRIName, scored: list[dict], critic_failures: list[str],
                              run_iso: str) -> str:
    """Render one dashboard. Placeholder when scoring is stubbed."""
    dri_title = dri.capitalize()
    is_placeholder = any("scoring stub" in f for f in critic_failures)
    banner_color = "#cf222e" if is_placeholder else "#1a7f37"
    banner_text = ("⚠ PRE-RATIFICATION SCAFFOLD — Karun 1 / Kes 1 PRD not yet signed. "
                   "The 5 numbers and per-number rubric are placeholders. Real scoring "
                   "lands once the PRD signature flips Status → Colaberry-approved.")
    tiles = "\n".join(
        f'<tr><td>{i+1}</td><td>{r["number"]}</td>'
        f'<td>{r.get("value") if r.get("value") is not None else "<em>tbd</em>"}</td>'
        f'<td>{r.get("delta") if r.get("delta") is not None else "—"}</td>'
        f'<td>{"on" if r.get("on_track") else "off"}</td></tr>'
        for i, r in enumerate(scored)
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{dri_title} dash — {run_iso[:10]}</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 720px;
              margin: 24px auto; padding: 0 20px; color: #1f2328; }}
  h1 {{ margin-bottom: 4px; }}
  .meta {{ color: #57606a; font-size: 13px; margin-bottom: 18px; }}
  .banner {{ background: {banner_color}; color: white; padding: 12px 16px;
                  border-radius: 8px; margin-bottom: 18px; font-size: 13px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
  th, td {{ border-bottom: 1px solid #d0d7de; padding: 8px 6px; text-align: left; }}
  th {{ background: #f6f8fa; }}
  .footer {{ color: #57606a; font-size: 12px; margin-top: 32px; }}
</style>
</head>
<body>
<h1>{dri_title} 1:1 dashboard</h1>
<div class="meta">Generated {run_iso} · 30 min before Ali ↔ {dri_title} 1:1 ·
  source: <code>docs/personas/{dri}-prd.md</code></div>
{f'<div class="banner">{banner_text}</div>' if is_placeholder else ''}
<table>
  <thead><tr><th>#</th><th>Number</th><th>Current</th><th>Δ vs prior wk</th><th>Track</th></tr></thead>
  <tbody>
{tiles}
  </tbody>
</table>
<p class="footer">Opening question: <em>Of these score deltas, which one surprised you?</em></p>
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
        html = _render_html(dri, scored, critic_failures, started)

        out_path = out_dir / f"{date_str}.html"
        out_path.write_text(html, encoding="utf-8")

        # Persist a JSON sidecar with the run's structured data
        sidecar = {
            "dri": dri,
            "ran_at": started,
            "sources_stub": bool(sources.get("_stub")),
            "scored": scored,
            "critic_failures": critic_failures,
            "html_path": str(out_path),
        }
        (out_dir / f"{date_str}.json").write_text(json.dumps(sidecar, indent=2), encoding="utf-8")

        placeholder = bool(sources.get("_stub"))
        status = "critic_failed" if (critic_failures and not placeholder) else "ok"
        return DashResult(
            dri=dri, ran_at=started, output_path=str(out_path),
            status=status, critic_failures=critic_failures, placeholder=placeholder,
        )
    except Exception as e:
        logger.exception("pilot dash for %s failed", dri)
        return DashResult(
            dri=dri, ran_at=started, output_path="", status="error", error=str(e),
        )
