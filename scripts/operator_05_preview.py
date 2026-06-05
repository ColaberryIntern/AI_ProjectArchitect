"""Render the Op 5 v01 review artifact: operator memory + shared KB priority order.

Usage:
    python scripts/operator_05_preview.py --out tmp/operator-05-v01.html

What it shows:
  1. The 5-layer priority diagram (org > shared KB > tenant > per-user > learned memory)
  2. The starter OPERATOR_MEMORY.md template (what a freshly-provisioned user sees)
  3. A capture flow simulation: 3 real user prompts run through the detectors:
     - "I prefer black over autopep8 for Python formatting" -> stated_preference
     - "Don't use em-dashes in any outbound email" -> correction
     - 3x observation of "operator runs Playwright smoke after deploy" -> promotion
  4. The same memory file after the 3 captures land + the promotion fires
"""

from __future__ import annotations

import argparse
import html as html_lib
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from execution.products.library import operator_memory, operator_scaffold  # noqa: E402


def run_capture_simulation() -> tuple[str, list[dict]]:
    """Build a temp workspace, run 3 captures, return the resulting memory + manifest log."""
    tmpdir = Path(tempfile.mkdtemp(prefix="op5-preview-"))
    operator_scaffold.seed_workspace(
        workspace_dir=tmpdir,
        user_email="karun@colaberry.com",
        user_display_name="Karun (test)",
        tenant_id="colaberry",
        overwrite=True,
    )

    log = []

    # 1. Stated preference
    pref_prompt = "I prefer black over autopep8 for Python formatting"
    sig1 = operator_memory.detect_stated_preference(pref_prompt)
    if sig1:
        result = operator_memory.append_memory_entry(tmpdir, sig1)
        log.append({"prompt": pref_prompt, "signal": sig1.__dict__, "result": result})

    # 2. Correction
    corr_prompt = "Don't use em-dashes in any outbound email"
    sig2 = operator_memory.detect_correction(corr_prompt)
    if sig2:
        result = operator_memory.append_memory_entry(tmpdir, sig2)
        log.append({"prompt": corr_prompt, "signal": sig2.__dict__, "result": result})

    # 3. Three observations of the same behavior, then promotion
    behavior = "operator runs Playwright smoke check after deploy"
    for i in range(3):
        obs_sig = operator_memory.detect_pattern_observation(behavior)
        # Each captured observation is timestamped slightly differently to satisfy idempotency.
        # In real life the timestamp changes per day so the raw_text drifts naturally; here we
        # nudge each one to be unique.
        obs_sig.raw_text = f"{behavior} (session #{i+1})"
        obs_sig.summary = obs_sig.raw_text
        result = operator_memory.append_memory_entry(tmpdir, obs_sig)
        log.append({"prompt": f"<observation #{i+1}>", "signal": obs_sig.__dict__, "result": result})

    promotion = operator_memory.promote_pattern_if_observed(tmpdir, behavior)
    log.append({"prompt": "<promotion check>", "signal": None, "result": promotion})

    final_memory = (tmpdir / "OPERATOR_MEMORY.md").read_text(encoding="utf-8")
    return final_memory, log


def render_priority_diagram() -> str:
    rows = [
        ("Layer 1", "Org CLAUDE.md", "raw.githubusercontent.com/ColaberryIntern/AI_ProjectArchitect/main/CLAUDE.md (1h TTL)", "Admin-controlled - absolute", "#1a365d"),
        ("Layer 2", "Shared KB", "www.colaberry.com + www.colaberry.ai + www.enterprise.colaberry.com (24h TTL scrape)", "Admin-controlled - narrative", "#2b6cb0"),
        ("Layer 3", "Tenant CLAUDE.md", ".claude/tenant/CLAUDE.md (optional)", "Tenant admin - their policy", "#5a32a3"),
        ("Layer 4", "Per-user CLAUDE.md", "<workspace>/CLAUDE.md", "User-editable - preferences", "#4a5568"),
        ("Layer 5", "OPERATOR_MEMORY.md", "<workspace>/OPERATOR_MEMORY.md", "Claude-managed (learned) - lowest", "#1a202c"),
    ]
    body = ""
    for label, name, src, who, color in rows:
        body += (
            f'<tr>'
            f'<td style="padding: 10px 14px; background: {color}; color: white; font-weight: 700; width: 90px;">{label}</td>'
            f'<td style="padding: 10px 14px; border-bottom: 1px solid #e2e8f0;"><strong>{html_lib.escape(name)}</strong><br /><span style="font-size: 12px; color: #4a5568;">{html_lib.escape(src)}</span></td>'
            f'<td style="padding: 10px 14px; border-bottom: 1px solid #e2e8f0; font-size: 13px; color: #2d3748;">{html_lib.escape(who)}</td>'
            f'</tr>'
        )
    return f'<table style="border-collapse: collapse; width: 100%; margin-top: 12px;">{body}</table>'


def render_html(generated_at_iso: str) -> str:
    final_memory, log = run_capture_simulation()

    log_rows = ""
    for entry in log:
        log_rows += (
            f'<tr>'
            f'<td style="padding: 8px 12px; border-bottom: 1px solid #e2e8f0; font-family: Consolas, monospace; font-size: 12px;">{html_lib.escape(entry["prompt"][:80])}</td>'
            f'<td style="padding: 8px 12px; border-bottom: 1px solid #e2e8f0; font-size: 12px;">'
        )
        if entry["signal"]:
            log_rows += f'<code>{html_lib.escape(entry["signal"].get("kind", ""))}</code>'
        else:
            log_rows += '<em>(no signal)</em>'
        log_rows += (
            f'</td>'
            f'<td style="padding: 8px 12px; border-bottom: 1px solid #e2e8f0; font-size: 12px;">'
            f'<code>{html_lib.escape(str(entry["result"].get("action", "?")))}</code>'
        )
        if entry["result"].get("section"):
            log_rows += f' &middot; {html_lib.escape(entry["result"]["section"])}'
        if entry["result"].get("count") is not None:
            log_rows += f' &middot; count={entry["result"]["count"]}'
        log_rows += '</td></tr>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8" /><title>Operator 5 v01 - Memory + shared KB</title></head>
<body style="font-family: 'Aptos', Arial, sans-serif; color: #1a202c; background: #f7fafc; margin: 0; padding: 24px;">
<div style="max-width: 960px; margin: 0 auto;">

<div style="background: linear-gradient(135deg, #1a365d 0%, #2b6cb0 100%); color: #ffffff; padding: 24px 28px; border-radius: 10px; margin-bottom: 24px;">
  <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 1.5px; opacity: 0.85;">Operator 5 &middot; Review v01 &middot; LAST CHILD SPEC</div>
  <div style="font-size: 24px; font-weight: 700; margin-top: 6px;">Operator memory + shared KB</div>
  <div style="font-size: 13px; opacity: 0.9; margin-top: 8px;">5-layer priority &middot; per-user learning never overrides admin policy &middot; Generated {generated_at_iso}</div>
</div>

<div style="background: #ffffff; padding: 18px 22px; border-radius: 8px; margin-bottom: 22px; border-left: 4px solid #15803d;">
  <div style="font-weight: 700; color: #137333; margin-bottom: 6px;">What this ships (and what it does NOT do)</div>
  <div style="font-size: 14px; color: #2d3748;">
    <strong>Two rails.</strong> Rail 1 = shared KB (admin-controlled, already implemented in Op 1's <code>scrape_colaberry_knowledge()</code> as Layer 2). Rail 2 = per-user learned memory in <code>OPERATOR_MEMORY.md</code> at Layer 5 (lowest priority -- never overrides anything above). Op 5 adds the capture logic + the OPERATOR_MEMORY.md template + extends <code>seed_workspace()</code> to write it at provisioning.
  </div>
  <div style="font-size: 14px; color: #2d3748; margin-top: 10px;">
    <strong>What it does NOT do:</strong> override org policy (Layer 1). Override shared KB (Layer 2). Override your per-user CLAUDE.md (Layer 4). Cross-operator memory sharing. Memory expiration. The "control the narrative" rule is preserved structurally -- admin can always edit the GitHub raw CLAUDE.md or the colaberry.com sites and every operator's Claude Code picks it up on next session.
  </div>
</div>

<h2 style="color: #1a365d; border-bottom: 2px solid #e2e8f0; padding-bottom: 8px;">1. The 5-layer priority order</h2>

<p style="color: #4a5568; font-size: 14px;">
  This is the assembled-context table Claude Code reads at session start. Layer 1 wins on conflict. Operator memory at Layer 5 is the lowest priority -- it shapes defaults and tailors style, never overrides explicit policy.
</p>

{render_priority_diagram()}

<h2 style="color: #1a365d; border-bottom: 2px solid #e2e8f0; padding-bottom: 8px; margin-top: 32px;">2. Capture flow simulation (run against the real code)</h2>

<p style="color: #4a5568; font-size: 14px;">
  Three prompts run through the actual <code>detect_stated_preference()</code>, <code>detect_correction()</code>, and <code>detect_pattern_observation()</code> functions. The third pattern (Playwright smoke after deploy) is observed 3 times and then promoted to the Recurring patterns section.
</p>

<table style="border-collapse: collapse; width: 100%; margin-top: 8px;">
  <thead><tr style="background: #1a365d; color: #ffffff;">
    <th style="text-align: left; padding: 9px 12px;">Prompt / observation</th>
    <th style="text-align: left; padding: 9px 12px;">Signal kind</th>
    <th style="text-align: left; padding: 9px 12px;">Result</th>
  </tr></thead>
  <tbody>{log_rows}</tbody>
</table>

<h2 style="color: #1a365d; border-bottom: 2px solid #e2e8f0; padding-bottom: 8px; margin-top: 32px;">3. Resulting OPERATOR_MEMORY.md (post-capture)</h2>

<p style="color: #4a5568; font-size: 14px;">
  This is the actual file written by the capture sim. Note the stated preference + correction landed in their sections, the 3 observations landed in Open observations, and the promotion line landed in Recurring patterns.
</p>

<pre style="background: #1a202c; color: #cbd5e0; padding: 18px; border-radius: 6px; font-family: 'Consolas', monospace; font-size: 12px; line-height: 1.55; white-space: pre-wrap; word-wrap: break-word; max-height: 520px; overflow-y: auto;">{html_lib.escape(final_memory)}</pre>

<h2 style="color: #1a365d; border-bottom: 2px solid #e2e8f0; padding-bottom: 8px; margin-top: 32px;">4. Code shipped</h2>

<table style="border-collapse: collapse; width: 100%; font-size: 13px;">
  <thead><tr style="background: #1a365d; color: #ffffff;">
    <th style="text-align: left; padding: 9px 12px;">File</th>
    <th style="text-align: left; padding: 9px 12px;">What it is</th>
  </tr></thead>
  <tbody>
    <tr style="background: #f7fafc;"><td style="padding: 8px 12px;"><code>execution/products/library/operator_memory.py</code></td><td style="padding: 8px 12px;"><code>render_starter_operator_memory()</code> + 3 detectors (<code>detect_stated_preference</code>, <code>detect_correction</code>, <code>detect_pattern_observation</code>) + <code>append_memory_entry</code> (idempotent) + <code>promote_pattern_if_observed</code> (3-occurrence threshold). Stdlib only.</td></tr>
    <tr><td style="padding: 8px 12px;"><code>execution/products/library/operator_scaffold.py</code></td><td style="padding: 8px 12px;">EXTENDED &mdash; <code>seed_workspace()</code> now also writes <code>OPERATOR_MEMORY.md</code> at provisioning.</td></tr>
    <tr style="background: #f7fafc;"><td style="padding: 8px 12px;"><code>scripts/operator_05_preview.py</code></td><td style="padding: 8px 12px;">This preview. Runs a real 3-event capture simulation + promotion against the actual code.</td></tr>
  </tbody>
</table>

<h2 style="color: #1a365d; border-bottom: 2px solid #e2e8f0; padding-bottom: 8px; margin-top: 32px;">5. Once this ships</h2>

<div style="background: #e6f4ea; border-left: 4px solid #15803d; padding: 14px 18px; border-radius: 4px;">
  <strong style="color: #137333;">Kickoff completes.</strong> Op 5 is the last child spec. Once you approve and it auto-closes via the Op 4 confidence gate (>= 0.85), the Op 0 parent auto-closes too. All 6 BC tickets in the kickoff list close. The per-operator experience layer is built and the foundation work from PR #1 (auth, library, workflow, admin, provisioning) finally has its human-facing rails.
</div>

<div style="margin-top: 28px; padding-top: 14px; border-top: 1px solid #e2e8f0; font-size: 12px; color: #718096;">
  Spec: <code>docs/specs/operator-05-operator-memory-system.md</code> &middot;
  BC ticket: <code>9967247849</code> &middot;
  Session: CC-20260605-4w8q
</div>

</div>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="tmp/operator-05-v01.html")
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
