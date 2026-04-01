"""Smart recommendation engine for AI Advisory.

Generates prioritized recommendations for systems, capabilities,
and implementation strategy based on the full session context.
"""

from execution.advisory.capability_mapper import AI_SYSTEMS, BUSINESS_OUTCOMES


def recommend_design(session: dict) -> dict:
    """Recommend outcomes + systems with a SINGLE primary goal focus.

    Forces exactly 1 primary outcome (highest keyword match) and
    recommends only systems aligned with that primary goal.

    Returns:
        Dict with recommended_outcomes, recommended_systems, primary_goal, and reasoning.
    """
    answers = session.get("answers", [])
    idea = session.get("business_idea", "").lower()
    all_text = idea + " " + " ".join(a.get("answer_text", "") for a in answers).lower()

    # ── Score each outcome by keyword density ───────────────────────
    outcome_keywords = {
        "increase_revenue": ["revenue", "sales", "lead", "conversion", "pipeline", "close", "deal", "prospect", "outreach", "quota"],
        "reduce_costs": ["cost", "manual", "automat", "efficien", "reduce", "save", "expensive", "waste", "overhead"],
        "improve_cx": ["customer", "support", "response", "satisfaction", "service", "chat", "ticket", "complaint", "nps"],
        "scale_operations": ["scale", "grow", "expand", "volume", "hire", "headcount", "capacity", "throughput"],
        "improve_decisions": ["data", "decision", "report", "dashboard", "analytics", "insight", "visibility", "metric"],
    }

    outcome_scores = {}
    for outcome_id, keywords in outcome_keywords.items():
        score = sum(1 for kw in keywords if kw in all_text)
        if score > 0:
            outcome_scores[outcome_id] = score

    # Pick PRIMARY (highest score) + at most 1 secondary
    rec_outcomes = {}
    if outcome_scores:
        sorted_outcomes = sorted(outcome_scores.items(), key=lambda x: x[1], reverse=True)
        primary_id = sorted_outcomes[0][0]
        primary_label = _get_outcome_label_for_rec(primary_id)
        rec_outcomes[primary_id] = f"Primary goal: {primary_label}"

        # Add 1 secondary only if it has a strong signal (3+ keyword matches)
        if len(sorted_outcomes) > 1 and sorted_outcomes[1][1] >= 3:
            sec_id = sorted_outcomes[1][0]
            rec_outcomes[sec_id] = f"Secondary: {_get_outcome_label_for_rec(sec_id)}"
    else:
        # No keywords matched — default to reduce_costs (universal)
        rec_outcomes["reduce_costs"] = "Primary goal: Reduce operational costs"

    primary_goal = list(rec_outcomes.keys())[0]

    # ── System recommendations aligned with primary goal ────────────
    goal_systems = {
        "increase_revenue": ["revenue_engine", "communication_engine"],
        "reduce_costs": ["operations_engine", "finance_engine"],
        "improve_cx": ["customer_engine", "communication_engine"],
        "scale_operations": ["operations_engine", "intelligence_engine"],
        "improve_decisions": ["intelligence_engine", "operations_engine"],
    }

    rec_systems = {}
    for sys_id in goal_systems.get(primary_goal, ["operations_engine"]):
        sys_label = next((s["label"] for s in AI_SYSTEMS if s["id"] == sys_id), sys_id)
        rec_systems[sys_id] = f"Core system for {_get_outcome_label_for_rec(primary_goal)}"

    # Add intelligence engine if 2+ systems
    if len(rec_systems) >= 2 and "intelligence_engine" not in rec_systems:
        rec_systems["intelligence_engine"] = "Coordinates your AI systems"

    return {
        "recommended_outcomes": rec_outcomes,
        "recommended_systems": rec_systems,
    }


def _get_outcome_label_for_rec(outcome_id: str) -> str:
    labels = {
        "increase_revenue": "Increase Revenue",
        "reduce_costs": "Reduce Operational Costs",
        "improve_cx": "Improve Customer Experience",
        "scale_operations": "Scale Operations",
        "improve_decisions": "Improve Decision-Making",
    }
    return labels.get(outcome_id, outcome_id.replace("_", " ").title())


def generate_recommendations(session: dict) -> dict:
    """Generate smart recommendations based on full session context.

    Returns:
        Dict with suggested_systems, suggested_capabilities,
        priority_ranking, and implementation_phases.
    """
    outcomes = session.get("selected_outcomes", [])
    answers = session.get("answers", [])
    idea = session.get("business_idea", "").lower()
    all_text = idea + " " + " ".join(a.get("answer_text", "") for a in answers).lower()

    suggested_systems = _suggest_systems(outcomes, all_text)
    priority = _build_priority_ranking(session)
    phases = _build_implementation_phases(session)

    return {
        "suggested_systems": suggested_systems,
        "priority_ranking": priority,
        "implementation_phases": phases,
    }


def _suggest_systems(outcomes: list[str], context_text: str) -> list[dict]:
    """Suggest AI systems based on outcomes and context."""
    suggestions = []

    # Direct outcome → system mapping
    outcome_systems = {
        "increase_revenue": ["revenue_engine"],
        "reduce_costs": ["operations_engine", "finance_engine"],
        "improve_cx": ["customer_engine", "communication_engine"],
        "scale_operations": ["operations_engine", "intelligence_engine"],
        "improve_decisions": ["intelligence_engine", "finance_engine"],
    }

    scored = {}
    for outcome_id in outcomes:
        for sys_id in outcome_systems.get(outcome_id, []):
            scored[sys_id] = scored.get(sys_id, 0) + 30

    # Keyword-based system suggestions
    keyword_systems = {
        "sales": "revenue_engine", "lead": "revenue_engine", "revenue": "revenue_engine",
        "customer": "customer_engine", "support": "customer_engine", "service": "customer_engine",
        "operation": "operations_engine", "process": "operations_engine", "workflow": "operations_engine",
        "finance": "finance_engine", "invoice": "finance_engine", "accounting": "finance_engine",
        "email": "communication_engine", "meeting": "communication_engine",
        "data": "intelligence_engine", "analytics": "intelligence_engine", "report": "intelligence_engine",
    }
    for keyword, sys_id in keyword_systems.items():
        if keyword in context_text:
            scored[sys_id] = scored.get(sys_id, 0) + 10

    for sys in AI_SYSTEMS:
        score = scored.get(sys["id"], 0)
        if score > 0:
            suggestions.append({
                "system_id": sys["id"],
                "label": sys["label"],
                "score": score,
                "reason": f"Aligns with your business goals and context",
            })

    suggestions.sort(key=lambda x: x["score"], reverse=True)
    return suggestions


def _build_priority_ranking(session: dict) -> list[dict]:
    """Rank capabilities by implementation priority."""
    caps = session.get("selected_capabilities", [])
    from execution.advisory.capability_catalog import get_capabilities_by_ids
    selected = get_capabilities_by_ids(caps)

    # Priority: Sales/Support first (fastest ROI), then Operations, then others
    dept_priority = {
        "Sales": 1, "Customer Support": 2, "Marketing": 3,
        "Operations": 4, "Finance": 5, "Communication": 6,
        "Human Resources": 7, "Technology": 8,
    }

    ranked = sorted(selected, key=lambda c: dept_priority.get(c["department"], 9))
    return [
        {"capability_id": c["id"], "name": c["name"], "department": c["department"],
         "priority": i + 1}
        for i, c in enumerate(ranked)
    ]


def _build_implementation_phases(session: dict) -> list[dict]:
    """Suggest phased implementation plan."""
    caps = session.get("selected_capabilities", [])
    total = len(caps)

    if total <= 5:
        return [{"phase": 1, "label": "Full Deployment", "duration": "4-8 weeks",
                 "description": "Deploy all selected capabilities in a single phase"}]

    # Split into phases
    third = max(total // 3, 2)
    return [
        {"phase": 1, "label": "Quick Wins", "duration": "2-4 weeks",
         "count": third,
         "description": "Deploy highest-impact, lowest-complexity capabilities first"},
        {"phase": 2, "label": "Core Build", "duration": "4-8 weeks",
         "count": third,
         "description": "Implement core operational and customer-facing capabilities"},
        {"phase": 3, "label": "Full Scale", "duration": "8-12 weeks",
         "count": total - (third * 2),
         "description": "Complete deployment with advanced analytics and intelligence"},
    ]
