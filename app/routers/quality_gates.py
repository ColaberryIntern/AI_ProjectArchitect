"""Phase 7: Quality Gates routes."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app.dependencies import check_phase, get_phase_info, get_project_state
from execution.quality_gate_runner import generate_quality_report, run_final_gates
from execution.state_manager import advance_phase, record_final_quality, save_state

router = APIRouter()


@router.get("/quality-gates")
async def quality_gates_page(request: Request, slug: str):
    """Show quality gate results page."""
    state = get_project_state(slug)
    phase_info = get_phase_info(state)
    error = request.query_params.get("error")
    final_report = state["quality"]["final_report"]
    report_text = None
    if final_report.get("ran_at"):
        report_text = generate_quality_report(final_report)
    return request.app.state.templates.TemplateResponse(
        request, "project/quality_gates.html",
        {
            "state": state, "slug": slug, "phase_info": phase_info,
            "final_report": final_report, "report_text": report_text,
            "error": error,
        },
    )


@router.post("/quality-gates/run")
async def run_quality_gates(request: Request, slug: str):
    """Run final quality gates on the full document."""
    state = get_project_state(slug)
    check_phase(state, "quality_gates")

    all_text = ""
    for chapter in state["chapters"]:
        if chapter.get("content_path"):
            cp = Path(chapter["content_path"])
            if cp.exists():
                all_text += cp.read_text(encoding="utf-8") + "\n\n"

    gate_results = run_final_gates(all_text)
    record_final_quality(state, gate_results)
    save_state(state, slug)
    return RedirectResponse(
        url=f"/projects/{slug}/quality-gates", status_code=303
    )


@router.post("/quality-gates/advance")
async def advance_to_assembly(request: Request, slug: str):
    """Advance to final assembly."""
    state = get_project_state(slug)
    check_phase(state, "quality_gates")
    if not state["quality"]["final_report"].get("all_passed"):
        return RedirectResponse(
            url=f"/projects/{slug}/quality-gates?error=Quality+gates+not+passed",
            status_code=303,
        )
    advance_phase(state, "final_assembly")
    save_state(state, slug)
    return RedirectResponse(
        url=f"/projects/{slug}/final-assembly", status_code=303
    )
