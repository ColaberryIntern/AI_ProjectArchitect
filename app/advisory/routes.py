"""FastAPI routes for the AI Advisory layer.

Implements the executive-facing advisory wizard flow:
landing → idea input → 10 questions → generate → results → gate → lead capture → PDF

Integrates with lead management, campaign enrollment, and event tracking.
"""

import logging
from pathlib import Path

from fastapi import APIRouter, Form, Request
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


# ─── Landing & Session Start ────────────────────────────────────────

@router.get("/")
async def advisory_landing(request: Request):
    """Render the advisory landing page."""
    return templates.TemplateResponse("advisory_landing.html", {"request": request})


@router.post("/start")
async def start_session(request: Request, business_idea: str = Form(...)):
    """Create a new advisory session and redirect to questions."""
    from execution.advisory.advisory_state_manager import advance_status, initialize_session
    from execution.advisory.event_tracker import track_event

    session = initialize_session(business_idea)
    advance_status(session, "questioning")

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

    question = get_next_question(session)
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
    })


@router.post("/{session_id}/answer")
async def submit_answer(
    request: Request,
    session_id: str,
):
    """Record an answer and redirect to next question or generation."""
    from execution.advisory.advisory_state_manager import (
        load_session,
        record_answer,
        save_session,
        set_selected_systems,
    )
    from execution.advisory.question_engine import get_next_question, is_complete

    try:
        session = load_session(session_id)
    except FileNotFoundError:
        return RedirectResponse(url="/advisory/", status_code=303)

    # Parse form data manually to handle multi-value checkbox fields
    form_data = await request.form()
    answer_text = str(form_data.get("answer_text", "")).strip()
    email = str(form_data.get("email", "")).strip()
    selected_systems = [str(v) for v in form_data.getlist("selected_systems")]

    # Capture soft email if provided
    if email and not session.get("email"):
        session["email"] = email.strip()
        save_session(session)

    question = get_next_question(session)
    if question and answer_text:
        record_answer(session, question["id"], question["text"], answer_text)

    if selected_systems:
        set_selected_systems(session, selected_systems)

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
    })


@router.post("/{session_id}/design")
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
    })


@router.post("/{session_id}/capabilities")
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

    return RedirectResponse(
        url=f"/advisory/{session_id}/generate",
        status_code=303,
    )


# ─── Generation ─────────────────────────────────────────────────────

@router.get("/{session_id}/generate")
async def generate_results(request: Request, session_id: str):
    """Run all generation engines and redirect to results."""
    from execution.advisory.advisory_state_manager import (
        advance_status,
        load_session,
        set_capability_map,
        set_impact_model,
        set_maturity_score,
        set_org_structure,
    )
    from execution.advisory.business_interpreter import interpret_answers
    from execution.advisory.event_tracker import track_event
    from execution.advisory.impact_calculator import calculate_impact
    from execution.advisory.maturity_scorer import score_maturity
    from execution.advisory.org_builder import build_org_structure

    try:
        session = load_session(session_id)
    except FileNotFoundError:
        return RedirectResponse(url="/advisory/", status_code=303)

    # Skip regeneration if already complete
    if session.get("capability_map") is not None and session.get("org_structure") is not None:
        return RedirectResponse(
            url=f"/advisory/{session_id}/results",
            status_code=303,
        )

    # Generate all outputs
    answers = session.get("answers", [])
    business_idea = session.get("business_idea", "")
    selected_caps = session.get("selected_capabilities", [])

    capability_map = interpret_answers(answers, business_idea, selected_capability_ids=selected_caps)
    set_capability_map(session, capability_map)

    maturity = score_maturity(answers, capability_map)
    set_maturity_score(session, maturity)

    # Analyze primary problem for weighted architecture
    from execution.advisory.problem_analyzer import analyze_problems
    problem_analysis = analyze_problems(session)

    # Generate problem-weighted agent architecture
    from execution.advisory.advisory_state_manager import set_agents
    from execution.advisory.agent_generator import generate_agents
    from execution.advisory.capability_mapper import should_include_cory

    include_cory = should_include_cory(session)
    agents = generate_agents(selected_caps, include_cory=include_cory, problem_analysis=problem_analysis)
    set_agents(session, agents)

    # Store problem analysis on session for results page
    from execution.advisory.advisory_state_manager import save_session
    session["problem_analysis"] = problem_analysis
    save_session(session)

    org_nodes = build_org_structure(capability_map, maturity, business_idea)
    set_org_structure(session, org_nodes)

    impact = calculate_impact(capability_map, maturity, answers, business_idea)
    set_impact_model(session, impact)

    advance_status(session, "complete")

    track_event(
        event_name="advisory_results_generated",
        session_id=session_id,
        email=session.get("email", ""),
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
        "get_dimension_label": get_dimension_label,
        "format_currency": format_currency,
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

    return templates.TemplateResponse("simulation.html", {
        "request": request,
        "session": session,
        "agent_stats": agent_stats,
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


@router.post("/{session_id}/save-lead")
async def save_lead(
    request: Request,
    session_id: str,
    name: str = Form(...),
    email: str = Form(...),
    company: str = Form(default=""),
    role: str = Form(default=""),
):
    """Capture lead info, create/update lead record, generate PDF."""
    from execution.advisory.advisory_state_manager import (
        advance_status,
        load_session,
        record_lead,
        set_pdf_path,
    )
    from execution.advisory.advisory_to_lead_mapper import map_advisory_to_lead
    from execution.advisory.event_tracker import track_event

    try:
        session = load_session(session_id)
    except FileNotFoundError:
        return RedirectResponse(url="/advisory/", status_code=303)

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

@router.post("/{session_id}/book-strategy-call")
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


@router.post("/api/calendar/book")
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
