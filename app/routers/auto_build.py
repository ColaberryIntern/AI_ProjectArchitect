"""Auto-build routes: triggers and streams the automated chapter build pipeline."""

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse

from app.dependencies import get_phase_info, get_project_state
from execution.auto_builder import (
    clear_build_progress,
    get_build_progress,
    is_build_running,
    run_auto_build_sync,
)
from execution.build_depth import get_depth_config, get_scoring_thresholds
from execution.state_manager import get_build_depth_mode

router = APIRouter()


@router.get("/auto-build")
async def auto_build_page(request: Request, slug: str):
    """Show auto-build progress page."""
    state = get_project_state(slug)

    # If already complete, redirect to complete page
    if state["current_phase"] == "complete":
        return RedirectResponse(url=f"/projects/{slug}/complete", status_code=303)

    phase_info = get_phase_info(state)
    building = is_build_running(slug)
    depth_mode = get_build_depth_mode(state)
    depth_config = get_depth_config(depth_mode)
    thresholds = get_scoring_thresholds(depth_mode)
    return request.app.state.templates.TemplateResponse(
        request, "project/auto_build.html",
        {
            "state": state, "slug": slug, "phase_info": phase_info,
            "building": building,
            "depth_label": depth_config["label"],
            "depth_pages": depth_config["target_pages"],
            "incomplete_threshold": thresholds["incomplete_threshold"],
            "complete_threshold": thresholds["complete_threshold"],
        },
    )


@router.post("/auto-build/start")
async def start_auto_build(request: Request, slug: str):
    """Trigger the auto-build pipeline in a background thread."""
    state = get_project_state(slug)

    # Only start if in chapter_build phase and not already running
    if state["current_phase"] != "chapter_build":
        return JSONResponse(
            content={"status": "error", "message": "Not in chapter_build phase"},
            status_code=409,
        )

    if is_build_running(slug):
        return JSONResponse(
            content={"status": "already_running", "message": "Build already in progress"},
            status_code=409,
        )

    # Launch in background thread
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, run_auto_build_sync, slug)

    return JSONResponse(content={"status": "started"})


@router.get("/auto-build/events")
async def auto_build_events(request: Request, slug: str):
    """SSE endpoint streaming build progress events."""

    async def event_stream():
        last_count = 0
        idle_cycles = 0
        max_idle = 600  # 10 minutes at 1s intervals

        while idle_cycles < max_idle:
            events = get_build_progress(slug)

            # Send any new events
            if len(events) > last_count:
                for event in events[last_count:]:
                    data = json.dumps(event.to_dict())
                    yield f"data: {data}\n\n"
                last_count = len(events)
                idle_cycles = 0

                # Check if build is complete
                if events and events[-1].event_type in ("complete", "error"):
                    break
            else:
                idle_cycles += 1

            await asyncio.sleep(1)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/auto-build/status")
async def auto_build_status(request: Request, slug: str):
    """Polling fallback: return current build status as JSON."""
    state = get_project_state(slug)
    events = get_build_progress(slug)
    latest = events[-1].to_dict() if events else None
    return JSONResponse(content={
        "phase": state["current_phase"],
        "building": is_build_running(slug),
        "latest_event": latest,
        "event_count": len(events),
    })
