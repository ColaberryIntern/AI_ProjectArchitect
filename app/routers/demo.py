"""Demo generation and viewing routes."""

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.dependencies import get_project_state
from execution.state_manager import save_state

router = APIRouter()


@router.post("/generate-demo")
async def generate_demo(request: Request, slug: str):
    """Generate demo config and redirect to demo page."""
    from execution.demo.demo_generator import generate_demo_config

    state = get_project_state(slug)
    config = generate_demo_config(state)
    state["demo"] = config
    save_state(state, slug)
    return RedirectResponse(url=f"/projects/{slug}/demo", status_code=303)


@router.get("/demo")
async def demo_page(request: Request, slug: str):
    """Render the interactive demo experience."""
    from execution.demo.demo_generator import generate_demo_config

    state = get_project_state(slug)

    # Auto-generate if not cached
    if not state.get("demo"):
        config = generate_demo_config(state)
        state["demo"] = config
        save_state(state, slug)

    demo = state["demo"]
    share = request.query_params.get("share") == "true"

    return request.app.state.templates.TemplateResponse(
        request, "project/demo.html",
        {
            "state": state,
            "slug": slug,
            "demo": demo,
            "demo_json": json.dumps(demo),
            "share_mode": share,
            "advisory_session_id": (state.get("advisory") or {}).get("advisory_session_id", ""),
        },
    )


@router.get("/api/demo/config")
async def demo_config_api(request: Request, slug: str):
    """Return demo config as JSON (for AJAX refresh)."""
    state = get_project_state(slug)
    demo = state.get("demo")
    if not demo:
        from execution.demo.demo_generator import generate_demo_config
        demo = generate_demo_config(state)
    return JSONResponse(content=demo)
