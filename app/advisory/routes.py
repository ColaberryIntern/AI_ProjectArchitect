"""FastAPI routes for the AI Advisory layer.

Implements the executive-facing advisory wizard flow:
landing → idea input → 10 questions → generate → results → gate → lead capture → PDF

Integrates with lead management, campaign enrollment, and event tracking.
"""

import logging
import os
import time
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/advisory", tags=["advisory"])

# Advisory templates live in their own directory
_TEMPLATE_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


def _extract_utm(request: Request) -> dict:
    """Extract UTM params from query string or cookies."""
    params = {}
    for key in ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"):
        val = request.query_params.get(key, "")
        if val:
            params[key] = val
    return params


def _session_user(request: Request):
    """Resolve the logged-in operator (My-Day build routes only). Mirrors
    app/routers/my_day.py._session_user: cookie JWT, dev fallback to Ali."""
    from execution.products.library import auth_google, tenancy
    cookie = request.cookies.get(auth_google.SESSION_COOKIE_NAME)
    user = auth_google.current_user_from_cookie(cookie)
    if user:
        return user
    if not auth_google.is_enabled():
        return tenancy.get_user("ali@colaberry.com")
    return None


def _advisory_enabled() -> bool:
    """Call-time kill-switch (P1.5 hardening). Default ON; set
    OPS_ADVISORY_ENABLED=false to disable the public advisory funnel."""
    return os.environ.get("OPS_ADVISORY_ENABLED", "true").strip().lower() != "false"


def require_advisory_enabled():
    """Dependency on the mutating/side-effecting advisory routes. Read-only pages
    stay up so a disabled banner can still render."""
    if not _advisory_enabled():
        raise HTTPException(
            status_code=503,
            detail="AI Advisory is temporarily disabled by an administrator.",
        )


# ─── Per-IP rate limit on the public funnel (P1.5 hardening) ─────────
# Process-local sliding window (prod runs a single uvicorn worker). Caps abuse
# of the unauthenticated funnel (LLM spend / BC spam). Tune via env; 0 disables.
_ADV_RATE: dict[str, list[float]] = {}
_ADV_RATE_MAX = int(os.environ.get("OPS_ADVISORY_RATE_MAX", "40"))
_ADV_RATE_WINDOW = int(os.environ.get("OPS_ADVISORY_RATE_WINDOW_SEC", "600"))


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit_advisory(request: Request):
    """Dependency: 429 when an IP exceeds OPS_ADVISORY_RATE_MAX requests per
    OPS_ADVISORY_RATE_WINDOW_SEC on the gated funnel entrypoints."""
    if _ADV_RATE_MAX <= 0:
        return
    ip = _client_ip(request)
    now = time.time()
    hits = [t for t in _ADV_RATE.get(ip, []) if t > now - _ADV_RATE_WINDOW]
    hits.append(now)
    _ADV_RATE[ip] = hits
    if len(hits) > _ADV_RATE_MAX:
        raise HTTPException(
            status_code=429,
            detail="Too many requests — please slow down and try again shortly.",
        )


# ─── Landing & Session Start ────────────────────────────────────────

@router.get("/")
async def advisory_landing(request: Request):
    """Render the advisory landing page."""
    return templates.TemplateResponse("advisory_landing.html", {"request": request})


@router.get("/new")
async def advisory_new_project(request: Request):
    """Focused 'Create a new project' start page for the My-Day build flow —
    a clean idea box that posts straight into the process (no marketing)."""
    return templates.TemplateResponse("advisory_new_project.html",
                                      {"request": request, "myday_build": True})


@router.get("/demo/walkthrough")
async def demo_walkthrough(request: Request):
    """Render the guided demo walkthrough page with optional industry scenario."""
    from execution.demo.demo_scenarios import get_scenario
    scenario_id = request.query_params.get("scenario", "logistics")
    scenario = get_scenario(scenario_id)
    return templates.TemplateResponse("demo_walkthrough.html", {
        "request": request,
        "scenario_json": __import__("json").dumps(scenario),
        "scenario_id": scenario_id,
    })


@router.post("/start", dependencies=[Depends(require_advisory_enabled), Depends(rate_limit_advisory)])
async def start_session(request: Request, business_idea: str = Form(...),
                        myday_build: str = Form("")):
    """Create a new advisory session and redirect to questions."""
    from execution.advisory.advisory_state_manager import advance_status, initialize_session, save_session
    from execution.advisory.event_tracker import track_event

    session = initialize_session(business_idea)
    advance_status(session, "questioning")

    # Personalize the intake questions to the idea the user just described, so
    # the example chips (and the opening question) relate to THEIR business
    # instead of generic logistics/SaaS/healthcare filler. Generated once here
    # and cached on the session; fallback-safe (returns {} when the LLM is off).
    try:
        from execution.advisory.question_tailor import tailor_questions
        session["tailored_questions"] = tailor_questions(business_idea)
    except Exception:
        session["tailored_questions"] = {}
    save_session(session)

    # My-Day-initiated "Create a new project" build: flag the session so that
    # after capabilities we divert to the build-setup step (target BC project +
    # pace) instead of the public results page. The anonymous funnel never sets
    # this, so it is unaffected.
    if str(myday_build).strip() in ("1", "true", "True", "on", "yes"):
        session["myday_build"] = True
        save_session(session)

    track_event(
        event_name="advisory_session_started",
        session_id=session["session_id"],
        utm_params=_extract_utm(request),
    )

    return RedirectResponse(
        url=f"/advisory/{session['session_id']}/questions",
        status_code=303,
    )


# ─── Question Flow ──────────────────────────────────────────────────

@router.get("/{session_id}/questions")
async def question_flow(request: Request, session_id: str):
    """Render the current question in the wizard."""
    from execution.advisory.advisory_state_manager import load_session
    from execution.advisory.question_engine import (
        SYSTEM_INTEGRATION_OPTIONS,
        get_next_question,
        get_progress,
        is_complete,
    )

    try:
        session = load_session(session_id)
    except FileNotFoundError:
        return RedirectResponse(url="/advisory/", status_code=303)

    if is_complete(session):
        return RedirectResponse(
            url=f"/advisory/{session_id}/design",
            status_code=303,
        )

    from execution.advisory.question_tailor import apply_tailoring
    question = apply_tailoring(get_next_question(session), session.get("tailored_questions"))
    progress = get_progress(session)

    # Show soft email capture after question 5 (mid-Phase 1)
    show_email_capture = (progress["current_index"] == 5 and not session.get("email"))

    return templates.TemplateResponse("question_flow.html", {
        "request": request,
        "session": session,
        "question": question,
        "progress": progress,
        "system_options": SYSTEM_INTEGRATION_OPTIONS,
        "show_email_capture": show_email_capture,
        "myday_build": session.get("myday_build", False),
    })


@router.post("/{session_id}/answer", dependencies=[Depends(require_advisory_enabled)])
async def submit_answer(
    request: Request,
    session_id: str,
):
    """Record an answer with LLM validation. Rejects off-topic answers."""
    from execution.advisory.advisory_state_manager import (
        load_session,
        record_answer,
        save_session,
        set_selected_systems,
    )
    from execution.advisory.question_engine import (
        get_next_question,
        get_remaining_question_ids,
        is_complete,
    )

    try:
        session = load_session(session_id)
    except FileNotFoundError:
        return RedirectResponse(url="/advisory/", status_code=303)

    # Parse form data
    form_data = await request.form()
    answer_text = str(form_data.get("answer_text", "")).strip()
    email = str(form_data.get("email", "")).strip()
    selected_systems = [str(v) for v in form_data.getlist("selected_systems")]

    # Capture soft email
    if email and not session.get("email"):
        session["email"] = email.strip()
        save_session(session)

    # Clear any pending follow-up (user is responding to it)
    if session.get("pending_follow_up"):
        session["pending_follow_up"] = None
        save_session(session)

    question = get_next_question(session)
    if not question or not answer_text:
        return RedirectResponse(url=f"/advisory/{session_id}/questions", status_code=303)

    # Use the same idea-tailored question the user actually saw — so validation
    # judges against the personalized prompt and the recorded history matches.
    from execution.advisory.question_tailor import apply_tailoring
    question = apply_tailoring(question, session.get("tailored_questions"))

    # ═══ LLM VALIDATION ═══════════════════════════════════════════
    try:
        from execution.advisory.answer_validator import validate_answer
        remaining = get_remaining_question_ids(session)
        validation = validate_answer(
            question=question,
            answer_text=answer_text,
            previous_answers=session.get("answers", []),
            remaining_question_ids=remaining,
        )
    except Exception:
        # Fallback: accept answer as-is
        validation = {"addresses_question": True, "quality": "complete", "follow_up": None, "already_answered": []}

    # If answer is completely off-topic, don't advance — ask follow-up
    if validation.get("quality") == "off_topic" and validation.get("follow_up"):
        session["pending_follow_up"] = validation["follow_up"]
        save_session(session)
        return RedirectResponse(url=f"/advisory/{session_id}/questions", status_code=303)

    # Answer is valid (complete or partial) — record it
    record_answer(session, question["id"], question["text"], answer_text)

    if selected_systems:
        set_selected_systems(session, selected_systems)

    # Mark any questions that were already answered in this response
    already_answered = validation.get("already_answered", [])
    if already_answered:
        skipped = session.get("skipped_questions", [])
        for qid in already_answered:
            if qid not in skipped:
                skipped.append(qid)
        session["skipped_questions"] = skipped
        save_session(session)

    # Advance past any skipped questions
    skipped = set(session.get("skipped_questions", []))
    while session["current_question_index"] < 10:
        from execution.advisory.question_engine import get_question
        next_q = get_question(session["current_question_index"])
        if next_q and next_q["id"] in skipped:
            session["current_question_index"] += 1
        else:
            break
    save_session(session)

    if is_complete(session):
        from execution.advisory.event_tracker import track_event
        track_event(
            event_name="advisory_questions_completed",
            session_id=session_id,
            email=session.get("email", ""),
        )
        return RedirectResponse(
            url=f"/advisory/{session_id}/design",
            status_code=303,
        )

    return RedirectResponse(
        url=f"/advisory/{session_id}/questions",
        status_code=303,
    )


# ─── Design Studio (Outcomes + Systems) ─────────────────────────────

@router.get("/{session_id}/design")
async def design_studio(request: Request, session_id: str):
    """Show the AI-guided business outcomes + systems selection page."""
    from execution.advisory.advisory_state_manager import load_session
    from execution.advisory.capability_mapper import AI_SYSTEMS, BUSINESS_OUTCOMES
    from execution.advisory.recommendation_engine import recommend_design

    try:
        session = load_session(session_id)
    except FileNotFoundError:
        return RedirectResponse(url="/advisory/", status_code=303)

    if session.get("capability_map") is not None and session.get("org_structure") is not None:
        return RedirectResponse(url=f"/advisory/{session_id}/results", status_code=303)

    # Generate recommendations from answers
    recs = recommend_design(session)

    return templates.TemplateResponse("design_studio.html", {
        "request": request,
        "session": session,
        "outcomes": BUSINESS_OUTCOMES,
        "systems": AI_SYSTEMS,
        "rec_outcomes": recs["recommended_outcomes"],
        "rec_systems": recs["recommended_systems"],
        "myday_build": session.get("myday_build", False),
    })


@router.post("/{session_id}/design", dependencies=[Depends(require_advisory_enabled)])
async def save_design(request: Request, session_id: str):
    """Save outcomes + systems, compute recommendations, redirect to capabilities."""
    from execution.advisory.advisory_state_manager import (
        load_session,
        set_capability_recommendations,
        set_selected_ai_systems,
        set_selected_outcomes,
    )
    from execution.advisory.capability_mapper import map_capabilities, should_include_cory
    from execution.advisory.event_tracker import track_event

    try:
        session = load_session(session_id)
    except FileNotFoundError:
        return RedirectResponse(url="/advisory/", status_code=303)

    form_data = await request.form()
    outcome_ids = [str(v) for v in form_data.getlist("outcomes")]
    system_ids = [str(v) for v in form_data.getlist("ai_systems")]

    # Auto-include Cory if criteria met
    if should_include_cory({"selected_ai_systems": system_ids, "selected_outcomes": outcome_ids}):
        if "intelligence_engine" not in system_ids:
            system_ids.append("intelligence_engine")

    set_selected_outcomes(session, outcome_ids)
    set_selected_ai_systems(session, system_ids)

    # Compute capability recommendations
    recommendations = map_capabilities(session)
    set_capability_recommendations(session, recommendations)

    track_event(
        event_name="advisory_design_completed",
        session_id=session_id,
        email=session.get("email", ""),
        properties={"outcomes": outcome_ids, "systems": system_ids},
    )

    return RedirectResponse(
        url=f"/advisory/{session_id}/capabilities",
        status_code=303,
    )


# ─── Capability Selector (with Recommendations) ─────────────────────

@router.get("/{session_id}/capabilities")
async def capability_selector(request: Request, session_id: str):
    """Show capabilities with AI-powered recommendations pre-selected."""
    from execution.advisory.advisory_state_manager import load_session
    from execution.advisory.capability_catalog import get_department_meta

    try:
        session = load_session(session_id)
    except FileNotFoundError:
        return RedirectResponse(url="/advisory/", status_code=303)

    if session.get("capability_map") is not None and session.get("org_structure") is not None:
        return RedirectResponse(url=f"/advisory/{session_id}/results", status_code=303)

    # If design studio wasn't completed, redirect there first
    if not session.get("selected_outcomes") and not session.get("selected_ai_systems"):
        return RedirectResponse(url=f"/advisory/{session_id}/design", status_code=303)

    departments = get_department_meta()
    recs = session.get("capability_recommendations") or {}
    recommended_ids = set(recs.get("recommended", []))
    reasoning = recs.get("reasoning", {})
    confidence = recs.get("confidence_scores", {})
    exclusion_reasons = recs.get("exclusion_reasons", {})
    primary_label = recs.get("primary_label", "")

    # Pre-select recommended capabilities (user can override)
    selected_ids = session.get("selected_capabilities") or list(recommended_ids)

    return templates.TemplateResponse("capability_selector.html", {
        "request": request,
        "session": session,
        "departments": departments,
        "selected_ids": selected_ids,
        "recommended_ids": recommended_ids,
        "reasoning": reasoning,
        "confidence": confidence,
        "exclusion_reasons": exclusion_reasons,
        "primary_label": primary_label,
        "myday_build": session.get("myday_build", False),
    })


@router.post("/{session_id}/capabilities", dependencies=[Depends(require_advisory_enabled)])
async def save_capabilities(request: Request, session_id: str):
    """Save selected capabilities and proceed to generation."""
    from execution.advisory.advisory_state_manager import (
        load_session,
        set_selected_capabilities,
    )
    from execution.advisory.event_tracker import track_event

    try:
        session = load_session(session_id)
    except FileNotFoundError:
        return RedirectResponse(url="/advisory/", status_code=303)

    form_data = await request.form()
    selected_ids = [str(v) for v in form_data.getlist("capabilities")]

    set_selected_capabilities(session, selected_ids)

    track_event(
        event_name="advisory_capabilities_selected",
        session_id=session_id,
        email=session.get("email", ""),
        properties={"count": len(selected_ids)},
    )

    # My-Day build: collect target BC project + pace before generating.
    if session.get("myday_build"):
        return RedirectResponse(url=f"/advisory/{session_id}/build-setup", status_code=303)

    return RedirectResponse(
        url=f"/advisory/{session_id}/generate",
        status_code=303,
    )


# ─── My-Day "Create a new project" build (target project + pace → background) ──

@router.get("/{session_id}/build-setup")
async def build_setup(request: Request, session_id: str):
    """Collect the target Basecamp project + build pace before generating."""
    from execution.advisory.advisory_state_manager import load_session

    user = _session_user(request)
    if not user:
        return RedirectResponse(url=f"/auth/login?next=/advisory/{session_id}/build-setup", status_code=303)
    try:
        session = load_session(session_id)
    except FileNotFoundError:
        return RedirectResponse(url="/advisory/", status_code=303)

    # The operator's Basecamp projects for the dropdown (cached; no BC call).
    projects = []
    try:
        from execution.products.ops import store
        projects = [{"bc_id": p.bc_id, "name": p.name} for p in store.load_projects(user.email)]
    except Exception:
        logger.warning("could not load operator BC projects for build-setup", exc_info=True)

    return templates.TemplateResponse("build_setup.html", {
        "request": request,
        "session": session,
        "session_id": session_id,
        "projects": projects,
        "myday_build": True,
    })


@router.post("/{session_id}/start-build")
async def start_build(request: Request, session_id: str,
                      bc_project_id: str = Form(...), pace: str = Form("standard"),
                      blueprint: str = Form("standard")):
    """Run advisory generation synchronously (so the org page renders + the
    project slug exists), then kick the requirements+Basecamp build in the
    background and send the user to the buildout page."""
    from execution.advisory.advisory_generation import generate_advisory_outputs
    from execution.advisory import build_status, myday_build_orchestrator

    user = _session_user(request)
    if not user:
        return RedirectResponse(url=f"/auth/login?next=/advisory/{session_id}/build-setup", status_code=303)
    try:
        bucket = int(bc_project_id)
    except (TypeError, ValueError):
        return RedirectResponse(url=f"/advisory/{session_id}/build-setup", status_code=303)
    pace = pace if pace in ("sprint", "standard", "relaxed") else "standard"
    blueprint = blueprint if blueprint in ("standard", "autonomous") else "standard"

    # Phase a: advisory generation + project creation (synchronous → slug).
    try:
        result = generate_advisory_outputs(session_id)
    except FileNotFoundError:
        return RedirectResponse(url="/advisory/", status_code=303)
    slug = result.get("slug")
    if not slug:
        return RedirectResponse(url=f"/advisory/{session_id}/results", status_code=303)

    # Seed the build status, then kick phases b-d in the background.
    from execution.advisory.advisory_state_manager import load_session, save_session
    session = load_session(session_id)
    idea_text = session.get("business_idea", "")
    session["myday_build_target"] = {"bc_project_id": bucket, "pace": pace, "slug": slug, "blueprint": blueprint}
    save_session(session)

    build_status.write_status(
        slug, phase="advisory", message="Designing your AI organization…",
        session_id=session_id, bc_project_id=bucket, pace=pace, blueprint=blueprint,
        operator_email=user.email, project_name=(session.get("business_idea") or "")[:80],
    )
    myday_build_orchestrator.kick_build(session_id, bucket, pace, user.email, slug, idea_text, blueprint=blueprint)

    return RedirectResponse(url=f"/advisory/{session_id}/results?building=1", status_code=303)


@router.get("/{session_id}/build-status.json")
async def build_status_json(request: Request, session_id: str):
    """Polling endpoint for the buildout page's progress banner."""
    from fastapi.responses import JSONResponse
    from execution.advisory import build_status
    from execution.advisory.advisory_state_manager import load_session

    try:
        session = load_session(session_id)
    except FileNotFoundError:
        return JSONResponse({"phase": "unknown"}, headers={"Cache-Control": "no-store"})
    slug = (session.get("myday_build_target") or {}).get("slug") or session.get("linked_project_slug")
    if not slug:
        return JSONResponse({"phase": "starting", "percent": 0}, headers={"Cache-Control": "no-store"})
    status = build_status.read_status(slug) or {"phase": "starting", "percent": 0}
    return JSONResponse(status, headers={"Cache-Control": "no-store"})


# ─── Generation ─────────────────────────────────────────────────────

@router.get("/{session_id}/generate", dependencies=[Depends(require_advisory_enabled), Depends(rate_limit_advisory)])
async def generate_results(request: Request, session_id: str):
    """Run all generation engines and redirect to results."""
    from execution.ops_platform import runtime_controls
    if runtime_controls.is_paused("advisory_pipeline"):
        from fastapi.responses import HTMLResponse
        return HTMLResponse(
            "<h2>Advisory is temporarily paused</h2>"
            "<p>The AI advisory is paused by an administrator. Please check back shortly.</p>",
            status_code=503,
        )
    from execution.advisory.advisory_generation import generate_advisory_outputs
    from execution.advisory.event_tracker import track_event

    try:
        result = generate_advisory_outputs(session_id)
    except FileNotFoundError:
        return RedirectResponse(url="/advisory/", status_code=303)

    if not result.get("already_complete"):
        track_event(
            event_name="advisory_results_generated",
            session_id=session_id,
            email=result.get("email", ""),
        )

    return RedirectResponse(
        url=f"/advisory/{session_id}/results",
        status_code=303,
    )


# ─── Results ────────────────────────────────────────────────────────

@router.get("/{session_id}/results")
async def show_results(request: Request, session_id: str):
    """Show the org visualization and impact dashboard."""
    import json

    from execution.advisory.advisory_state_manager import load_session
    from execution.advisory.impact_calculator import format_currency
    from execution.advisory.maturity_scorer import get_dimension_label, get_maturity_label
    from execution.advisory.org_builder import flatten_org_tree, get_org_stats

    try:
        session = load_session(session_id)
    except FileNotFoundError:
        return RedirectResponse(url="/advisory/", status_code=303)

    if session.get("org_structure") is None:
        return RedirectResponse(
            url=f"/advisory/{session_id}/generate",
            status_code=303,
        )

    # Track outcome: viewed results
    try:
        from execution.advisory.outcome_tracker import record_outcome
        record_outcome(session_id, "viewed_results", {
            "capabilities": session.get("selected_capabilities", []),
            "domain": (session.get("problem_analysis") or {}).get("primary_problem", ""),
        })
    except Exception:
        pass

    org_tree = flatten_org_tree(session["org_structure"])
    org_stats = get_org_stats(session["org_structure"])
    maturity_label = get_maturity_label(session["maturity_score"]["overall"])

    # Agent stats
    from execution.advisory.agent_generator import get_agent_stats
    agents = session.get("agents", [])
    agent_stats = get_agent_stats(agents) if agents else {}

    # System flows, insights, timeline, personalized messaging
    from execution.advisory.system_flow_generator import (
        generate_implementation_timeline,
        generate_insights,
        generate_personalized_cost_of_inaction,
        generate_system_flows,
    )
    system_flows = generate_system_flows(session.get("selected_capabilities", []))
    insights = generate_insights(session)
    timeline = generate_implementation_timeline(session)
    inaction_messages = generate_personalized_cost_of_inaction(session)
    problem_analysis = session.get("problem_analysis")
    architecture = session.get("architecture")

    # Confidence score + optimization suggestions
    try:
        from execution.advisory.outcome_tracker import calculate_system_confidence, generate_optimization_suggestions
        confidence = calculate_system_confidence(session)
        optimization_suggestions = generate_optimization_suggestions(session)
    except Exception:
        confidence = {"score": 75, "based_on": 1, "factors": {}}
        optimization_suggestions = []

    return templates.TemplateResponse("org_visualization.html", {
        "request": request,
        "session": session,
        "org_tree_json": json.dumps(org_tree),
        "org_stats": org_stats,
        "maturity_label": maturity_label,
        "agents": agents,
        "agents_json": json.dumps(agents),
        "agent_stats": agent_stats,
        "system_flows": system_flows,
        "insights": insights,
        "timeline": timeline,
        "inaction_messages": inaction_messages,
        "problem_analysis": problem_analysis,
        "architecture": architecture,
        "confidence": confidence,
        "optimization_suggestions": optimization_suggestions,
        "get_dimension_label": get_dimension_label,
        "format_currency": format_currency,
        # My-Day build: drive the background-progress banner + poller.
        "myday_build": bool(session.get("myday_build")),
        "building": request.query_params.get("building") == "1",
        "build_bc_project_id": (session.get("myday_build_target") or {}).get("bc_project_id"),
    })


# ─── Simulation ─────────────────────────────────────────────────────

@router.get("/{session_id}/simulation")
async def simulation_page(request: Request, session_id: str):
    """Show the AI simulation page."""
    from execution.advisory.advisory_state_manager import load_session

    try:
        session = load_session(session_id)
    except FileNotFoundError:
        return RedirectResponse(url="/advisory/", status_code=303)

    if not session.get("agents"):
        return RedirectResponse(url=f"/advisory/{session_id}/results", status_code=303)

    from execution.advisory.agent_generator import get_agent_stats
    agent_stats = get_agent_stats(session.get("agents", []))

    import json
    agents = session.get("agents", [])

    return templates.TemplateResponse("simulation.html", {
        "request": request,
        "session": session,
        "agent_stats": agent_stats,
        "agents_json": json.dumps(agents),
    })


@router.get("/{session_id}/simulation/stream")
async def simulation_stream(request: Request, session_id: str):
    """Stream simulation events via Server-Sent Events (SSE)."""
    import asyncio
    import json

    from starlette.responses import StreamingResponse

    from execution.advisory.advisory_state_manager import load_session
    from execution.advisory.simulation_engine import run_simulation

    try:
        session = load_session(session_id)
    except FileNotFoundError:
        return JSONResponse({"error": "Session not found"}, status_code=404)

    simulation = run_simulation(session)

    async def event_generator():
        # Send start event
        yield f"data: {json.dumps({'type': 'start', 'total': simulation['total_events']})}\n\n"

        for i, event in enumerate(simulation["events"]):
            await asyncio.sleep(1.2 + (0.8 * (i % 3 == 0)))  # 1.2-2.0s between events
            yield f"data: {json.dumps({**event, 'type': event['type'], 'index': i})}\n\n"

        # Send summary
        await asyncio.sleep(1)
        yield f"data: {json.dumps({'type': 'complete', 'summary': simulation['summary']})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ─── Gate + Lead Capture ────────────────────────────────────────────

@router.get("/{session_id}/gate")
async def gate_page(request: Request, session_id: str):
    """Show the gated CTA page with lead capture form."""
    from execution.advisory.advisory_state_manager import load_session

    try:
        session = load_session(session_id)
    except FileNotFoundError:
        return RedirectResponse(url="/advisory/", status_code=303)

    return templates.TemplateResponse("gate_page.html", {
        "request": request,
        "session": session,
    })


@router.post("/{session_id}/save-lead", dependencies=[Depends(require_advisory_enabled)])
async def save_lead(
    request: Request,
    session_id: str,
):
    """Capture lead info, create/update lead record, generate PDF."""
    from execution.advisory.advisory_state_manager import (
        advance_status,
        load_session,
        record_lead,
        save_session,
        set_pdf_path,
    )
    from execution.advisory.advisory_to_lead_mapper import map_advisory_to_lead
    from execution.advisory.event_tracker import track_event

    try:
        session = load_session(session_id)
    except FileNotFoundError:
        return RedirectResponse(url="/advisory/", status_code=303)

    # Parse form data
    form_data = await request.form()
    name = str(form_data.get("name", "")).strip()
    email = str(form_data.get("email", "")).strip()
    company = str(form_data.get("company", "")).strip()
    role = str(form_data.get("role", "")).strip()
    visitor_fingerprint = str(form_data.get("visitor_fingerprint", "")).strip()

    # Store fingerprint on session for enterprise sync
    if visitor_fingerprint:
        session["visitor_fingerprint"] = visitor_fingerprint
        save_session(session)

    record_lead(session, name, email, company, role)
    advance_status(session, "gated")

    # Map to lead record + campaign enrollment
    try:
        map_advisory_to_lead(session)
    except Exception:
        logger.warning("Lead mapping failed", exc_info=True)

    # Generate PDF
    try:
        from execution.advisory.pdf_generator import generate_pdf
        pdf_path = generate_pdf(session)
        set_pdf_path(session, pdf_path)

        # Attach PDF to lead
        from execution.advisory.lead_manager import attach_pdf as lead_attach_pdf
        lead_attach_pdf(email, pdf_path)
    except Exception:
        logger.warning("PDF generation failed", exc_info=True)

    track_event(
        event_name="advisory_lead_captured",
        session_id=session_id,
        email=email,
        properties={"company": company, "role": role},
    )

    # Sync report completion to enterprise platform
    if session.get("pdf_path"):
        try:
            import asyncio
            from execution.advisory.enterprise_sync import send_enterprise_event
            asyncio.ensure_future(send_enterprise_event("report.completed", {
                "id": session_id,
                "userId": session_id,
                "downloadUrl": session.get("pdf_path", ""),
                "reportType": "advisory_assessment",
                "format": "pdf",
                "status": "completed",
                "user": {"email": email, "name": name, "role": role},
            }))
        except Exception:
            logger.warning("Enterprise PDF sync failed (non-blocking)", exc_info=True)

    # Track outcome: downloaded PDF (strong positive signal)
    try:
        from execution.advisory.outcome_tracker import record_outcome
        record_outcome(session_id, "downloaded_pdf", {
            "capabilities": session.get("selected_capabilities", []),
            "domain": (session.get("problem_analysis") or {}).get("primary_problem", ""),
        })
    except Exception:
        pass

    # Update linked project with lead contact info (non-blocking)
    try:
        from execution.advisory.advisory_to_project_mapper import create_project_from_advisory
        create_project_from_advisory(session_id)
    except Exception:
        logger.warning("Advisory-to-project update failed (non-blocking)", exc_info=True)

    return RedirectResponse(
        url=f"/advisory/{session_id}/gate?saved=true",
        status_code=303,
    )


@router.get("/{session_id}/download-pdf")
async def download_pdf(request: Request, session_id: str):
    """Serve the generated PDF report."""
    from execution.advisory.advisory_state_manager import load_session
    from execution.advisory.event_tracker import track_event

    try:
        session = load_session(session_id)
    except FileNotFoundError:
        return RedirectResponse(url="/advisory/", status_code=303)

    pdf_path = session.get("pdf_path")
    if pdf_path and Path(pdf_path).exists():
        track_event(
            event_name="advisory_pdf_downloaded",
            session_id=session_id,
            email=(session.get("lead") or {}).get("email", ""),
        )
        return FileResponse(
            path=pdf_path,
            filename="AI_Advisory_Report.pdf",
            media_type="application/pdf",
        )

    return RedirectResponse(
        url=f"/advisory/{session_id}/gate",
        status_code=303,
    )


# ─── Session Resume ────────────────────────────────────────────────

@router.get("/{session_id}/resume")
async def resume_session(request: Request, session_id: str):
    """Resume an existing session — redirect to the appropriate step."""
    from execution.advisory.advisory_state_manager import load_session

    try:
        session = load_session(session_id)
    except FileNotFoundError:
        return RedirectResponse(url="/advisory/", status_code=303)

    status = session.get("status", "idea_input")

    if status == "gated" and session.get("lead"):
        return RedirectResponse(url=f"/advisory/{session_id}/gate?saved=true", status_code=303)
    elif status in ("complete", "gated"):
        return RedirectResponse(url=f"/advisory/{session_id}/results", status_code=303)
    elif status == "generating":
        return RedirectResponse(url=f"/advisory/{session_id}/generate", status_code=303)
    else:
        return RedirectResponse(url=f"/advisory/{session_id}/questions", status_code=303)


# ─── Booking Integration ────────────────────────────────────────────

@router.post("/{session_id}/book-strategy-call", dependencies=[Depends(require_advisory_enabled)])
async def book_strategy_call(request: Request, session_id: str):
    """Record a strategy call booking event with full sales context."""
    from execution.advisory.advisory_state_manager import load_session
    from execution.advisory.advisory_to_lead_mapper import advance_campaign_for_session
    from execution.advisory.event_tracker import track_event

    try:
        session = load_session(session_id)
    except FileNotFoundError:
        return JSONResponse({"error": "Session not found"}, status_code=404)

    email = (session.get("lead") or {}).get("email", "")

    # Run/refresh revenue pipeline to prepare meeting context
    try:
        from execution.advisory.revenue_pipeline import run_revenue_pipeline
        if email:
            run_revenue_pipeline(email)
    except Exception:
        logger.warning("Revenue pipeline failed during booking", exc_info=True)

    advance_campaign_for_session(session, "Booked Strategy Call")
    track_event(
        event_name="strategy_call_booked",
        session_id=session_id,
        email=email,
    )

    # Sync booking to enterprise platform
    try:
        from execution.advisory.enterprise_sync import build_lead_payload, send_enterprise_event
        import asyncio
        payload = build_lead_payload(session)
        payload["booking"] = {"scheduled_at": "", "meet_link": "", "event_id": ""}
        asyncio.ensure_future(send_enterprise_event("strategy_call.booked", payload))
    except Exception:
        logger.warning("Enterprise booking sync failed (non-blocking)", exc_info=True)

    return JSONResponse({"status": "booked"})


# ─── Calendar Booking API ──────────────────────────────────────────

@router.get("/api/calendar/availability")
async def calendar_availability(request: Request):
    """Return available booking slots."""
    try:
        from execution.advisory.calendar_service import get_available_slots
        days = int(request.query_params.get("days", "21"))
        availability = get_available_slots(min(days, 60))
        return JSONResponse(availability)
    except Exception as e:
        logger.warning("Calendar availability failed", exc_info=True)
        return JSONResponse({"error": str(e), "dates": [], "timezone": "America/Chicago"}, status_code=200)


@router.post("/api/calendar/book", dependencies=[Depends(require_advisory_enabled)])
async def calendar_book(request: Request):
    """Book a strategy call."""
    try:
        body = await request.json()
        from execution.advisory.calendar_service import create_booking

        booking = create_booking(
            name=body.get("name", ""),
            email=body.get("email", ""),
            company=body.get("company", ""),
            phone=body.get("phone", ""),
            slot_start=body.get("slot_start", ""),
            session_id=body.get("session_id", ""),
        )

        # Sync booking to enterprise platform with real booking data
        try:
            import asyncio
            from execution.advisory.advisory_state_manager import load_session
            from execution.advisory.enterprise_sync import build_lead_payload, send_enterprise_event
            sid = body.get("session_id", "")
            if sid:
                session = load_session(sid)
                payload = build_lead_payload(session)
                payload["booking"] = {
                    "scheduled_at": booking.get("startTime", ""),
                    "meet_link": booking.get("meetLink", ""),
                    "event_id": booking.get("eventId", ""),
                    "prep_notes": _build_call_prep(session),
                }
                asyncio.ensure_future(send_enterprise_event("strategy_call.booked", payload))
        except Exception:
            logger.warning("Enterprise booking sync failed (non-blocking)", exc_info=True)

        return JSONResponse({"booking": booking})
    except Exception as e:
        logger.warning("Calendar booking failed", exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/{session_id}/book")
async def booking_page(request: Request, session_id: str):
    """Show the calendar booking page."""
    from execution.advisory.advisory_state_manager import load_session

    try:
        session = load_session(session_id)
    except FileNotFoundError:
        return RedirectResponse(url="/advisory/", status_code=303)

    return templates.TemplateResponse("booking.html", {
        "request": request,
        "session": session,
    })


# ─── Event Tracking API ────────────────────────────────────────────

@router.post("/api/event")
async def track_event_api(request: Request):
    """Track a frontend event (CTA clicks, page views, etc.)."""
    from execution.advisory.event_tracker import track_event

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    event = track_event(
        event_name=body.get("event_name", "unknown"),
        session_id=body.get("session_id", ""),
        email=body.get("email", ""),
        properties=body.get("properties", {}),
        utm_params=body.get("utm_params", {}),
    )

    return JSONResponse({"event_id": event["event_id"]})


def _build_call_prep(session: dict) -> str:
    """Build strategy call prep notes from session data."""
    lines = []
    lead = session.get("lead") or {}
    if lead.get("company"):
        lines.append(f"Company: {lead['company']}")
    if session.get("business_idea"):
        lines.append(f"Business Idea: {session['business_idea'][:300]}")

    problem = session.get("problem_analysis") or {}
    if problem.get("primary_label"):
        lines.append(f"Primary Focus: {problem['primary_label']}")

    maturity = session.get("maturity_score") or {}
    if isinstance(maturity, dict) and maturity.get("overall"):
        lines.append(f"AI Readiness: {maturity['overall']}/5")

    impact = session.get("impact_model") or {}
    roi = impact.get("roi_summary", {})
    if roi.get("annual_benefit"):
        from execution.advisory.impact_calculator import format_currency
        lines.append(f"Projected Annual Benefit: {format_currency(roi['annual_benefit'])}")

    # Key Q&A
    for a in session.get("answers", [])[:5]:
        q = a.get("question_text", "")
        ans = a.get("answer_text", "")
        if q and ans:
            lines.append(f"Q: {q[:80]}")
            lines.append(f"A: {ans[:200]}")

    return "\n".join(lines)


# ─── Lead Admin View ────────────────────────────────────────────────

@router.get("/admin/leads")
async def admin_leads(request: Request):
    """Show the lead management admin view."""
    from execution.advisory.campaign_manager import (
        ensure_advisory_campaign,
        get_campaign_stats,
        get_enrollments_by_campaign,
    )
    from execution.advisory.lead_manager import list_all_leads

    leads = list_all_leads()
    campaign = ensure_advisory_campaign()
    stats = get_campaign_stats(campaign["campaign_id"])
    enrollments = get_enrollments_by_campaign(campaign["campaign_id"])

    # Build enrollment lookup by email
    enrollment_map = {e["email"]: e for e in enrollments}

    return templates.TemplateResponse("admin_leads.html", {
        "request": request,
        "leads": leads,
        "campaign": campaign,
        "campaign_stats": stats,
        "enrollment_map": enrollment_map,
    })


@router.get("/admin/leads/{lead_id}")
async def admin_lead_detail(request: Request, lead_id: str):
    """Show detailed lead info including advisory session data."""
    from execution.advisory.advisory_state_manager import load_session
    from execution.advisory.campaign_manager import get_enrollments_by_email
    from execution.advisory.event_tracker import get_events
    from execution.advisory.impact_calculator import format_currency
    from execution.advisory.lead_manager import get_lead_by_id

    lead = get_lead_by_id(lead_id)
    if not lead:
        return RedirectResponse(url="/advisory/admin/leads", status_code=303)

    # Load linked advisory sessions
    sessions = []
    for sid in lead.get("advisory_session_ids", []):
        try:
            sessions.append(load_session(sid))
        except FileNotFoundError:
            pass

    enrollments = get_enrollments_by_email(lead["email"])
    events = get_events(email=lead["email"], limit=50)

    return templates.TemplateResponse("admin_lead_detail.html", {
        "request": request,
        "lead": lead,
        "sessions": sessions,
        "enrollments": enrollments,
        "events": events,
        "format_currency": format_currency,
    })
