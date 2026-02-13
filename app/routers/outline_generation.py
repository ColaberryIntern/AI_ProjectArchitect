"""Phase 4: Outline Generation routes.

Profile-driven: uses project_profile for 10-section outlines,
falls back to idea-based 7-section for legacy projects.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.dependencies import get_phase_info, get_project_state
from execution.outline_generator import generate_outline, generate_outline_from_profile
from execution.outline_validator import run_all_checks
from execution.state_manager import (
    advance_phase,
    get_build_depth_mode,
    get_project_profile,
    is_profile_complete,
    save_state,
    set_outline_sections,
)

router = APIRouter()


@router.get("/outline-generation")
async def outline_generation_page(request: Request, slug: str):
    """Show outline section editor."""
    state = get_project_state(slug)
    phase_info = get_phase_info(state)

    # Auto-generate outline on first visit
    if not state["outline"]["sections"]:
        profile = get_project_profile(state)
        features = state.get("features", {}).get("core", [])
        depth_mode = get_build_depth_mode(state)
        if is_profile_complete(state):
            sections = generate_outline_from_profile(profile, features, depth_mode=depth_mode)
        else:
            idea = state.get("idea", {}).get("original_raw", "")
            sections = generate_outline(idea, features)
        set_outline_sections(state, sections)
        save_state(state, slug)

    error = request.query_params.get("error")
    return request.app.state.templates.TemplateResponse(
        request, "project/outline_generation.html",
        {"state": state, "slug": slug, "phase_info": phase_info, "error": error},
    )


@router.post("/outline-generation/sections")
async def save_sections(request: Request, slug: str):
    """Save outline sections from form data (dynamic count)."""
    state = get_project_state(slug)
    if state["current_phase"] != "outline_generation":
        return RedirectResponse(url=f"/projects/{slug}", status_code=303)

    form = await request.form()
    # Count from existing sections; fall back to counting form title keys
    section_count = len(state["outline"]["sections"])
    if section_count == 0:
        section_count = sum(1 for k in form if k.startswith("title_"))
    sections = []
    for i in range(1, section_count + 1):
        sections.append({
            "index": i,
            "title": form.get(f"title_{i}", ""),
            "type": "required",
            "summary": form.get(f"summary_{i}", ""),
        })

    set_outline_sections(state, sections)
    save_state(state, slug)
    return RedirectResponse(
        url=f"/projects/{slug}/outline-generation", status_code=303
    )


@router.get("/api/outline/validate")
async def validate_outline(request: Request, slug: str):
    """Run outline validation checks."""
    state = get_project_state(slug)
    sections = state["outline"]["sections"]
    result = run_all_checks(sections)
    return JSONResponse(content=result)


@router.post("/outline-generation/advance")
async def advance_to_approval(request: Request, slug: str):
    """Advance to outline approval."""
    state = get_project_state(slug)
    if state["current_phase"] != "outline_generation":
        return RedirectResponse(url=f"/projects/{slug}", status_code=303)
    advance_phase(state, "outline_approval")
    save_state(state, slug)
    return RedirectResponse(
        url=f"/projects/{slug}/outline-approval", status_code=303
    )
