"""Shared dependencies for the FastAPI web layer."""

import logging

from fastapi import HTTPException

from config.settings import OUTPUT_DIR, PHASE_ORDER
from execution.skill_catalog import get_skills_by_category, load_registry
from execution.state_manager import load_state

logger = logging.getLogger(__name__)


PHASE_LABELS = {
    "idea_intake": "Idea Intake",
    "feature_discovery": "Feature Discovery",
    "outline_generation": "Outline Generation",
    "outline_approval": "Outline Approval",
    "chapter_build": "Chapter Build",
    "quality_gates": "Quality Gates",
    "final_assembly": "Final Assembly",
    "complete": "Complete",
}

PHASE_URLS = {
    "idea_intake": "idea-intake",
    "feature_discovery": "feature-discovery",
    "outline_generation": "outline-generation",
    "outline_approval": "outline-approval",
    "chapter_build": "chapter-build",
    "quality_gates": "quality-gates",
    "final_assembly": "final-assembly",
    "complete": "complete",
}

# Phases that are auto-executed by the build pipeline and hidden from the nav.
HIDDEN_PHASES = {"quality_gates", "final_assembly"}


def get_project_state(slug: str) -> dict:
    """Load project state or raise 404."""
    try:
        return load_state(slug)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")


def check_phase(state: dict, required_phase: str) -> None:
    """Verify the project is in the expected phase, or raise 409."""
    if state["current_phase"] != required_phase:
        raise HTTPException(
            status_code=409,
            detail=f"Project is in phase '{state['current_phase']}', not '{required_phase}'"
        )


def get_phase_info(state: dict) -> dict:
    """Return phase navigation data for templates.

    Hidden phases (quality_gates, final_assembly) are auto-executed by the
    build pipeline and excluded from the visible nav.  When the project is
    in a hidden phase, the nav treats the previous visible phase as current.
    """
    current = state["current_phase"]
    try:
        idx = PHASE_ORDER.index(current)
    except ValueError:
        # Unknown phase (e.g., removed "guided_ideation") — treat as first phase
        idx = 0
        current = PHASE_ORDER[0]

    # Build the visible phase list (skip hidden phases).
    visible_phases = [p for p in PHASE_ORDER if p not in HIDDEN_PHASES]

    # If the project is in a hidden phase, map it to the preceding visible
    # phase for display purposes (e.g. quality_gates → chapter_build).
    display_current = current
    if current in HIDDEN_PHASES:
        for p in reversed(PHASE_ORDER[:idx]):
            if p not in HIDDEN_PHASES:
                display_current = p
                break

    display_idx = (
        visible_phases.index(display_current)
        if display_current in visible_phases
        else 0
    )

    phases = []
    for i, phase in enumerate(visible_phases):
        phases.append({
            "key": phase,
            "label": PHASE_LABELS[phase],
            "url_segment": PHASE_URLS[phase],
            "index": i,
            "is_current": phase == display_current,
            "is_completed": i < display_idx,
            "is_future": i > display_idx,
        })
    return {
        "current_phase": current,
        "current_label": PHASE_LABELS.get(current, current),
        "phase_index": display_idx,
        "total_phases": len(visible_phases),
        "phases": phases,
    }


def get_dashboard_stats() -> dict:
    """Compute system-wide stats for the dashboard.

    Returns:
        Dict with project_count, completed_count, projects_by_phase,
        skill_count, skills_by_category.
    """
    projects = list_projects()
    project_count = len(projects)
    completed_count = sum(1 for p in projects if p["current_phase"] == "complete")

    # Count projects per phase
    projects_by_phase: dict[str, int] = {}
    for p in projects:
        label = p["phase_label"]
        projects_by_phase[label] = projects_by_phase.get(label, 0) + 1

    # Skills
    registry = load_registry()
    skill_count = len(registry)
    skills_grouped = get_skills_by_category(registry)

    return {
        "project_count": project_count,
        "completed_count": completed_count,
        "projects_by_phase": projects_by_phase,
        "skill_count": skill_count,
        "skills_by_category": skills_grouped,
        "category_count": len(skills_grouped),
    }


def list_projects() -> list[dict]:
    """Scan OUTPUT_DIR for projects with state files, sorted newest first."""
    projects = []
    if not OUTPUT_DIR.exists():
        return projects
    for project_dir in sorted(OUTPUT_DIR.iterdir()):
        if not project_dir.is_dir():
            continue
        state_file = project_dir / "project_state.json"
        if state_file.exists():
            try:
                state = load_state(project_dir.name)
                advisory = state.get("advisory") or {}
                projects.append({
                    "name": state["project"]["name"],
                    "slug": state["project"]["slug"],
                    "current_phase": state["current_phase"],
                    "phase_label": PHASE_LABELS.get(state["current_phase"], state["current_phase"]),
                    "created_at": state["project"]["created_at"],
                    "updated_at": state["project"]["updated_at"],
                    "source": advisory.get("source", "manual"),
                    "company_name": advisory.get("company_name", ""),
                    "contact_name": advisory.get("contact_name", ""),
                    "contact_email": advisory.get("contact_email", ""),
                    "advisory_session_id": advisory.get("advisory_session_id", ""),
                })
            except Exception:
                continue
    # Sort by created_at descending (newest first)
    projects.sort(key=lambda p: p["created_at"], reverse=True)
    return projects
