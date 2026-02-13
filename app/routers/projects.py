"""Project management routes: list, create, dashboard."""

from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.dependencies import (
    PHASE_URLS,
    get_phase_info,
    get_project_state,
    list_projects,
)
from execution.state_manager import delete_project, initialize_state, save_state

router = APIRouter()


@router.get("/")
async def index(request: Request):
    """Landing page: list all projects + create button."""
    projects = list_projects()
    return request.app.state.templates.TemplateResponse(
        request, "index.html", {"projects": projects},
    )


@router.post("/projects/new")
async def create_project(request: Request, project_name: str = Form(...)):
    """Create a new project and redirect to idea intake."""
    state = initialize_state(project_name)
    slug = state["project"]["slug"]
    return RedirectResponse(
        url=f"/projects/{slug}/idea-intake", status_code=303
    )


@router.post("/projects/delete-all")
async def delete_all_projects_route(request: Request):
    """Delete all projects and redirect to the project list."""
    projects = list_projects()
    errors = []
    deleted_count = 0
    for p in projects:
        try:
            result = delete_project(p["slug"])
            if result:
                deleted_count += 1
        except (OSError, ValueError) as e:
            errors.append(f"{p['name']}: {e}")

    if errors:
        error_msg = quote(
            f"Deleted {deleted_count} projects. "
            f"Failed: {'; '.join(errors)}"
        )
        return RedirectResponse(url=f"/?error={error_msg}", status_code=303)
    return RedirectResponse(url="/", status_code=303)


@router.post("/projects/{slug}/delete")
async def delete_project_route(request: Request, slug: str):
    """Delete a project and redirect to the project list."""
    try:
        deleted = delete_project(slug)
    except OSError as e:
        return RedirectResponse(
            url=f"/?error={quote(str(e))}", status_code=303
        )
    if not deleted:
        raise HTTPException(status_code=404, detail="Project not found")
    return RedirectResponse(url="/", status_code=303)


@router.get("/projects/{slug}/guided-ideation")
async def guided_ideation_gone(request: Request, slug: str):
    """Guided Ideation was removed. Redirect to the project's current phase."""
    return RedirectResponse(url=f"/projects/{slug}", status_code=302)


@router.get("/projects/{slug}")
async def project_dashboard(request: Request, slug: str):
    """Redirect to the current phase page for this project."""
    state = get_project_state(slug)
    phase = state["current_phase"]
    if phase not in PHASE_URLS:
        # Auto-migrate: deprecated phase → nearest valid phase
        if state.get("idea", {}).get("original_raw"):
            phase = "feature_discovery"
        else:
            phase = "idea_intake"
        state["current_phase"] = phase
        save_state(state, slug)
    url_segment = PHASE_URLS[phase]
    return RedirectResponse(
        url=f"/projects/{slug}/{url_segment}", status_code=302
    )
