"""Maps completed advisory sessions into Project Builder projects.

Creates a pre-filled project in the Project Builder system so the user
goes from advisory vision straight into execution without starting from scratch.
"""

import logging
import re

from execution.advisory.advisory_state_manager import load_session, set_linked_project

logger = logging.getLogger(__name__)


def _slugify(name: str) -> str:
    """Convert a project name to a URL-safe slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def _extract_answer(session: dict, question_id: str) -> str:
    """Extract an answer by question ID from session answers."""
    for a in session.get("answers", []):
        if a.get("question_id") == question_id:
            return a.get("answer_text", "")
    return ""


def _extract_company_name(session: dict) -> str:
    """Extract company name from lead info or Q1 answer."""
    lead = session.get("lead") or {}
    if lead.get("company"):
        return lead["company"]
    # Try to extract from Q1
    q1 = _extract_answer(session, "q1_business_overview")
    if q1:
        return q1[:60]
    return "Organization"


def _extract_primary_system(session: dict) -> str:
    """Determine the primary AI system from session data."""
    problem = (session.get("problem_analysis") or {}).get("primary_problem", "")
    system_map = {
        "sales": "Sales Engine",
        "operations": "Operations Engine",
        "customer_support": "Support Engine",
        "marketing": "Marketing Engine",
        "finance": "Finance Engine",
        "hr": "HR Engine",
        "logistics": "Logistics Engine",
        "data": "Intelligence Engine",
    }
    for key, label in system_map.items():
        if key in problem.lower():
            return label

    # Fallback to selected outcomes
    outcomes = session.get("selected_outcomes", [])
    if outcomes:
        outcome_map = {
            "reduce_costs": "Operations Engine",
            "grow_revenue": "Sales Engine",
            "improve_cx": "Support Engine",
            "scale_operations": "Operations Engine",
        }
        for oid in outcomes:
            if oid in outcome_map:
                return outcome_map[oid]

    return "Workforce System"


def generate_project_name(session: dict) -> str:
    """Generate a project name in the format: '{Company} - AI {Primary System}'.

    Args:
        session: The advisory session dict.

    Returns:
        A descriptive project name.
    """
    company = _extract_company_name(session)
    system = _extract_primary_system(session)
    return f"{company} - AI {system}"


def map_advisory_to_project_text(session: dict) -> str:
    """Generate a rich, structured idea text from an advisory session.

    This becomes the pre-filled content in the Project Builder's idea intake
    textarea, giving the system all the context it needs to generate a
    complete project profile automatically.

    Args:
        session: The advisory session dict (must have answers, capability_map, etc.)

    Returns:
        A long-form structured text suitable for the idea intake field.
    """
    sections = []

    # 1. Company Overview
    q1 = _extract_answer(session, "q1_business_overview")
    q2 = _extract_answer(session, "q2_company_size")
    q3 = _extract_answer(session, "q3_departments")
    if q1 or q2:
        overview = "COMPANY OVERVIEW\n"
        if q1:
            overview += f"{q1}\n"
        if q2:
            overview += f"Company size: {q2}\n"
        if q3:
            overview += f"Key departments: {q3}\n"
        sections.append(overview.strip())

    # 2. Business Problem
    q4 = _extract_answer(session, "q4_bottlenecks")
    problem = (session.get("problem_analysis") or {})
    if q4 or problem.get("primary_problem"):
        prob_text = "BUSINESS PROBLEM\n"
        if q4:
            prob_text += f"Current bottlenecks: {q4}\n"
        if problem.get("primary_problem"):
            prob_text += f"Primary challenge area: {problem['primary_problem']}\n"
        if problem.get("domain_scores"):
            top_domains = sorted(
                problem["domain_scores"].items(),
                key=lambda x: x[1],
                reverse=True,
            )[:3]
            if top_domains:
                prob_text += "Top affected areas: " + ", ".join(
                    f"{d} ({s:.0%})" for d, s in top_domains
                ) + "\n"
        sections.append(prob_text.strip())

    # 3. Objectives
    q10 = _extract_answer(session, "q10_success_vision")
    outcomes = session.get("selected_outcomes", [])
    if q10 or outcomes:
        obj_text = "OBJECTIVES\n"
        if q10:
            obj_text += f"12-month success vision: {q10}\n"
        if outcomes:
            obj_text += f"Selected business outcomes: {', '.join(outcomes)}\n"
        sections.append(obj_text.strip())

    # 4. Proposed AI System
    cap_map = session.get("capability_map") or {}
    agents = session.get("agents") or []
    architecture = session.get("architecture") or {}
    if cap_map.get("departments") or agents:
        sys_text = "PROPOSED AI SYSTEM\n"
        if architecture.get("engines"):
            engine_names = [e.get("name", "") for e in architecture["engines"]]
            sys_text += f"Core engines: {', '.join(engine_names)}\n"
        if agents:
            agent_names = [a.get("name", "") for a in agents[:10]]
            sys_text += f"AI agents ({len(agents)} total): {', '.join(agent_names)}\n"
        sections.append(sys_text.strip())

    # 5. Departments
    if cap_map.get("departments"):
        dept_text = "DEPARTMENTS AND CAPABILITIES\n"
        for dept in cap_map["departments"]:
            dept_name = dept.get("name", "")
            caps = [c.get("name", "") for c in dept.get("capabilities", [])]
            if dept_name and caps:
                dept_text += f"- {dept_name}: {', '.join(caps)}\n"
        sections.append(dept_text.strip())

    # 6. AI Workforce
    if agents:
        wf_text = "AI WORKFORCE\n"
        for agent in agents[:8]:
            name = agent.get("name", "")
            role = agent.get("role", "")
            if name:
                wf_text += f"- {name}: {role}\n"
        if len(agents) > 8:
            wf_text += f"  ... and {len(agents) - 8} more agents\n"
        sections.append(wf_text.strip())

    # 7. Current Tools & Systems
    q6 = _extract_answer(session, "q6_current_tools")
    selected_systems = session.get("selected_systems", [])
    if q6 or selected_systems:
        tools_text = "CURRENT SYSTEMS\n"
        if q6:
            tools_text += f"Daily tools: {q6}\n"
        if selected_systems:
            tools_text += f"Integration targets: {', '.join(selected_systems)}\n"
        sections.append(tools_text.strip())

    # 8. Financial Impact
    impact = session.get("impact_model") or {}
    q9 = _extract_answer(session, "q9_budget_timeline")
    if impact or q9:
        fin_text = "FINANCIAL IMPACT\n"
        if q9:
            fin_text += f"Budget and timeline: {q9}\n"
        cost_savings = impact.get("cost_savings", {})
        revenue = impact.get("revenue_impact", {})
        if cost_savings.get("total_annual"):
            fin_text += f"Projected annual cost savings: ${cost_savings['total_annual']:,.0f}\n"
        if revenue.get("estimated_annual_revenue_gain"):
            fin_text += f"Projected annual revenue gain: ${revenue['estimated_annual_revenue_gain']:,.0f}\n"
        roi = impact.get("roi_summary", {})
        if roi.get("payback_months"):
            fin_text += f"Estimated payback: {roi['payback_months']} months\n"
        sections.append(fin_text.strip())

    if not sections:
        # Minimal fallback
        idea = session.get("business_idea", "")
        return idea if idea else "AI system design from advisory session"

    return "\n\n".join(sections)


def create_project_from_advisory(session_id: str) -> dict | None:
    """Create a Project Builder project from a completed advisory session.

    This is the main entry point. It:
    1. Loads the advisory session
    2. Checks for duplicate (already linked project)
    3. Generates a project name and pre-filled idea text
    4. Creates the project in the Project Builder
    5. Records the idea on the project
    6. Links the advisory session to the project
    7. Stores advisory metadata on the project state

    Args:
        session_id: The advisory session UUID.

    Returns:
        The created project state dict, or None if creation was skipped/failed.
    """
    try:
        session = load_session(session_id)
    except FileNotFoundError:
        logger.warning(f"[AdvisoryToProject] Session {session_id} not found")
        return None

    # Duplicate protection: skip if already linked
    if session.get("linked_project_slug"):
        logger.info(
            f"[AdvisoryToProject] Session {session_id} already linked to "
            f"project '{session['linked_project_slug']}'"
        )
        return None

    # Generate project data
    project_name = generate_project_name(session)
    idea_text = map_advisory_to_project_text(session)

    # Check if a project with this slug already exists
    from execution.state_manager import _slugify as project_slugify
    slug = project_slugify(project_name)

    from config.settings import OUTPUT_DIR
    state_path = OUTPUT_DIR / slug / "project_state.json"
    if state_path.exists():
        logger.info(f"[AdvisoryToProject] Project '{slug}' already exists, linking")
        set_linked_project(session, slug)
        return None

    try:
        # Create the project (idea is NOT recorded here — it's pre-filled
        # on the idea intake page so the user can review/edit before submitting)
        from execution.state_manager import initialize_state, save_state

        state = initialize_state(project_name)

        # Store the advisory idea text for pre-fill (not yet "captured")
        state["advisory_prefill"] = idea_text

        # Store advisory metadata on the project
        state["advisory"] = {
            "source": "advisory",
            "advisory_session_id": session_id,
            "company_name": _extract_company_name(session),
            "contact_name": (session.get("lead") or {}).get("name", ""),
            "contact_email": (session.get("lead") or {}).get("email", "") or session.get("email", ""),
            "role": (session.get("lead") or {}).get("role", ""),
            "industry": _extract_industry(session),
            "selected_capabilities": session.get("selected_capabilities", []),
            "selected_outcomes": session.get("selected_outcomes", []),
        }

        save_state(state, state["project"]["slug"])

        # Link advisory session back to this project
        set_linked_project(session, state["project"]["slug"])

        logger.info(
            f"[AdvisoryToProject] Created project '{project_name}' "
            f"(slug: {state['project']['slug']}) from advisory session {session_id}"
        )
        return state

    except Exception as e:
        logger.error(f"[AdvisoryToProject] Failed to create project: {e}", exc_info=True)
        return None


def _extract_industry(session: dict) -> str:
    """Extract industry hint from Q1 answer."""
    q1 = _extract_answer(session, "q1_business_overview")
    return q1[:100] if q1 else ""
