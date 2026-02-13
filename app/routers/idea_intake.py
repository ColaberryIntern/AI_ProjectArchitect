"""Phase 1: Idea Intake routes.

Two-step flow:
1. Submit raw idea text → generate project profile → redirect to profile review
2. Review & confirm profile fields → advance to feature discovery
"""

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.dependencies import get_phase_info, get_project_state
from execution.profile_generator import generate_profile
from execution.state_manager import (
    PROFILE_REQUIRED_FIELDS,
    advance_phase,
    append_chat_message,
    confirm_all_profile_fields,
    get_project_profile,
    record_idea,
    save_state,
    set_chat_step,
    set_profile_derived,
    set_profile_field,
)

router = APIRouter()


@router.get("/idea-intake")
async def idea_intake_page(request: Request, slug: str):
    """Show idea intake form, or redirect to profile review if profile exists."""
    state = get_project_state(slug)
    phase_info = get_phase_info(state)

    # If profile already generated and still in idea_intake, redirect to profile review
    profile = get_project_profile(state)
    if profile.get("generated_at") and state["current_phase"] == "idea_intake":
        return RedirectResponse(
            url=f"/projects/{slug}/idea-intake/profile", status_code=303
        )

    error = request.query_params.get("error")
    return request.app.state.templates.TemplateResponse(
        request, "project/idea_intake.html",
        {"state": state, "slug": slug, "phase_info": phase_info, "error": error},
    )


@router.post("/idea-intake")
async def submit_idea(request: Request, slug: str, raw_idea: str = Form(...)):
    """Record the raw idea, generate profile, redirect to profile review."""
    state = get_project_state(slug)
    if state["current_phase"] != "idea_intake":
        return RedirectResponse(url=f"/projects/{slug}", status_code=303)

    record_idea(state, raw_idea)

    # Generate project profile from idea
    profile_data = generate_profile(raw_idea)

    # Store options + recommendations in state
    for field in PROFILE_REQUIRED_FIELDS:
        field_data = profile_data["fields"].get(field, {})
        set_profile_field(
            state, field,
            field_data.get("options", []),
            field_data.get("recommended"),
            field_data.get("confidence", 0.0),
        )

    # Store derived lists
    derived = profile_data.get("derived", {})
    set_profile_derived(
        state,
        technical_constraints=derived.get("technical_constraints", []),
        nfrs=derived.get("non_functional_requirements", []),
        success_metrics=derived.get("success_metrics", []),
        risk_assessment=derived.get("risk_assessment", []),
        core_use_cases=derived.get("core_use_cases", []),
    )

    # Sync chat engine state
    append_chat_message(state, "user", raw_idea)
    append_chat_message(
        state, "bot",
        "Got it! I've analyzed your idea and extracted a project profile. "
        "Please review and confirm each field."
    )

    save_state(state, slug)
    return RedirectResponse(
        url=f"/projects/{slug}/idea-intake/profile", status_code=303
    )


@router.get("/idea-intake/profile")
async def profile_review_page(request: Request, slug: str):
    """Show profile review page with radio buttons for each field."""
    state = get_project_state(slug)
    if state["current_phase"] != "idea_intake":
        return RedirectResponse(url=f"/projects/{slug}", status_code=303)

    profile = get_project_profile(state)
    if not profile.get("generated_at"):
        return RedirectResponse(
            url=f"/projects/{slug}/idea-intake", status_code=303
        )

    phase_info = get_phase_info(state)
    error = request.query_params.get("error")

    # Build field display data
    field_labels = {
        "problem_definition": "Problem Definition",
        "target_user": "Target User",
        "value_proposition": "Value Proposition",
        "deployment_type": "Deployment Type",
        "ai_depth": "AI Depth",
        "monetization_model": "Monetization Model",
        "mvp_scope": "MVP Scope",
    }

    return request.app.state.templates.TemplateResponse(
        request, "project/profile_review.html",
        {
            "state": state,
            "slug": slug,
            "phase_info": phase_info,
            "error": error,
            "profile": profile,
            "field_labels": field_labels,
            "required_fields": PROFILE_REQUIRED_FIELDS,
        },
    )


@router.post("/idea-intake/profile")
async def confirm_profile(request: Request, slug: str):
    """Confirm all 7 profile fields and advance to feature discovery."""
    state = get_project_state(slug)
    if state["current_phase"] != "idea_intake":
        return RedirectResponse(url=f"/projects/{slug}", status_code=303)

    form = await request.form()
    selections = {field: form.get(field, "") for field in PROFILE_REQUIRED_FIELDS}

    # Validate all fields have a selection
    if not all(selections.values()):
        return RedirectResponse(
            url=f"/projects/{slug}/idea-intake/profile?error=all_required",
            status_code=303,
        )

    confirm_all_profile_fields(state, selections)
    advance_phase(state, "feature_discovery")

    # Sync chat engine state
    set_chat_step(state, "feature_discovery.welcome")
    append_chat_message(
        state, "bot",
        "Profile confirmed! Let's discover the features your product needs."
    )

    save_state(state, slug)
    return RedirectResponse(
        url=f"/projects/{slug}/feature-discovery", status_code=303
    )
