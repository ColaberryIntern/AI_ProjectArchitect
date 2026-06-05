"""Render the Op 4 v01 review artifact: 2 scenarios (auto-close + ask-to-confirm) + decision tree.

Usage:
    python scripts/operator_04_preview.py --out tmp/operator-04-v01.html
"""

from __future__ import annotations

import argparse
import html as html_lib
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from execution.products.library import auto_close  # noqa: E402


def scenario_high_confidence() -> tuple[auto_close.WorkArtifacts, auto_close.CloseDecision, str]:
    """Scenario A: schema-safe library change, tests pass, low blast radius."""
    artifacts = auto_close.WorkArtifacts(
        work_shipped=True,
        verification_passed=True,
        progress_md_updated=True,
        recent_blockers_count=0,
        files_touched=[
            "backend/src/services/leadRouter.ts",
            "backend/src/services/leadRouterRetry.ts",
            "backend/src/services/__tests__/leadRouter.test.ts",
        ],
        summary="Added retry loop with exponential backoff to the lead router. 12 tests pass.",
        test_evidence="test_lead_router (12 pass / 0 fail)",
    )
    confidence = auto_close.compute_confidence(
        directive_clarity=0.95,
        test_coverage=0.90,
        reversibility=0.95,
        blast_radius=0.90,
        compliance_safety=1.00,
    )
    decision = auto_close.decide_close_action(artifacts, confidence)
    card = auto_close.render_auto_close_card(
        artifacts, decision,
        session_id="CC-20260605-4w8q",
        commit_sha="abc123def",
        progress_md_url="https://github.com/ColaberryIntern/AI_ProjectArchitect/blob/main/PROGRESS.md",
    )
    return artifacts, decision, card


def scenario_low_confidence() -> tuple[auto_close.WorkArtifacts, auto_close.CloseDecision, str]:
    """Scenario B: production schema migration. Done but high blast = ask-to-confirm."""
    artifacts = auto_close.WorkArtifacts(
        work_shipped=True,
        verification_passed=True,
        progress_md_updated=True,
        recent_blockers_count=0,
        files_touched=[
            "backend/src/seeds/migrations/20260605_add_retry_count_to_leads.sql",
            "backend/src/services/leadRouter.ts",
        ],
        summary="Schema migration on prod leads table; added retry_count column.",
        test_evidence="tsc clean; dev DB migration test passed",
    )
    # High clarity + tests + compliance OK, but reversibility LOW (DB column on prod) +
    # blast radius LOW (touches prod schema). These pull aggregate below 0.85.
    confidence = auto_close.compute_confidence(
        directive_clarity=0.95,
        test_coverage=0.85,
        reversibility=0.40,       # DB column hard to drop safely
        blast_radius=0.50,        # touches production schema
        compliance_safety=0.95,
    )
    decision = auto_close.decide_close_action(artifacts, confidence)
    card = auto_close.render_ask_confirm_card(artifacts, decision, session_id="CC-20260605-4w8q")
    return artifacts, decision, card


def scenario_not_ready() -> tuple[auto_close.WorkArtifacts, auto_close.CloseDecision]:
    """Scenario C: gate failure -> no comment posted at all."""
    artifacts = auto_close.WorkArtifacts(
        work_shipped=True,
        verification_passed=False,  # tests failing
        progress_md_updated=False,  # forgot to update
        recent_blockers_count=1,    # blocker in last 5 comments
        files_touched=["backend/src/services/leadRouter.ts"],
        summary="Mid-work on retry logic; tests not yet passing.",
    )
    confidence = auto_close.compute_confidence(
        directive_clarity=0.95, test_coverage=0.30, reversibility=0.95, blast_radius=0.90, compliance_safety=1.00,
    )
    decision = auto_close.decide_close_action(artifacts, confidence)
    return artifacts, decision


def render_html(generated_at_iso: str) -> str:
    art_a, dec_a, card_a = scenario_high_confidence()
    art_b, dec_b, card_b = scenario_low_confidence()
    art_c, dec_c = scenario_not_ready()

    def conf_breakdown(d: auto_close.CloseDecision) -> str:
        return (
            f"directive {d.confidence.directive_clarity:.2f}, "
            f"tests {d.confidence.test_coverage:.2f}, "
            f"reversibility {d.confidence.reversibility:.2f}, "
            f"blast {d.confidence.blast_radius:.2f}, "
            f"compliance {d.confidence.compliance_safety:.2f}"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8" /><title>Operator 4 v01 - Auto-close + confidence gate review</title></head>
<body style="font-family: 'Aptos', Arial, sans-serif; color: #1a202c; background: #f7fafc; margin: 0; padding: 24px;">
<div style="max-width: 920px; margin: 0 auto;">

<div style="background: linear-gradient(135deg, #1a365d 0%, #2b6cb0 100%); color: #ffffff; padding: 24px 28px; border-radius: 10px; margin-bottom: 24px;">
  <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 1.5px; opacity: 0.85;">Operator 4 &middot; Review v01</div>
  <div style="font-size: 24px; font-weight: 700; margin-top: 6px;">Auto-close tickets with confidence gate</div>
  <div style="font-size: 13px; opacity: 0.9; margin-top: 8px;">4-gate done check &middot; 5-dimension confidence &middot; >= 0.85 auto-close, &lt; 0.85 ask-to-confirm &middot; Generated {generated_at_iso}</div>
</div>

<div style="background: #ffffff; padding: 18px 22px; border-radius: 8px; margin-bottom: 22px; border-left: 4px solid #15803d;">
  <div style="font-weight: 700; color: #137333; margin-bottom: 6px;">What this ships</div>
  <div style="font-size: 14px; color: #2d3748;">
    A single module (<code>auto_close.py</code>) that Claude calls at session-end. It runs the 4-gate done check, computes confidence across 5 dimensions, and decides between auto-close, ask-to-confirm, or not-ready. Below are 3 real scenarios run through the actual code.
  </div>
</div>

<h2 style="color: #1a365d; border-bottom: 2px solid #e2e8f0; padding-bottom: 8px;">Decision logic</h2>

<table style="border-collapse: collapse; width: 100%; font-size: 13px;">
  <thead><tr style="background: #1a365d; color: #ffffff;">
    <th style="text-align: left; padding: 9px 12px;">Aggregate confidence</th>
    <th style="text-align: left; padding: 9px 12px;">Action</th>
    <th style="text-align: left; padding: 9px 12px;">BC effect</th>
  </tr></thead>
  <tbody>
    <tr style="background: #f7fafc;"><td style="padding: 8px 12px;">&gt;= 0.85 <em>and done-gate passes</em></td><td style="padding: 8px 12px;"><strong style="color: #15803d;">auto_close</strong></td><td style="padding: 8px 12px;">Green close-summary card posted + ticket marked complete</td></tr>
    <tr><td style="padding: 8px 12px;">0.70 - 0.85 <em>and done-gate passes</em></td><td style="padding: 8px 12px;"><strong style="color: #b06000;">ask_confirm</strong></td><td style="padding: 8px 12px;">Amber ask-to-confirm card posted; ticket stays open</td></tr>
    <tr style="background: #f7fafc;"><td style="padding: 8px 12px;">&lt; 0.70 <em>or done-gate fails</em></td><td style="padding: 8px 12px;"><strong style="color: #4a5568;">not_ready</strong></td><td style="padding: 8px 12px;">No comment posted; surfaces blocking reason in logs</td></tr>
  </tbody>
</table>

<h2 style="color: #1a365d; border-bottom: 2px solid #e2e8f0; padding-bottom: 8px; margin-top: 32px;">Scenario A: high confidence -&gt; auto-close</h2>

<p style="color: #4a5568; font-size: 14px;">
  Library-level change (lead router retry logic). Tests pass, low blast, easily reversible.
</p>

<div style="background: #ffffff; padding: 14px 18px; border-radius: 6px; border: 1px solid #e2e8f0; margin: 12px 0;">
  <div><strong>Decision:</strong> <code>{dec_a.action}</code></div>
  <div><strong>Aggregate confidence:</strong> {dec_a.aggregate_confidence:.2f}</div>
  <div><strong>Breakdown:</strong> {conf_breakdown(dec_a)}</div>
  <div><strong>Reasoning:</strong> {html_lib.escape(dec_a.reasoning)}</div>
</div>

<div style="margin: 12px 0;">{card_a}</div>

<h2 style="color: #1a365d; border-bottom: 2px solid #e2e8f0; padding-bottom: 8px; margin-top: 32px;">Scenario B: low confidence -&gt; ask-to-confirm</h2>

<p style="color: #4a5568; font-size: 14px;">
  Same done-gate state (work shipped, verified, PROGRESS.md updated, no blockers) but a production schema migration: low reversibility + medium blast radius pull aggregate below 0.85.
</p>

<div style="background: #ffffff; padding: 14px 18px; border-radius: 6px; border: 1px solid #e2e8f0; margin: 12px 0;">
  <div><strong>Decision:</strong> <code>{dec_b.action}</code></div>
  <div><strong>Aggregate confidence:</strong> {dec_b.aggregate_confidence:.2f}</div>
  <div><strong>Breakdown:</strong> {conf_breakdown(dec_b)}</div>
  <div><strong>Reasoning:</strong> {html_lib.escape(dec_b.reasoning)}</div>
</div>

<div style="margin: 12px 0;">{card_b}</div>

<h2 style="color: #1a365d; border-bottom: 2px solid #e2e8f0; padding-bottom: 8px; margin-top: 32px;">Scenario C: gate fails -&gt; not_ready (no BC action)</h2>

<p style="color: #4a5568; font-size: 14px;">
  Tests are failing, PROGRESS.md not updated, recent blocker in step comments. Done-gate refuses; no comment posted; Claude surfaces the blocking reason and continues working.
</p>

<div style="background: #ffffff; padding: 14px 18px; border-radius: 6px; border: 1px solid #e2e8f0; margin: 12px 0;">
  <div><strong>Decision:</strong> <code>{dec_c.action}</code></div>
  <div><strong>Aggregate confidence:</strong> {dec_c.aggregate_confidence:.2f}</div>
  <div><strong>Reasoning:</strong> {html_lib.escape(dec_c.reasoning)}</div>
  <div><strong>Blocking reasons:</strong></div>
  <ul style="margin: 4px 0 0 22px;">
    {''.join(f'<li>{html_lib.escape(r)}</li>' for r in dec_c.blocking_reasons)}
  </ul>
</div>

<h2 style="color: #1a365d; border-bottom: 2px solid #e2e8f0; padding-bottom: 8px; margin-top: 32px;">Code shipped</h2>

<table style="border-collapse: collapse; width: 100%; font-size: 13px;">
  <thead><tr style="background: #1a365d; color: #ffffff;">
    <th style="text-align: left; padding: 9px 12px;">File</th>
    <th style="text-align: left; padding: 9px 12px;">What it is</th>
  </tr></thead>
  <tbody>
    <tr style="background: #f7fafc;"><td style="padding: 8px 12px;"><code>execution/products/library/auto_close.py</code></td><td style="padding: 8px 12px;"><code>is_ticket_done()</code> + <code>compute_confidence()</code> + <code>decide_close_action()</code> (pure function) + <code>render_auto_close_card()</code> / <code>render_ask_confirm_card()</code> + <code>execute_close_decision()</code> (side-effecting). Stdlib only.</td></tr>
    <tr><td style="padding: 8px 12px;"><code>scripts/operator_04_preview.py</code></td><td style="padding: 8px 12px;">This preview. Runs the 3 scenarios through the real decide_close_action() and renders the actual comment cards.</td></tr>
  </tbody>
</table>

<div style="margin-top: 28px; padding-top: 14px; border-top: 1px solid #e2e8f0; font-size: 12px; color: #718096;">
  Spec: <code>docs/specs/operator-04-auto-close-tickets.md</code> &middot;
  BC ticket: <code>9967247829</code> &middot;
  Session: CC-20260605-4w8q
</div>

</div>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="tmp/operator-04-v01.html")
    args = parser.parse_args()

    generated_at = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    html_doc = render_html(generated_at_iso=generated_at)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_doc, encoding="utf-8")
    print(f"[render] {len(html_doc):,} bytes -> {out_path}")
    print(f"RESULT_JSON:{{\"out\":\"{out_path}\",\"chars\":{len(html_doc)}}}")


if __name__ == "__main__":
    main()
