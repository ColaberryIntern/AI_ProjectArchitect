"""Trust Command Center — flagship dashboard + autonomy controls at /admin/trust.

Super-admin gated (reuses app.routers.admin auth). The page renders a templated,
on-brand dashboard with a live 7-layer architecture diagram; the control endpoints
let an operator pause/kill/freeze/approve live AI autonomy. All reads come from the
read-only aggregator (execution.ops_platform.trust_center); all control mutations are
audited by the underlying ops modules and gated by _require_super_admin (always
enforced, independent of OPS_ENFORCE_RBAC).

Design: docs/trust-audit/dashboard-design.md
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.routers.admin import _ctx, _current_user, _require_super_admin
from execution.ops_platform import trust_center as tc

router = APIRouter(prefix="/admin/trust")


def _actor(request: Request) -> dict:
    u = _current_user(request)
    if u:
        return {"name": getattr(u, "email", "admin"),
                "email": getattr(u, "email", None),
                "roles": list(getattr(u, "roles", []) or [])}
    return {"name": "admin", "roles": ["admin"]}


async def _body(request: Request) -> dict:
    try:
        b = await request.json()
        return b if isinstance(b, dict) else {}
    except Exception:
        return {}


# ── HTML page ──


@router.get("")
async def trust_home(request: Request):
    _require_super_admin(request)
    return request.app.state.templates.TemplateResponse(
        request, "admin/trust_center.html",
        _ctx(request, page_title="Trust Command Center", data=tc.page_data()),
    )


# ── JSON read twins ──


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
async def audit_json(request: Request, days: int = 7, limit: int = 100,
                     action: str | None = None, actor: str | None = None,
                     entity_id: str | None = None):
    _require_super_admin(request)
    return JSONResponse(tc.audit_detail(days=days, limit=limit, action=action,
                                        actor=actor, entity_id=entity_id))


@router.get("/audit/replay.json")
async def audit_replay_json(request: Request, correlation_id: str):
    _require_super_admin(request)
    return JSONResponse(tc.audit_replay(correlation_id))


# ── Drill-down detail (read-only) ──


@router.get("/layer/{n}.json")
async def layer_json(request: Request, n: int):
    _require_super_admin(request)
    return JSONResponse(tc.layer_detail(n))


@router.get("/agent/{agent_id}.json")
async def agent_json(request: Request, agent_id: str):
    _require_super_admin(request)
    return JSONResponse(tc.agent_detail(agent_id))


@router.get("/compliance.json")
async def compliance_json(request: Request):
    _require_super_admin(request)
    return JSONResponse(tc.compliance_detail())


@router.get("/cost.json")
async def cost_json(request: Request):
    _require_super_admin(request)
    return JSONResponse(tc.cost_detail())


@router.get("/availability.json")
async def availability_json(request: Request):
    _require_super_admin(request)
    return JSONResponse(tc.availability())


@router.get("/lexicon.json")
async def lexicon_json(request: Request):
    _require_super_admin(request)
    return JSONResponse(tc.lexicon_detail())


@router.get("/scorecard.json")
async def scorecard_json(request: Request):
    _require_super_admin(request)
    return JSONResponse(tc.trust_scorecard())


@router.get("/layers.json")
async def layers_json(request: Request):
    _require_super_admin(request)
    return JSONResponse(tc.layers())


@router.get("/live.json")
async def live_json(request: Request):
    _require_super_admin(request)
    return JSONResponse(tc.live())


@router.get("/snapshot.json")
async def snapshot_json(request: Request):
    _require_super_admin(request)
    return JSONResponse(tc.snapshot())


# ── Control levers (super-admin gated; underlying calls are audited) ──


@router.post("/control/global/{action}")
async def control_global(request: Request, action: str):
    _require_super_admin(request)
    if action not in ("pause", "resume"):
        return JSONResponse({"error": "action must be pause|resume"}, status_code=400)
    from execution.ops_platform import runtime_controls
    body = await _body(request)
    state = runtime_controls.set_global_paused(
        action == "pause", actor=_actor(request),
        reason=body.get("reason", "Trust Center global kill-switch"))
    return JSONResponse({"ok": True, "state": state})


@router.post("/control/agent/{agent_id}/{action}")
async def control_agent(request: Request, agent_id: str, action: str):
    _require_super_admin(request)
    if action not in ("pause", "resume"):
        return JSONResponse({"error": "action must be pause|resume"}, status_code=400)
    from execution.ops_platform import runtime_controls
    body = await _body(request)
    state = runtime_controls.set_agent_paused(
        agent_id, action == "pause", actor=_actor(request),
        reason=body.get("reason", "Trust Center per-agent toggle"))
    return JSONResponse({"ok": True, "state": state})


@router.post("/control/freeze/{capability_id}")
async def control_freeze(request: Request, capability_id: str):
    _require_super_admin(request)
    from execution.ops_platform import controls
    body = await _body(request)
    ctrl = controls.freeze(capability_id, actor=_actor(request),
                           reason=body.get("reason", "Trust Center freeze"))
    return JSONResponse({"ok": True, "control_id": ctrl.control_id})


@router.post("/control/unfreeze/{capability_id}")
async def control_unfreeze(request: Request, capability_id: str):
    _require_super_admin(request)
    from execution.ops_platform import controls
    body = await _body(request)
    ok = controls.unfreeze(capability_id, actor=_actor(request),
                           reason=body.get("reason", "Trust Center unfreeze"))
    return JSONResponse({"ok": bool(ok)})


@router.post("/control/rollback/{capability_id}")
async def control_rollback(request: Request, capability_id: str):
    _require_super_admin(request)
    from execution.ops_platform import controls
    body = await _body(request)
    target = body.get("target_version_id")
    if not target:
        return JSONResponse({"error": "target_version_id required"}, status_code=400)
    result = controls.emergency_rollback(
        capability_id, target_version_id=target, actor=_actor(request),
        reason=body.get("reason", "Trust Center emergency rollback"))
    return JSONResponse({"ok": True, "result": result})


@router.post("/control/approval/{request_id}/decide")
async def control_approval(request: Request, request_id: str):
    _require_super_admin(request)
    from execution.ops_platform import approvals
    body = await _body(request)
    decision = body.get("decision")
    if decision not in ("approved", "rejected"):
        return JSONResponse({"error": "decision must be approved|rejected"}, status_code=400)
    req = approvals.submit_decision(
        request_id, approver=_actor(request), decision=decision,
        comment=body.get("comment", "via Trust Center"))
    return JSONResponse({"ok": True, "state": getattr(req, "state", None)})
