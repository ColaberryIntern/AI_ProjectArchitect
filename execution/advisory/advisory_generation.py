"""The advisory generation pipeline, callable outside an HTTP request.

This is the 9-stage body that used to live inline in
``app/advisory/routes.py::generate_results`` (industry → capability map →
maturity → problem analysis → agents → org → impact → architecture), followed
by auto-creating the Project Builder project. Extracted so both the public
``/advisory/{id}/generate`` route and the My-Day background build orchestrator
run the exact same generation.

Behavior is identical to the old inline route: same stage order, same
intermediate ``save_session`` calls, same non-blocking project creation.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def generate_advisory_outputs(session_id: str) -> dict:
    """Run the advisory generation for a session and auto-create its project.

    Idempotent: if the session is already generated (capability_map +
    org_structure present), returns the existing linked slug without
    regenerating.

    Returns ``{"session_id", "slug", "email", "already_complete"}`` where
    ``slug`` is the linked Project Builder slug (or None if creation failed).
    Raises ``FileNotFoundError`` if the session does not exist.
    """
    from execution.advisory.advisory_state_manager import (
        advance_status,
        load_session,
        save_session,
        set_agents,
        set_capability_map,
        set_impact_model,
        set_maturity_score,
        set_org_structure,
    )
    from execution.advisory.agent_generator import generate_agents
    from execution.advisory.architecture_builder import build_architecture
    from execution.advisory.advisory_to_project_mapper import create_project_from_advisory
    from execution.advisory.business_interpreter import interpret_answers
    from execution.advisory.capability_mapper import should_include_cory
    from execution.advisory.impact_calculator import calculate_impact
    from execution.advisory.maturity_scorer import score_maturity
    from execution.advisory.org_builder import build_org_structure
    from execution.advisory.problem_analyzer import analyze_problems
    from execution.advisory.taxonomy_registry import lookup_taxonomy

    session = load_session(session_id)
    email = session.get("email", "")

    # Skip regeneration if already complete.
    if session.get("capability_map") is not None and session.get("org_structure") is not None:
        return {
            "session_id": session_id,
            "slug": session.get("linked_project_slug"),
            "email": email,
            "already_complete": True,
        }

    answers = session.get("answers", [])
    # Prefer the refined idea from the 9-phase discovery (My-Day build flow);
    # the public funnel never sets it, so it falls back to the raw idea.
    business_idea = session.get("refined_idea") or session.get("business_idea", "")
    selected_caps = session.get("selected_capabilities", [])

    # ── Industry Detection (taxonomy registry: seed → cache → sync LLM) ──
    answer_text = " ".join(a.get("answer_text", "") for a in answers)
    industry_text = business_idea
    if answers:
        industry_text = f"{business_idea}\n\n{answers[0].get('answer_text', '')}"
    try:
        industry_profile = lookup_taxonomy(industry_text.strip(), business_idea + " " + answer_text)
    except Exception:
        industry_profile = None

    if industry_profile:
        meta = industry_profile.get("_meta", {})
        industry_id = meta.get("industry_key") or ""
        industry_confidence = {"seed": 0.85, "registry": 0.80, "generated": 0.60}.get(meta.get("source"), 0.5)
        industry_label = industry_profile.get("label", industry_id)
    else:
        industry_id, industry_confidence, industry_label = "", 0.0, ""

    session["industry"] = {
        "id": industry_id,
        "label": industry_label,
        "confidence": industry_confidence,
        "source": (industry_profile or {}).get("_meta", {}).get("source"),
    }
    save_session(session)

    capability_map = interpret_answers(answers, business_idea, selected_capability_ids=selected_caps)
    set_capability_map(session, capability_map)

    maturity = score_maturity(answers, capability_map)
    set_maturity_score(session, maturity)

    # Analyze primary problem for weighted architecture
    problem_analysis = analyze_problems(session)

    # Generate problem-weighted agent architecture with industry context
    include_cory = should_include_cory(session)
    agents = generate_agents(
        selected_caps, include_cory=include_cory,
        problem_analysis=problem_analysis, industry_profile=industry_profile,
    )
    set_agents(session, agents)

    session["problem_analysis"] = problem_analysis
    save_session(session)

    org_nodes = build_org_structure(capability_map, maturity, business_idea)
    set_org_structure(session, org_nodes)

    impact = calculate_impact(capability_map, maturity, answers, business_idea, industry_profile=industry_profile)
    set_impact_model(session, impact)

    # Build structured architecture (engines, dependencies, flows)
    architecture = build_architecture(selected_caps, include_coo=include_cory)
    session["architecture"] = architecture
    save_session(session)

    advance_status(session, "complete")

    # Auto-create Project Builder project (non-blocking).
    try:
        create_project_from_advisory(session_id)
    except Exception:
        logger.warning("Advisory-to-project creation failed (non-blocking)", exc_info=True)

    # Re-load to pick up linked_project_slug set by the mapper.
    session = load_session(session_id)
    return {
        "session_id": session_id,
        "slug": session.get("linked_project_slug"),
        "email": email,
        "already_complete": False,
    }
