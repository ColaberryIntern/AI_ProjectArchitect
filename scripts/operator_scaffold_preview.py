"""Render the Op 1 v01 review artifact: a styled HTML preview of the 4-layer context.

Usage:
    python scripts/operator_scaffold_preview.py \
        --user-email test.user@example.com \
        --display-name "Test User" \
        --tenant-id colaberry \
        --workspace-dir tmp/op1-test-workspace \
        --out tmp/operator-01-v01.html

What it does:
  1. Seeds a fresh test workspace via operator_scaffold.seed_workspace()
  2. Calls operator_scaffold.assemble_context() to fetch + scrape + concatenate the 4 layers
  3. Renders the assembled context as a styled HTML document grouped by layer with
     priority banners, freshness indicators, and source links
  4. Writes the HTML to the --out path (default tmp/operator-01-v01.html)
  5. Prints a summary line with per-layer status to stdout

The HTML is the visual artifact Ali reviews via the email-review loop. It demonstrates:
  - The 4 layers visually distinct (color-coded headers per layer)
  - Each layer's source URL + fetched-at timestamp + ok/degraded indicator
  - The full concatenated markdown that Claude Code will see at session start
"""

from __future__ import annotations

import argparse
import html
import sys
import time
from pathlib import Path

# Add repo root to sys.path so we can import execution.products.library
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from execution.products.library import operator_scaffold  # noqa: E402


LAYER_PALETTE = {
    "Layer 1 (highest priority)": {"bg": "#1a365d", "fg": "#ffffff", "accent": "#d4a017"},
    "Layer 2":                    {"bg": "#2b6cb0", "fg": "#ffffff", "accent": "#9ae6b4"},
    "Layer 3":                    {"bg": "#5a32a3", "fg": "#ffffff", "accent": "#fbd38d"},
    "Layer 4 (lowest priority)":  {"bg": "#4a5568", "fg": "#ffffff", "accent": "#cbd5e0"},
    "Layer 5 (lowest priority - never overrides anything above)": {
        "bg": "#1a202c", "fg": "#ffffff", "accent": "#a0aec0",
    },
}


def render_html(ctx: operator_scaffold.AssembledContext, generated_at_iso: str) -> str:
    """Render the assembled context as a styled HTML page (for email embedding + standalone viewing)."""
    layer_cards = []
    for layer in ctx.layers:
        palette = LAYER_PALETTE.get(layer.priority_label, {"bg": "#2d3748", "fg": "#ffffff", "accent": "#cbd5e0"})
        ok_badge = (
            '<span style="background:#15803d;color:#ffffff;padding:3px 9px;border-radius:11px;'
            'font-size:11px;font-weight:600;">FRESH</span>'
            if layer.ok else
            '<span style="background:#b91c1c;color:#ffffff;padding:3px 9px;border-radius:11px;'
            'font-size:11px;font-weight:600;">DEGRADED</span>'
        )
        fetched_str = (
            time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(layer.fetched_at))
            if layer.fetched_at else "(no fetch)"
        )
        body_preview = html.escape(layer.body)
        if len(body_preview) > 3200:
            body_preview = body_preview[:3200] + "\n\n[truncated for preview; full body in the assembled context]"

        layer_cards.append(f"""
<div style="margin: 22px 0; border: 1px solid #e2e8f0; border-radius: 8px; overflow: hidden;">
  <div style="background: {palette['bg']}; color: {palette['fg']}; padding: 14px 18px;">
    <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 1.5px; opacity: 0.85;">
      {html.escape(layer.priority_label)}
    </div>
    <div style="font-size: 17px; font-weight: 700; margin-top: 4px;">{html.escape(layer.name)}</div>
    <div style="font-size: 12px; opacity: 0.85; margin-top: 6px;">
      {ok_badge}
      &middot; Fetched: {fetched_str}
      &middot; Source: <span style="font-family: 'Consolas', monospace;">{html.escape(layer.source)}</span>
    </div>
  </div>
  <pre style="margin: 0; padding: 16px 18px; background: #ffffff; color: #1a202c; font-family: 'Consolas', 'Courier New', monospace; font-size: 12.5px; line-height: 1.5; white-space: pre-wrap; word-wrap: break-word; max-height: 420px; overflow-y: auto;">{body_preview}</pre>
</div>""")

    layers_html = "\n".join(layer_cards)

    warnings_html = ""
    if ctx.warnings:
        warning_items = "\n".join(
            f'<li style="margin: 4px 0;">{html.escape(w)}</li>' for w in ctx.warnings
        )
        warnings_html = f"""
<div style="margin: 18px 0; padding: 14px 18px; background: #fffbea; border-left: 4px solid #d4a017; border-radius: 4px;">
  <div style="font-weight: 700; color: #7d4e00; margin-bottom: 6px;">Warnings ({len(ctx.warnings)}):</div>
  <ul style="margin: 0; padding-left: 20px; color: #2d3748;">{warning_items}</ul>
</div>"""

    full_md = ctx.as_concatenated_markdown()
    full_md_chars = len(full_md)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Operator 1 v01 - Assembled context preview</title>
</head>
<body style="font-family: 'Aptos', Arial, sans-serif; color: #1a202c; background: #f7fafc; margin: 0; padding: 24px;">
<div style="max-width: 920px; margin: 0 auto;">

<div style="background: linear-gradient(135deg, #1a365d 0%, #2b6cb0 100%); color: #ffffff; padding: 24px 28px; border-radius: 10px; margin-bottom: 24px;">
  <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 1.5px; opacity: 0.85;">
    Operator 1 &middot; Review v01
  </div>
  <div style="font-size: 24px; font-weight: 700; margin-top: 6px;">Assembled 4-layer context preview</div>
  <div style="font-size: 13px; opacity: 0.9; margin-top: 8px;">
    For: <strong>{html.escape(ctx.user_display_name)}</strong> &middot;
    Email: <code>{html.escape(ctx.user_email)}</code> &middot;
    Tenant: <code>{html.escape(ctx.tenant_id or 'unassigned')}</code><br />
    Assembled at: {generated_at_iso} &middot; Total: {full_md_chars:,} chars across {len(ctx.layers)} layer{'s' if len(ctx.layers) != 1 else ''}
  </div>
</div>

<div style="background: #ffffff; padding: 18px 22px; border-radius: 8px; margin-bottom: 22px; border-left: 4px solid #15803d;">
  <div style="font-weight: 700; color: #137333; margin-bottom: 6px;">What you are looking at</div>
  <div style="font-size: 14px; color: #2d3748;">
    This is the visual review artifact for <strong>Operator 1 v01</strong> (per-user CLAUDE.md +
    PROGRESS.md scaffold). Below is the full assembled context that Claude Code will surface
    at session start when {html.escape(ctx.user_display_name)} opens a session.
    Each layer is shown in priority order with its source URL and freshness state.
    Higher-priority layers (Layer 1 first) win on conflict.
  </div>
  <div style="font-size: 13px; color: #4a5568; margin-top: 10px;">
    Reply on the email this came in to approve, request changes, or stop the loop.
  </div>
</div>

{warnings_html}

{layers_html}

<details style="margin-top: 26px;">
  <summary style="cursor: pointer; font-weight: 600; color: #2b6cb0; padding: 8px 0;">
    Show the full concatenated context as plain text ({full_md_chars:,} chars)
  </summary>
  <pre style="background: #1a202c; color: #cbd5e0; padding: 18px; border-radius: 6px; margin-top: 8px; font-family: 'Consolas', monospace; font-size: 12px; line-height: 1.5; white-space: pre-wrap; overflow-x: auto;">{html.escape(full_md)}</pre>
</details>

<div style="margin-top: 32px; padding-top: 16px; border-top: 1px solid #e2e8f0; font-size: 12px; color: #718096;">
  Generated by <code>scripts/operator_scaffold_preview.py</code> on {generated_at_iso}.<br />
  Spec: <code>docs/specs/operator-01-per-user-scaffold.md</code> &middot;
  BC ticket: <code>9967247766</code> &middot;
  Module: <code>execution/products/library/operator_scaffold.py</code>
</div>

</div>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description="Render the Op 1 v01 assembled context preview.")
    parser.add_argument("--user-email", default="test.user@colaberry.com")
    parser.add_argument("--display-name", default="Test User")
    parser.add_argument("--tenant-id", default="colaberry")
    parser.add_argument("--workspace-dir", default="tmp/op1-test-workspace")
    parser.add_argument("--out", default="tmp/operator-01-v01.html")
    parser.add_argument("--org-local-fallback", default="CLAUDE.md",
                        help="Local file to use as Layer 1 if the GitHub raw URL fails. Defaults to repo root CLAUDE.md.")
    args = parser.parse_args()

    workspace = Path(args.workspace_dir)
    workspace.mkdir(parents=True, exist_ok=True)

    # Seed the test workspace (per-user CLAUDE.md + PROGRESS.md + .claude/ scaffolding)
    seed_manifest = operator_scaffold.seed_workspace(
        workspace_dir=workspace,
        user_email=args.user_email,
        user_display_name=args.display_name,
        tenant_id=args.tenant_id,
        overwrite=True,
    )
    print(f"[seed] wrote {len(seed_manifest['written'])} files into {workspace}")
    for path in seed_manifest['written']:
        print(f"  + {path}")

    # Assemble the 4-layer context
    org_fallback = Path(args.org_local_fallback) if args.org_local_fallback else None
    ctx = operator_scaffold.assemble_context(
        user_email=args.user_email,
        user_display_name=args.display_name,
        workspace_dir=workspace,
        tenant_id=args.tenant_id,
        org_local_fallback=org_fallback,
    )

    # Summary line
    layer_summary = ", ".join(
        f"{layer.priority_label.split(' ')[0]}-{layer.priority_label.split(' ')[1]}={'OK' if layer.ok else 'DEGRADED'}"
        for layer in ctx.layers
    )
    print(f"[assemble] {len(ctx.layers)} layers: {layer_summary}")
    if ctx.warnings:
        print(f"[warnings] {len(ctx.warnings)}:")
        for w in ctx.warnings:
            print(f"  ! {w}")

    # Render HTML
    generated_at = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    html_doc = render_html(ctx, generated_at_iso=generated_at)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_doc, encoding="utf-8")
    print(f"[render] {len(html_doc):,} bytes -> {out_path}")
    print(f"RESULT_JSON:{{\"layers\":{len(ctx.layers)},\"warnings\":{len(ctx.warnings)},\"out\":\"{out_path}\",\"chars\":{len(html_doc)}}}")


if __name__ == "__main__":
    main()
