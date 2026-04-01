"""Smart recommendation engine for AI Advisory.

Generates prioritized recommendations for systems, capabilities,
and implementation strategy based on the full session context.
"""

from execution.advisory.capability_mapper import AI_SYSTEMS, BUSINESS_OUTCOMES


def recommend_design(session: dict) -> dict:
    """Recommend outcomes + systems based on the user's answers and business idea.

    Returns:
        Dict with recommended_outcomes, recommended_systems, and reasoning per item.
    """
    answers = session.get("answers", [])
    idea = session.get("business_idea", "").lower()
    all_text = idea + " " + " ".join(a.get("answer_text", "") for a in answers).lower()

    rec_outcomes = {}  # outcome_id -> reason
    rec_systems = {}   # system_id -> reason

    # ── Outcome recommendations from keywords ───────────────────────
    if any(kw in all_text for kw in ("revenue", "sales", "lead", "conversion", "pipeline", "close")):
        rec_outcomes["increase_revenue"] = "Your business mentions sales, leads, or revenue growth"
    if any(kw in all_text for kw in ("cost", "manual", "automat", "efficien", "reduce", "save")):
        rec_outcomes["reduce_costs"] = "You described manual processes or cost concerns"
    if any(kw in all_text for kw in ("customer", "support", "response", "satisfaction", "service", "chat")):
        rec_outcomes["improve_cx"] = "Customer experience and support are key to your business"
    if any(kw in all_text for kw in ("scale", "grow", "expand", "volume", "hire", "headcount")):
        rec_outcomes["scale_operations"] = "You want to grow without proportional headcount"
    if any(kw in all_text for kw in ("data", "decision", "report", "dashboard", "analytics", "insight")):
        rec_outcomes["improve_decisions"] = "You mentioned data-driven decisions or reporting needs"

    # Default: always recommend at least 2
    if len(rec_outcomes) < 2:
        if "reduce_costs" not in rec_outcomes:
            rec_outcomes["reduce_costs"] = "Most organizations benefit from operational cost reduction"
        if "improve_decisions" not in rec_outcomes:
            rec_outcomes["improve_decisions"] = "Data-driven decisions accelerate business outcomes"

    # ── System recommendations from keywords ────────────────────────
    keyword_to_system = {
        "sales": ("revenue_engine", "Matches your sales and revenue focus"),
        "lead": ("revenue_engine", "Lead management is core to your growth"),
        "revenue": ("revenue_engine", "Directly supports your revenue goals"),
        "marketing": ("revenue_engine", "Campaign and content automation for growth"),
        "customer": ("customer_engine", "Customer experience is central to your business"),
        "support": ("customer_engine", "Support automation reduces costs and improves satisfaction"),
        "ticket": ("customer_engine", "Ticket management and triage automation"),
        "operation": ("operations_engine", "Operational efficiency is a key objective"),
        "process": ("operations_engine", "Process automation for your workflows"),
        "workflow": ("operations_engine", "Workflow optimization across departments"),
        "schedule": ("operations_engine", "Scheduling and resource optimization"),
        "inventory": ("operations_engine", "Inventory and supply chain management"),
        "invoice": ("finance_engine", "Financial processing and automation"),
        "finance": ("finance_engine", "Financial intelligence and forecasting"),
        "accounting": ("finance_engine", "Accounting and expense automation"),
        "email": ("communication_engine", "Email and communication automation"),
        "meeting": ("communication_engine", "Meeting and collaboration efficiency"),
        "document": ("communication_engine", "Document management and knowledge base"),
        "data": ("intelligence_engine", "Data infrastructure and analytics"),
        "report": ("intelligence_engine", "Reporting and business intelligence"),
        "analytics": ("intelligence_engine", "Analytics and insight generation"),
    }

    for keyword, (sys_id, reason) in keyword_to_system.items():
        if keyword in all_text and sys_id not in rec_systems:
            rec_systems[sys_id] = reason

    # Auto-include intelligence engine if 2+ other systems
    if len(rec_systems) >= 2 and "intelligence_engine" not in rec_systems:
        rec_systems["intelligence_engine"] = "Central AI intelligence to coordinate your multiple AI systems"

    # Default: always recommend at least 1 system
    if not rec_systems:
        rec_systems["operations_engine"] = "Operational automation benefits every business"

    return {
        "recommended_outcomes": rec_outcomes,
        "recommended_systems": rec_systems,
    }


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
