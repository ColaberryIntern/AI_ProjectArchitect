"""Trust Command Center — read-only dashboard at /admin/trust (Phase 10 v1).

Super-admin gated (reuses app.routers.admin._require_super_admin). Renders a
self-contained HTML overview plus JSON twins for each view. All data comes from
execution.ops_platform.trust_center (read-only aggregator over existing stores);
no metric is fabricated — not-yet-instrumented signals are labeled as such.

Design: docs/trust-audit/dashboard-design.md
"""

from __future__ import annotations

import html

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.routers.admin import _require_super_admin
from execution.ops_platform import trust_center as tc

router = APIRouter(prefix="/admin/trust")


# ── JSON twins ──


@router.get("/overview.json")
async def overview_json(request: Request):
    _require_super_admin(request)
    return JSONResponse(tc.overview())


@router.get("/operations.json")
async def operations_json(request: Request):
    _require_super_admin(request)
    return JSONResponse(tc.operations())


@router.get("/governance.json")
async def governance_json(request: Request):
    _require_super_admin(request)
    return JSONResponse(tc.governance())


@router.get("/audit.json")
async def audit_json(request: Request, days: int = 7, limit: int = 50,
                     action: str | None = None):
    _require_super_admin(request)
    return JSONResponse(tc.audit_explorer(days=days, limit=limit, action=action))


@router.get("/snapshot.json")
async def snapshot_json(request: Request):
    _require_super_admin(request)
    return JSONResponse(tc.snapshot())


# ── HTML overview ──


def _bar(score: int) -> str:
    filled = max(0, min(20, round(score / 5)))
    color = "#16a34a" if score >= 80 else ("#ca8a04" if score >= 65 else "#dc2626")
    return (f"<span style='font-family:monospace'>{'█' * filled}{'░' * (20 - filled)}</span> "
            f"<b style='color:{color}'>{score}</b>")


def _render_html(data: dict) -> str:
    ts = data.get("trust_score", {})
    pillars = ts.get("pillars", {})
    comp = data.get("compliance") or {}
    counts = comp.get("counts", {}) if isinstance(comp, dict) else {}
    agents = (data.get("runtime_agents") or {}).get("agents", []) if isinstance(data.get("runtime_agents"), dict) else []
    audit = data.get("audit_7d") or {}
    by_action = audit.get("by_action", {}) if isinstance(audit, dict) else {}

    pillar_rows = "".join(
        f"<tr><td>{html.escape(k)}</td><td>{_bar(int(v))}</td></tr>"
        for k, v in pillars.items()
    )
    agent_rows = "".join(
        f"<tr><td>{html.escape(str(a.get('name','')))}</td>"
        f"<td><code>{html.escape(str(a.get('autonomy_policy','')))}</code></td>"
        f"<td>{html.escape(str(a.get('status','')))}</td></tr>"
        for a in agents
    ) or "<tr><td colspan=3>none</td></tr>"
    top_actions = sorted(by_action.items(), key=lambda kv: kv[1], reverse=True)[:8]
    action_rows = "".join(
        f"<tr><td><code>{html.escape(k)}</code></td><td>{v}</td></tr>" for k, v in top_actions
    ) or "<tr><td colspan=2>no audit rows in window</td></tr>"

    overall = ts.get("overall", "?")
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Trust Command Center</title>
<style>
 body{{font-family:Arial,Helvetica,sans-serif;margin:24px;color:#111;background:#fafafa}}
 h1{{margin:0 0 2px}} .sub{{color:#666;font-size:13px;margin-bottom:18px}}
 .grid{{display:flex;flex-wrap:wrap;gap:16px}}
 .card{{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:16px;min-width:320px;flex:1}}
 .score{{font-size:40px;font-weight:700}}
 table{{border-collapse:collapse;width:100%;font-size:13px}} td,th{{padding:4px 8px;text-align:left;border-bottom:1px solid #f0f0f0}}
 .badge{{display:inline-block;padding:2px 8px;border-radius:10px;font-size:12px}}
 .warn{{background:#fef3c7;color:#92400e}} .ok{{background:#dcfce7;color:#166534}}
 a{{color:#2563eb}} code{{background:#f6f8fa;padding:1px 4px;border-radius:4px}}
</style></head><body>
<h1>Trust Command Center <span class="badge warn">read-only v1</span></h1>
<div class="sub">Overall trust score is the {html.escape(str(ts.get('source','')))} value
 ({html.escape(str(ts.get('note','')))}). JSON twins:
 <a href="/admin/trust/snapshot.json">snapshot</a> ·
 <a href="/admin/trust/overview.json">overview</a> ·
 <a href="/admin/trust/operations.json">operations</a> ·
 <a href="/admin/trust/governance.json">governance</a> ·
 <a href="/admin/trust/audit.json">audit</a></div>
<div class="grid">
 <div class="card"><div>Overall Trust</div><div class="score">{overall}<span style="font-size:16px">/100</span></div>
   <table>{pillar_rows}</table></div>
 <div class="card"><div>TBI Compliance</div>
   <p>framework <code>{html.escape(str(comp.get('framework_version','?')))}</code> ·
   total {comp.get('total','?')}</p>
   <p><span class="badge ok">compliant {counts.get('compliant',0)}</span>
      <span class="badge warn">conditional {counts.get('conditional',0)}</span>
      <span class="badge" style="background:#fee2e2;color:#991b1b">non-compliant {counts.get('non_compliant',0)}</span></p>
   <div>Cost (7d): <span class="badge warn">not yet instrumented</span></div></div>
 <div class="card"><div>Runtime AI agents</div><table>
   <tr><th>name</th><th>policy</th><th>status</th></tr>{agent_rows}</table></div>
 <div class="card"><div>Audit activity (7d, top actions)</div><table>
   <tr><th>action</th><th>count</th></tr>{action_rows}</table></div>
</div>
<p class="sub">Full audit: <code>docs/trust-audit/TRUST_COMPLIANCE_REPORT.md</code></p>
</body></html>"""


@router.get("")
async def trust_home(request: Request):
    _require_super_admin(request)
    return HTMLResponse(_render_html(tc.overview()))
