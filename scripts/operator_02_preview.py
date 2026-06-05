"""Render the Op 2 v01 review artifact: the doctrine text + 3 example flows.

Usage:
    python scripts/operator_02_preview.py --out tmp/operator-02-v01.html

What it shows:
  1. The proposed doctrine text (the section that lands in the org CLAUDE.md)
  2. Three mock-rendered flows:
     A. Substantive prompt, no ticket reference -> ticket creation flow
     B. Substantive prompt with existing BC URL -> reuse existing ticket
     C. --no-ticket override -> bypass + log
  3. Code samples of the 3 helper modules + a write of session-state.json
  4. Open questions for Ali to decide before v01 ships
"""

from __future__ import annotations

import argparse
import html
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from execution.products.library import (  # noqa: E402
    session_state,
    ticket_creation_flow,
)

DOCTRINE_TEXT_PATH = REPO_ROOT / "docs" / "specs" / "operator-02-doctrine-text.md"


def render_flow_a() -> tuple[str, str]:
    """Flow A: substantive prompt, no ticket reference, ticket creation flow."""
    prompt = "Build a retry loop for the lead router so we stop dropping payloads on timeout."
    classification = ticket_creation_flow.classify_prompt(prompt)
    title = ticket_creation_flow.derive_proposed_title(prompt)
    confirm_msg = ticket_creation_flow.render_confirmation_message(title)

    description = (
        f"<p><strong>Prompt:</strong> &quot;{html.escape(prompt)}&quot;</p>"
        f"<p><strong>Classification:</strong> <code>{classification.kind}</code> "
        f"(matched: {html.escape(classification.matched_signal)})</p>"
        f"<p><strong>Derived title:</strong> <code>{html.escape(title)}</code></p>"
        f"<p><strong>Claude responds with:</strong></p>"
        f"<pre style='background:#1a202c;color:#cbd5e0;padding:14px;border-radius:6px;"
        f"font-family:Consolas,monospace;font-size:12.5px;white-space:pre-wrap;'>"
        f"{html.escape(confirm_msg)}</pre>"
        f"<p><strong>After user confirms:</strong> Claude calls "
        f"<code>ticket_creation_flow.create_ticket_for_session()</code>, gets back "
        f"a BC todo, writes <code>.claude/session-state.json</code>, proceeds with the work.</p>"
    )
    return "A. Substantive prompt, no ticket reference (creates ticket)", description


def render_flow_b() -> tuple[str, str]:
    """Flow B: substantive prompt with existing BC URL -> reuse."""
    prompt = (
        "Continue work on https://app.basecamp.com/3945211/buckets/7463955/todos/9967247766 -- "
        "we left off on the DEGRADED enterprise.colaberry.com fetch question."
    )
    classification = ticket_creation_flow.classify_prompt(prompt)

    description = (
        f"<p><strong>Prompt:</strong> &quot;{html.escape(prompt)}&quot;</p>"
        f"<p><strong>Classification:</strong> <code>{classification.kind}</code> "
        f"(matched: {html.escape(classification.matched_signal or '')})</p>"
        f"<p><strong>Existing ticket reference detected:</strong></p>"
        f"<pre style='background:#f7fafc;border:1px solid #e2e8f0;padding:10px;"
        f"border-radius:4px;font-family:Consolas,monospace;font-size:12px;'>"
        f"{html.escape(repr(classification.existing_ticket_ref))}</pre>"
        f"<p><strong>Claude's action:</strong> No ticket creation. Calls "
        f"<code>ticket_creation_flow.fetch_existing_ticket()</code> to load context, "
        f"writes <code>.claude/session-state.json</code> with this todo as the active "
        f"ticket, then proceeds. No user prompt needed -- the ticket is already chosen.</p>"
    )
    return "B. Substantive prompt with existing BC URL (reuses ticket)", description


def render_flow_c() -> tuple[str, str]:
    """Flow C: --no-ticket override -> bypass."""
    prompt = "--no-ticket grep the repo for all places we reference openclawAuthorityContentAgent"
    classification = ticket_creation_flow.classify_prompt(prompt)

    # Show the resulting session-state.json
    state = session_state.SessionState(session_id="CC-20260605-4w8q")
    state.set_bypass(reason="user_explicit_no_ticket_flag")
    state_json = state.to_dict()

    import json as _json
    description = (
        f"<p><strong>Prompt:</strong> &quot;{html.escape(prompt)}&quot;</p>"
        f"<p><strong>Classification:</strong> <code>{classification.kind}</code> "
        f"(matched: {html.escape(classification.matched_signal)})</p>"
        f"<p><strong>Claude's action:</strong> No ticket creation. Logs the bypass to "
        f"<code>.claude/session-state.json</code> with the reason, then proceeds with the "
        f"read-only grep. The bypass surfaces in the weekly audit so unused/over-used "
        f"overrides are visible.</p>"
        f"<p><strong>session-state.json after bypass:</strong></p>"
        f"<pre style='background:#1a202c;color:#cbd5e0;padding:14px;border-radius:6px;"
        f"font-family:Consolas,monospace;font-size:12px;white-space:pre-wrap;'>"
        f"{html.escape(_json.dumps(state_json, indent=2))}</pre>"
    )
    return "C. --no-ticket override (bypass + log)", description


def render_doctrine_section(doctrine_md: str) -> str:
    """Render the doctrine markdown as a styled HTML block."""
    return (
        f"<div style='border: 2px solid #1a365d; border-radius: 8px; padding: 0; overflow: hidden;'>"
        f"  <div style='background: #1a365d; color: #ffffff; padding: 12px 18px;'>"
        f"    <div style='font-size: 11px; text-transform: uppercase; letter-spacing: 1.5px; opacity: 0.85;'>"
        f"      Proposed text for the org CLAUDE.md"
        f"    </div>"
        f"    <div style='font-size: 17px; font-weight: 700; margin-top: 4px;'>"
        f"      Mandatory Ticket-Driven Work doctrine"
        f"    </div>"
        f"  </div>"
        f"  <pre style='margin: 0; padding: 18px; background: #ffffff; color: #1a202c; "
        f"      font-family: Consolas, monospace; font-size: 12.5px; line-height: 1.55; "
        f"      white-space: pre-wrap; word-wrap: break-word; max-height: 520px; overflow-y: auto;'>"
        f"{html.escape(doctrine_md)}</pre>"
        f"</div>"
    )


def render_html(generated_at_iso: str) -> str:
    flow_a_title, flow_a_html = render_flow_a()
    flow_b_title, flow_b_html = render_flow_b()
    flow_c_title, flow_c_html = render_flow_c()

    doctrine_md = DOCTRINE_TEXT_PATH.read_text(encoding="utf-8") if DOCTRINE_TEXT_PATH.exists() else "[doctrine file missing]"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Operator 2 v01 - Mandatory ticket doctrine review</title>
</head>
<body style="font-family: 'Aptos', Arial, sans-serif; color: #1a202c; background: #f7fafc; margin: 0; padding: 24px;">
<div style="max-width: 920px; margin: 0 auto;">

<div style="background: linear-gradient(135deg, #1a365d 0%, #2b6cb0 100%); color: #ffffff; padding: 24px 28px; border-radius: 10px; margin-bottom: 24px;">
  <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 1.5px; opacity: 0.85;">
    Operator 2 &middot; Review v01
  </div>
  <div style="font-size: 24px; font-weight: 700; margin-top: 6px;">Mandatory ticket-driven work doctrine</div>
  <div style="font-size: 13px; opacity: 0.9; margin-top: 8px;">
    The rule that every Claude Code session is anchored to exactly one BC ticket &middot;
    Generated {generated_at_iso}
  </div>
</div>

<div style="background: #ffffff; padding: 18px 22px; border-radius: 8px; margin-bottom: 22px; border-left: 4px solid #15803d;">
  <div style="font-weight: 700; color: #137333; margin-bottom: 6px;">What you are looking at</div>
  <div style="font-size: 14px; color: #2d3748;">
    Op 2 v01 has four parts: (1) the doctrine text that goes into the org CLAUDE.md, (2) three
    helper modules that implement the file format + the BC API calls + the prompt classifier,
    (3) three mock flows showing how each variant works in practice, (4) open questions for
    you to decide before this ships.
  </div>
  <div style="font-size: 13px; color: #4a5568; margin-top: 10px;">
    Reply on the email this came in to approve, request changes, or stop the loop.
  </div>
</div>

<h2 style="color: #1a365d; border-bottom: 2px solid #e2e8f0; padding-bottom: 8px;">1. The doctrine text</h2>

<p style="color: #4a5568; font-size: 14px;">
  This is the exact section that gets appended to the org CLAUDE.md (the one at
  <code>ColaberryIntern/AI_ProjectArchitect/CLAUDE.md</code>, distributed via Op 1's Layer 1).
  Nothing else in CLAUDE.md changes in this version.
</p>

{render_doctrine_section(doctrine_md)}

<h2 style="color: #1a365d; border-bottom: 2px solid #e2e8f0; padding-bottom: 8px; margin-top: 32px;">2. Three example flows</h2>

<p style="color: #4a5568; font-size: 14px;">
  Each flow shows a real prompt run through <code>ticket_creation_flow.classify_prompt()</code>
  (the actual code shipped in this v01) with the resulting classification + Claude's response.
</p>

<div style="margin: 18px 0; border: 1px solid #e2e8f0; border-radius: 8px; overflow: hidden;">
  <div style="background: #2b6cb0; color: #ffffff; padding: 10px 16px; font-weight: 700;">{flow_a_title}</div>
  <div style="padding: 14px 18px; background: #ffffff;">{flow_a_html}</div>
</div>

<div style="margin: 18px 0; border: 1px solid #e2e8f0; border-radius: 8px; overflow: hidden;">
  <div style="background: #5a32a3; color: #ffffff; padding: 10px 16px; font-weight: 700;">{flow_b_title}</div>
  <div style="padding: 14px 18px; background: #ffffff;">{flow_b_html}</div>
</div>

<div style="margin: 18px 0; border: 1px solid #e2e8f0; border-radius: 8px; overflow: hidden;">
  <div style="background: #4a5568; color: #ffffff; padding: 10px 16px; font-weight: 700;">{flow_c_title}</div>
  <div style="padding: 14px 18px; background: #ffffff;">{flow_c_html}</div>
</div>

<h2 style="color: #1a365d; border-bottom: 2px solid #e2e8f0; padding-bottom: 8px; margin-top: 32px;">3. Code shipped</h2>

<table style="border-collapse: collapse; width: 100%; font-size: 13px;">
  <thead>
    <tr style="background: #1a365d; color: #ffffff;">
      <th style="text-align: left; padding: 9px 12px;">Module</th>
      <th style="text-align: left; padding: 9px 12px;">Purpose</th>
    </tr>
  </thead>
  <tbody>
    <tr style="background: #f7fafc;"><td style="padding: 8px 12px; border-bottom: 1px solid #e2e8f0;"><code>execution/products/library/session_state.py</code></td><td style="padding: 8px 12px; border-bottom: 1px solid #e2e8f0;"><code>SessionState</code> dataclass + serialization to <code>.claude/session-state.json</code>. Mutually exclusive <code>active_ticket</code> vs <code>ticket_bypass</code>. Stdlib only.</td></tr>
    <tr><td style="padding: 8px 12px; border-bottom: 1px solid #e2e8f0;"><code>execution/products/library/personal_bc_provisioner.py</code></td><td style="padding: 8px 12px; border-bottom: 1px solid #e2e8f0;">Idempotent <code>provision_user_personal_bc()</code> that creates or reuses the user's personal BC project. Find-by-name dedup. Stdlib only.</td></tr>
    <tr style="background: #f7fafc;"><td style="padding: 8px 12px; border-bottom: 1px solid #e2e8f0;"><code>execution/products/library/ticket_creation_flow.py</code></td><td style="padding: 8px 12px; border-bottom: 1px solid #e2e8f0;"><code>classify_prompt()</code> + <code>derive_proposed_title()</code> + <code>create_ticket_for_session()</code> + <code>fetch_existing_ticket()</code>. The prompt-router and BC API client.</td></tr>
    <tr><td style="padding: 8px 12px;"><code>docs/specs/operator-02-doctrine-text.md</code></td><td style="padding: 8px 12px;">The exact text that lands in the org CLAUDE.md once you approve.</td></tr>
  </tbody>
</table>

<h2 style="color: #1a365d; border-bottom: 2px solid #e2e8f0; padding-bottom: 8px; margin-top: 32px;">4. Open questions before this ships</h2>

<div style="background: #fffbea; border-left: 4px solid #d4a017; padding: 14px 18px; border-radius: 4px; margin-top: 12px;">
  <ol style="margin: 0; padding-left: 22px; color: #2d3748; line-height: 1.7;">
    <li><strong>Personal BC project access grant:</strong> v01 creates the project but does NOT auto-add the user as a collaborator (requires resolving email -> BC person id which is a separate API call chain). v01 expects an admin to add the user from the BC UI after provisioning. v02 closes this loop. <strong>OK to ship as a 2-step?</strong></li>
    <li><strong>Substantive verb list:</strong> the classifier has ~30 hardcoded verbs (build, fix, deploy, send, etc.). Edge cases default to substantive (doctrine bias). <strong>Any specific verb you want added or removed?</strong></li>
    <li><strong>Confirmation gate:</strong> Claude asks "edit the title or reply confirm to proceed" before creating. Some workflows feel slower with this gate. <strong>Should the gate be opt-out (default = auto-create with derived title; user can edit after) or opt-in (current = always ask)?</strong></li>
    <li><strong>BC todolist for tickets:</strong> the doctrine creates tickets in the user's personal BC project but doesn't specify a todolist within it. v01 defaults to the first todolist. <strong>Should we name it something specific like "Claude Code Sessions"?</strong></li>
  </ol>
</div>

<div style="margin-top: 28px; padding-top: 14px; border-top: 1px solid #e2e8f0; font-size: 12px; color: #718096;">
  Spec: <code>docs/specs/operator-02-mandatory-ticket-doctrine.md</code> &middot;
  BC ticket: <code>9967247783</code> &middot;
  Session: CC-20260605-4w8q
</div>

</div>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="tmp/operator-02-v01.html")
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
