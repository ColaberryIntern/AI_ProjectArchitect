"""Phase 6: Chapter Build routes."""

from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.dependencies import check_phase, get_phase_info, get_project_state
from config.settings import OUTPUT_DIR
from execution.quality_gate_runner import run_chapter_gates
from execution.state_manager import (
    advance_phase,
    all_chapters_approved,
    get_chapter,
    record_chapter_quality,
    record_chapter_status,
    save_state,
)
from execution.template_renderer import render_chapter

router = APIRouter()


@router.get("/chapter-build")
async def chapter_build_page(request: Request, slug: str):
    """Show chapter build overview."""
    state = get_project_state(slug)
    phase_info = get_phase_info(state)
    error = request.query_params.get("error")
    return request.app.state.templates.TemplateResponse(
        request, "project/chapter_build.html",
        {
            "state": state, "slug": slug, "phase_info": phase_info,
            "all_approved": all_chapters_approved(state),
            "selected_chapter": None, "chapter_content": None,
            "gate_results": None, "error": error,
        },
    )


@router.get("/chapter-build/{chapter_index}")
async def chapter_detail_page(request: Request, slug: str, chapter_index: int):
    """Show specific chapter editor."""
    state = get_project_state(slug)
    phase_info = get_phase_info(state)
    chapter = get_chapter(state, chapter_index)
    error = request.query_params.get("error")

    chapter_content = None
    if chapter and chapter.get("content_path"):
        content_path = Path(chapter["content_path"])
        if content_path.exists():
            chapter_content = content_path.read_text(encoding="utf-8")

    return request.app.state.templates.TemplateResponse(
        request, "project/chapter_build.html",
        {
            "state": state, "slug": slug, "phase_info": phase_info,
            "all_approved": all_chapters_approved(state),
            "selected_chapter": chapter, "selected_index": chapter_index,
            "chapter_content": chapter_content,
            "gate_results": chapter.get("quality_report") if chapter else None,
            "error": error,
        },
    )


@router.post("/chapter-build/{chapter_index}/submit")
async def submit_chapter(
    request: Request,
    slug: str,
    chapter_index: int,
    purpose: str = Form(...),
    design_intent: str = Form(...),
    implementation_guidance: str = Form(...),
):
    """Submit chapter content."""
    state = get_project_state(slug)
    check_phase(state, "chapter_build")

    chapter = get_chapter(state, chapter_index)
    if chapter is None:
        return RedirectResponse(
            url=f"/projects/{slug}/chapter-build?error=Chapter+not+found",
            status_code=303,
        )

    section_title = chapter["outline_section"]
    content = render_chapter(
        index=chapter_index,
        title=section_title,
        purpose=purpose,
        design_intent=design_intent,
        implementation_guidance=implementation_guidance,
    )

    chapter_dir = OUTPUT_DIR / slug / "chapters"
    chapter_dir.mkdir(parents=True, exist_ok=True)
    chapter_path = chapter_dir / f"ch{chapter_index}.md"
    chapter_path.write_text(content, encoding="utf-8")

    status = "draft"
    if chapter["status"].startswith("revision_"):
        rev_num = chapter["revision_count"] + 1
        status = f"revision_{rev_num}"
    elif chapter["status"] == "draft":
        status = "revision_1"

    record_chapter_status(state, chapter_index, status, str(chapter_path))

    gate_results = run_chapter_gates(content, section_title)
    record_chapter_quality(state, chapter_index, gate_results)
    save_state(state, slug)

    return RedirectResponse(
        url=f"/projects/{slug}/chapter-build/{chapter_index}", status_code=303
    )


@router.post("/chapter-build/{chapter_index}/approve")
async def approve_chapter(request: Request, slug: str, chapter_index: int):
    """Approve a chapter."""
    state = get_project_state(slug)
    check_phase(state, "chapter_build")
    record_chapter_status(state, chapter_index, "approved")
    save_state(state, slug)

    next_index = chapter_index + 1
    if next_index <= len(state["chapters"]):
        return RedirectResponse(
            url=f"/projects/{slug}/chapter-build/{next_index}", status_code=303
        )
    return RedirectResponse(
        url=f"/projects/{slug}/chapter-build", status_code=303
    )


@router.get("/api/chapters/{chapter_index}/quality")
async def chapter_quality(request: Request, slug: str, chapter_index: int):
    """Run quality gates on a chapter."""
    state = get_project_state(slug)
    chapter = get_chapter(state, chapter_index)
    if not chapter or not chapter.get("content_path"):
        return JSONResponse(content={"error": "Chapter not found or no content"}, status_code=404)

    content_path = Path(chapter["content_path"])
    if not content_path.exists():
        return JSONResponse(content={"error": "Chapter file not found"}, status_code=404)

    content = content_path.read_text(encoding="utf-8")
    gate_results = run_chapter_gates(content, chapter["outline_section"])
    return JSONResponse(content=gate_results)


@router.post("/chapter-build/advance")
async def advance_to_quality_gates(request: Request, slug: str):
    """Advance to quality gates when all chapters approved."""
    state = get_project_state(slug)
    check_phase(state, "chapter_build")
    if not all_chapters_approved(state):
        return RedirectResponse(
            url=f"/projects/{slug}/chapter-build?error=Not+all+chapters+approved",
            status_code=303,
        )
    advance_phase(state, "quality_gates")
    save_state(state, slug)
    return RedirectResponse(
        url=f"/projects/{slug}/quality-gates", status_code=303
    )
