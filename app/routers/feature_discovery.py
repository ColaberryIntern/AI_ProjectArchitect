"""Phase 2: Feature Discovery routes.

Profile-driven catalog generation with alignment validation.
"""

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.dependencies import get_phase_info, get_project_state
from execution.feature_catalog import (
    MUTUAL_EXCLUSION_GROUPS,
    generate_catalog,
    generate_catalog_from_profile,
    get_catalog_by_category,
    get_catalog_by_layer,
    get_features_by_ids,
)
from execution.feature_classifier import (
    check_feature_problem_mapping,
    check_intern_explainability,
    check_mutual_exclusions,
    order_by_priority,
)
from execution.intelligence_goals import (
    CONFIDENCE_GOAL_TYPES,
    CONFIDENCE_LEVELS,
    generate_intelligence_goals,
    should_show_intelligence_goals,
)
from execution.profile_validator import run_all_profile_checks
from execution.state_manager import (
    add_feature,
    advance_phase,
    approve_features,
    get_intelligence_goals,
    get_project_profile,
    is_profile_complete,
    save_state,
    set_intelligence_goals,
)

router = APIRouter()


@router.get("/feature-discovery")
async def feature_discovery_page(request: Request, slug: str):
    """Show feature catalog with checkboxes."""
    state = get_project_state(slug)
    phase_info = get_phase_info(state)

    # Gate: if profile incomplete and in feature_discovery, redirect to profile review
    if state["current_phase"] == "feature_discovery" and not is_profile_complete(state):
        return RedirectResponse(
            url=f"/projects/{slug}/idea-intake/profile", status_code=303
        )

    # Generate catalog on first visit (one-time LLM call)
    if not state["features"].get("catalog"):
        profile = get_project_profile(state)
        if is_profile_complete(state):
            state["features"]["catalog"] = generate_catalog_from_profile(profile)
        else:
            idea = state.get("idea", {}).get("original_raw", "")
            state["features"]["catalog"] = generate_catalog(idea)
        save_state(state, slug)

    catalog = state["features"]["catalog"]
    selected_ids = [f["id"] for f in state["features"]["core"]]
    catalog_by_category = get_catalog_by_category(catalog)
    catalog_by_layer = get_catalog_by_layer(catalog)
    error = request.query_params.get("error")

    # Intelligence goals context
    profile = get_project_profile(state)
    idea = state.get("idea", {}).get("original_raw", "")
    features = state["features"]["core"]
    ai_depth = profile.get("ai_depth", {}).get("selected", "")
    show_intelligence_goals = should_show_intelligence_goals(idea, features, ai_depth)
    existing_goals = get_intelligence_goals(state)

    # Auto-generate goals on first visit when triggered (mirrors catalog auto-gen)
    if show_intelligence_goals and not existing_goals:
        generated = generate_intelligence_goals(idea, features, ai_depth)
        set_intelligence_goals(state, generated)
        save_state(state, slug)
        existing_goals = get_intelligence_goals(state)

    return request.app.state.templates.TemplateResponse(
        request, "project/feature_discovery.html",
        {
            "state": state, "slug": slug, "phase_info": phase_info,
            "catalog": catalog, "selected_ids": selected_ids, "error": error,
            "catalog_by_category": catalog_by_category,
            "catalog_by_layer": catalog_by_layer,
            "exclusion_groups": MUTUAL_EXCLUSION_GROUPS,
            "show_intelligence_goals": show_intelligence_goals,
            "existing_goals": existing_goals,
            "confidence_levels": CONFIDENCE_LEVELS,
            "confidence_goal_types": list(CONFIDENCE_GOAL_TYPES),
        },
    )


@router.post("/feature-discovery/select")
async def select_features_from_catalog(request: Request, slug: str):
    """Save selected features from catalog and advance to outline generation."""
    state = get_project_state(slug)
    if state["current_phase"] != "feature_discovery":
        return RedirectResponse(url=f"/projects/{slug}", status_code=303)

    form_data = await request.form()
    selected_ids = form_data.getlist("features")

    # Mutual exclusion check
    exclusion_result = check_mutual_exclusions(selected_ids, MUTUAL_EXCLUSION_GROUPS)
    if not exclusion_result["passed"]:
        messages = [v["message"] for v in exclusion_result["violations"]]
        error_msg = "; ".join(messages)
        return RedirectResponse(
            url=f"/projects/{slug}/feature-discovery?error={error_msg}",
            status_code=303,
        )

    catalog = state["features"].get("catalog", [])

    # Clear previous selections, rebuild from form
    state["features"]["core"] = []
    selected = get_features_by_ids(catalog, selected_ids)
    for i, feat in enumerate(selected, 1):
        add_feature(
            state, "core", feat["id"], feat["name"], feat["description"],
            "Selected during feature discovery", "core problem", build_order=i,
        )

    # Store selected feature IDs in profile
    profile = get_project_profile(state)
    profile["selected_features"] = selected_ids

    approve_features(state)
    advance_phase(state, "outline_generation")
    save_state(state, slug)
    return RedirectResponse(
        url=f"/projects/{slug}/outline-generation", status_code=303
    )


@router.post("/feature-discovery/add")
async def add_feature_route(
    request: Request,
    slug: str,
    feature_type: str = Form(...),
    feature_id: str = Form(...),
    name: str = Form(...),
    description: str = Form(...),
    rationale: str = Form(...),
    problem_mapped_to: str = Form(""),
    build_order: int = Form(0),
    deferred: bool = Form(False),
    defer_reason: str = Form(""),
):
    """Add a new feature (legacy endpoint)."""
    state = get_project_state(slug)
    if state["current_phase"] != "feature_discovery":
        return RedirectResponse(url=f"/projects/{slug}", status_code=303)

    add_feature(
        state,
        feature_type=feature_type,
        feature_id=feature_id,
        name=name,
        description=description,
        rationale=rationale,
        problem_mapped_to=problem_mapped_to,
        build_order=build_order,
        deferred=deferred,
        defer_reason=defer_reason or None,
    )
    save_state(state, slug)
    return RedirectResponse(
        url=f"/projects/{slug}/feature-discovery", status_code=303
    )


@router.get("/api/features/validate")
async def validate_features(request: Request, slug: str):
    """Run feature validation checks."""
    state = get_project_state(slug)
    core = state["features"]["core"]
    problems = list({f.get("problem_mapped_to", "") for f in core if f.get("problem_mapped_to")})
    mapping = check_feature_problem_mapping(core, problems)
    explainability = check_intern_explainability(core)
    ordered = order_by_priority(core)
    return JSONResponse(content={
        "problem_mapping": mapping,
        "intern_explainability": explainability,
        "ordered_features": ordered,
    })


@router.get("/api/features/validate-alignment")
async def validate_feature_alignment(request: Request, slug: str):
    """Check feature alignment with project profile."""
    state = get_project_state(slug)
    profile = get_project_profile(state)
    features = state["features"]["core"]
    result = run_all_profile_checks(profile, features)
    return JSONResponse(content=result)


@router.get("/api/features/validate-exclusions")
async def validate_exclusions(request: Request, slug: str):
    """Check selected features against mutual exclusion groups."""
    state = get_project_state(slug)
    selected_ids = [f["id"] for f in state["features"]["core"]]
    result = check_mutual_exclusions(selected_ids, MUTUAL_EXCLUSION_GROUPS)
    return JSONResponse(content=result)


@router.get("/api/intelligence-goals/generate")
async def generate_goals_route(request: Request, slug: str):
    """Generate intelligence goals for the project via LLM."""
    state = get_project_state(slug)
    profile = get_project_profile(state)
    idea = state.get("idea", {}).get("original_raw", "")
    features = state["features"]["core"]
    ai_depth = profile.get("ai_depth", {}).get("selected", "")

    goals = generate_intelligence_goals(idea, features, ai_depth)
    return JSONResponse(content={"goals": goals})


@router.post("/api/intelligence-goals/save")
async def save_goals_route(request: Request, slug: str):
    """Save selected intelligence goals to state."""
    state = get_project_state(slug)
    body = await request.json()
    goals = body.get("goals", [])

    # Validate structure (accepts both old and new field names)
    validated = []
    for goal in goals:
        if not isinstance(goal, dict):
            continue
        validated.append({
            "id": str(goal.get("id", "")),
            "user_facing_label": str(goal.get("user_facing_label") or goal.get("label", ""))[:100],
            "description": str(goal.get("description", ""))[:500],
            "goal_type": str(goal.get("goal_type") or goal.get("type", "recommendation")),
            "confidence_required": str(goal.get("confidence_required") or goal.get("confidence_level", "")) or None,
            "impact_level": str(goal.get("impact_level", "")) or None,
        })

    set_intelligence_goals(state, validated)
    save_state(state, slug)
    return JSONResponse(content={"saved": len(validated)})


@router.post("/feature-discovery/approve")
async def approve_features_route(request: Request, slug: str):
    """Approve features and advance to outline generation."""
    state = get_project_state(slug)
    if state["current_phase"] != "feature_discovery":
        return RedirectResponse(url=f"/projects/{slug}", status_code=303)

    approve_features(state)
    advance_phase(state, "outline_generation")
    save_state(state, slug)
    return RedirectResponse(
        url=f"/projects/{slug}/outline-generation", status_code=303
    )
