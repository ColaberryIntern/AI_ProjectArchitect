"""One-shot document generation API.

Accepts a JSON payload with project details, runs the full 8-phase
pipeline in a background thread, and provides polling for progress.

Endpoints:
    POST   /api/v1/generate                  — Start pipeline
    GET    /api/v1/generate/{job_id}/status   — Poll for progress
    GET    /api/v1/generate/{job_id}/download — Download completed document
    DELETE /api/v1/generate/{job_id}          — Cancel/cleanup
"""

import asyncio
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from execution.build_depth import get_scoring_thresholds, resolve_depth_mode
from execution.full_pipeline import (
    _check_document_quality,
    clear_pipeline_progress,
    get_pipeline_progress,
    is_pipeline_running,
    run_full_pipeline_sync,
)
from execution.state_manager import get_build_depth_mode, load_state

router = APIRouter(prefix="/api/v1", tags=["generate"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class GenerateRequest(BaseModel):
    """Request payload for one-shot document generation."""

    project_name: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Human-readable project name",
    )
    requirements: str = Field(
        ...,
        min_length=10,
        max_length=100000,
        description=(
            "Detailed project/campaign description as raw text. "
            "Can include business model, ICPs, messaging, "
            "qualification frameworks, CRM integration data, etc."
        ),
    )
    depth_mode: str = Field(
        default="professional",
        description="Build depth: light, standard, professional, or enterprise",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slugify(name: str) -> str:
    """Mirror state_manager._slugify for slug prediction."""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/generate", status_code=202)
async def start_generation(request: GenerateRequest):
    """Start the full pipeline in a background thread.

    Returns immediately with a job_id (project slug) and URLs for
    polling and downloading the completed document.
    """
    # Validate depth mode
    try:
        resolve_depth_mode(request.depth_mode)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Invalid depth_mode: {request.depth_mode}. "
                f"Must be light, standard, professional, or enterprise."
            ),
        )

    slug = _slugify(request.project_name)

    # Check if already running
    if is_pipeline_running(slug):
        raise HTTPException(
            status_code=409,
            detail=f"A pipeline is already running for '{slug}'.",
        )

    # Launch in background thread
    loop = asyncio.get_event_loop()
    loop.run_in_executor(
        None,
        run_full_pipeline_sync,
        request.project_name,
        request.requirements,
        request.depth_mode,
    )

    return JSONResponse(
        status_code=202,
        content={
            "job_id": slug,
            "status": "started",
            "poll_url": f"/api/v1/generate/{slug}/status",
            "download_url": f"/api/v1/generate/{slug}/download",
        },
    )


@router.get("/generate/{job_id}/status")
async def generation_status(job_id: str):
    """Poll for pipeline status.

    Returns the current phase, percent complete, latest message,
    and a download URL when the pipeline is finished.
    """
    events = get_pipeline_progress(job_id)
    if not events:
        raise HTTPException(
            status_code=404,
            detail=f"No pipeline found for job '{job_id}'.",
        )

    latest = events[-1]

    # Determine status string
    if latest.event_type == "complete":
        status = "complete"
    elif latest.event_type == "error":
        # Distinguish quality failures from generic pipeline errors
        if "quality verification" in latest.message.lower():
            status = "quality_failed"
        else:
            status = "error"
    else:
        status = "running"

    response = {
        "job_id": job_id,
        "phase": latest.event_type,
        "status": status,
        "percent": latest.percent,
        "latest_message": latest.message,
        "event_count": len(events),
    }

    # Include quality summary and download URL when complete
    if status == "complete":
        response["download_url"] = f"/api/v1/generate/{job_id}/download"
        try:
            state = load_state(job_id)
            response["document"] = {
                "filename": state["document"].get("filename"),
                "assembled_at": state["document"].get("assembled_at"),
            }
            depth_mode = get_build_depth_mode(state)
            quality = _check_document_quality(state, depth_mode)
            response["quality_verified"] = quality["passed"]
            response["quality_summary"] = {
                "final_gates_passed": quality["final_gates_passed"],
                "chapters_below_threshold": quality["deficient_chapters"],
                "average_score": quality["average_score"],
                "complete_threshold": quality["complete_threshold"],
            }
        except FileNotFoundError:
            pass

    # Include quality details on quality_failed so caller knows what went wrong
    if status == "quality_failed" and latest.data.get("quality"):
        quality = latest.data["quality"]
        response["quality_summary"] = {
            "final_gates_passed": quality.get("final_gates_passed", False),
            "chapters_below_threshold": quality.get("deficient_chapters", []),
            "average_score": quality.get("average_score", 0),
            "complete_threshold": quality.get("complete_threshold", 0),
        }

    return JSONResponse(content=response)


@router.get("/generate/{job_id}/download")
async def download_generated_document(job_id: str):
    """Download the completed document as a markdown file.

    Only serves the document when quality verification has passed.
    """
    try:
        state = load_state(job_id)
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Project '{job_id}' not found.",
        )

    if state["current_phase"] != "complete":
        raise HTTPException(
            status_code=409,
            detail=f"Document not yet ready. Current phase: {state['current_phase']}",
        )

    output_path = state["document"].get("output_path")
    if not output_path:
        raise HTTPException(
            status_code=404,
            detail="Document has not been assembled.",
        )

    if not Path(output_path).exists():
        raise HTTPException(
            status_code=404,
            detail="Document file not found on disk.",
        )

    # Quality check — serve document with warning headers if below threshold
    depth_mode = get_build_depth_mode(state)
    quality = _check_document_quality(state, depth_mode)
    headers = {}
    if not quality["passed"]:
        headers["X-Quality-Warning"] = "true"
        headers["X-Quality-Score"] = str(quality["average_score"])
        headers["X-Quality-Threshold"] = str(quality["complete_threshold"])

    return FileResponse(
        path=output_path,
        filename=state["document"]["filename"],
        media_type="text/markdown",
        headers=headers if headers else None,
    )


@router.delete("/generate/{job_id}")
async def cancel_generation(job_id: str):
    """Cancel or clean up a pipeline job.

    Clears in-memory progress events. Does not delete the project
    output directory (use the project delete endpoint for that).
    """
    was_running = is_pipeline_running(job_id)
    clear_pipeline_progress(job_id)

    return JSONResponse(content={
        "job_id": job_id,
        "was_running": was_running,
        "status": "cleared",
    })
