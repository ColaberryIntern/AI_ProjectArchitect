"""Phase 5: Outline Approval routes."""

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.dependencies import get_phase_info, get_project_state
from execution.build_depth import get_all_depth_modes
from execution.state_manager import (
    advance_phase,
    get_build_depth_mode,
    lock_outline,
    save_state,
    set_build_depth_mode,
    unlock_outline,
)

router = APIRouter()


@router.get("/outline-approval")
async def outline_approval_page(request: Request, slug: str):
    """Show outline for approval."""
    state = get_project_state(slug)
    phase_info = get_phase_info(state)
    error = request.query_params.get("error")
    depth_modes = get_all_depth_modes()
    current_depth = get_build_depth_mode(state)
    return request.app.state.templates.TemplateResponse(
        request, "project/outline_approval.html",
        {
            "state": state, "slug": slug, "phase_info": phase_info,
            "error": error, "depth_modes": depth_modes,
            "current_depth": current_depth,
        },
    )


@router.post("/outline-approval/lock")
async def lock_outline_route(request: Request, slug: str):
    """Lock outline and advance to chapter build."""
    state = get_project_state(slug)
    if state["current_phase"] != "outline_approval":
        return RedirectResponse(url=f"/projects/{slug}", status_code=303)
    lock_outline(state)
    advance_phase(state, "chapter_build")
    save_state(state, slug)
    return RedirectResponse(
        url=f"/projects/{slug}/auto-build", status_code=303
    )


@router.post("/outline-approval/depth")
async def set_depth_mode_route(
    request: Request, slug: str, depth_mode: str = Form(...)
):
    """Set the build depth mode for the project."""
    state = get_project_state(slug)
    try:
        set_build_depth_mode(state, depth_mode)
        save_state(state, slug)
    except ValueError:
        pass  # Ignore invalid mode, redirect back without changes
    return RedirectResponse(
        url=f"/projects/{slug}/outline-approval", status_code=303
    )


@router.post("/outline-approval/unlock")
async def unlock_outline_route(
    request: Request, slug: str, reason: str = Form(...)
):
    """Unlock outline for revisions."""
    state = get_project_state(slug)
    unlock_outline(state, reason)
    save_state(state, slug)
    return RedirectResponse(
        url=f"/projects/{slug}/outline-approval", status_code=303
    )
