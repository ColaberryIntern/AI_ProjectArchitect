"""Phase 8-9: Final Assembly and Complete routes."""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

from app.dependencies import get_phase_info, get_project_state
from execution.build_depth import get_build_profile
from execution.document_assembler import assemble_full_document
from execution.state_manager import (
    advance_phase,
    get_build_depth_mode,
    record_document_assembly,
    save_state,
    verify_outline_integrity,
    all_chapters_approved,
)

router = APIRouter()


@router.get("/final-assembly")
async def final_assembly_page(request: Request, slug: str):
    """Show pre-assembly checklist."""
    state = get_project_state(slug)
    phase_info = get_phase_info(state)
    error = request.query_params.get("error")
    checks = {
        "all_chapters_approved": all_chapters_approved(state),
        "quality_gates_passed": state["quality"]["final_report"].get("all_passed", False),
        "outline_integrity": verify_outline_integrity(state),
    }
    output_path = state["document"].get("output_path")
    file_exists = bool(output_path and Path(output_path).exists())
    return request.app.state.templates.TemplateResponse(
        request, "project/final_assembly.html",
        {
            "state": state, "slug": slug, "phase_info": phase_info,
            "checks": checks, "all_ready": all(checks.values()),
            "error": error, "file_exists": file_exists,
        },
    )


@router.post("/final-assembly/assemble")
async def assemble_document(request: Request, slug: str):
    """Run the full document assembly pipeline.

    Accepts assembly from both ``final_assembly`` and ``complete`` phases.
    The latter enables re-assembly when the output file has been deleted.
    """
    state = get_project_state(slug)

    allowed_phases = ("final_assembly", "complete")
    if state["current_phase"] not in allowed_phases:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Project is in phase '{state['current_phase']}', "
                f"expected one of {allowed_phases}"
            ),
        )

    chapter_paths = []
    chapter_titles = []
    for chapter in state["chapters"]:
        chapter_paths.append(chapter["content_path"])
        section = next(
            (s for s in state["outline"]["sections"] if s["index"] == chapter["index"]),
            None,
        )
        chapter_titles.append(section["title"] if section else f"Chapter {chapter['index']}")

    version = state["document"]["version"]
    result = assemble_full_document(
        chapter_paths=chapter_paths,
        chapter_titles=chapter_titles,
        project_name=state["project"]["name"],
        project_slug=slug,
        version=version,
    )

    record_document_assembly(state, result["filename"], result["output_path"])
    if state["current_phase"] == "final_assembly":
        advance_phase(state, "complete")
    save_state(state, slug)
    return RedirectResponse(
        url=f"/projects/{slug}/complete", status_code=303
    )


@router.get("/final-assembly/download")
async def download_document(request: Request, slug: str):
    """Download the assembled build guide."""
    state = get_project_state(slug)
    output_path = state["document"].get("output_path")
    if not output_path or not Path(output_path).exists():
        return RedirectResponse(
            url=f"/projects/{slug}/final-assembly?error=Document+not+found",
            status_code=303,
        )
    return FileResponse(
        path=output_path,
        filename=state["document"]["filename"],
        media_type="text/markdown",
    )


@router.get("/complete")
async def complete_page(request: Request, slug: str):
    """Show completion summary."""
    state = get_project_state(slug)
    phase_info = get_phase_info(state)
    depth_mode = get_build_depth_mode(state)
    build_profile = get_build_profile(depth_mode)
    target_range = build_profile["total_page_range"]
    return request.app.state.templates.TemplateResponse(
        request, "project/complete.html",
        {
            "state": state, "slug": slug, "phase_info": phase_info,
            "depth_mode": depth_mode,
            "target_pages": f"{target_range[0]}-{target_range[1]}",
        },
    )
