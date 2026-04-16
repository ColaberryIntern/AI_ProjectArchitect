"""Smart recommendation engine for AI Advisory.

Generates prioritized recommendations for systems, capabilities,
and implementation strategy based on the full session context.
"""

from execution.advisory.capability_mapper import AI_SYSTEMS, BUSINESS_OUTCOMES


PAIN_TO_OUTCOME_KEYWORDS = {
    "increase_revenue": ("revenue", "sales", "lead", "pipeline", "conversion", "pricing", "upsell", "cross-sell", "growth"),
    "reduce_costs": ("cost", "waste", "manual", "inefficien", "overhead", "labor", "rework", "idle", "overpay"),
    "improve_cx": ("customer", "satisfaction", "service", "support", "retention", "churn", "response time", "complaint"),
    "scale_operations": ("scale", "volume", "throughput", "capacity", "backlog", "bottleneck", "turnover"),
    "improve_decisions": ("data", "visibility", "forecast", "reporting", "analytics", "decision", "compliance"),
}

# Map generic system IDs to the department key in a taxonomy profile.
SYSTEM_TO_DEPT = {
    "operations_engine": "operations",
    "revenue_engine": "sales",
    "customer_engine": "customer_support",
    "communication_engine": "customer_support",
    "finance_engine": "finance",
    "intelligence_engine": "technology",
}


def recommend_design(session: dict) -> dict:
    """Recommend outcomes + systems, driven by industry taxonomy when available.

    Looks up a taxonomy for the user's business (seeded, cached, or LLM-generated
    sync on first encounter), then scores outcomes using both generic keywords
    and taxonomy-specific pain_catalog, and labels systems with
    industry-specific names from the taxonomy.

    Falls back to generic keyword-only scoring if taxonomy lookup fails.

    Returns:
        Dict with recommended_outcomes, recommended_systems, and (when a
        taxonomy was resolved) industry_label + industry_source metadata.
    """
    answers = session.get("answers", [])
    idea = session.get("business_idea", "")
    all_text = (idea + " " + " ".join(a.get("answer_text", "") for a in answers)).lower()

    taxonomy = _resolve_taxonomy(session, idea, all_text)

    outcome_scores = _score_outcomes(all_text, taxonomy)

    # Pick PRIMARY (highest score) + at most 1 secondary.
    rec_outcomes = {}
    if outcome_scores:
        sorted_outcomes = sorted(outcome_scores.items(), key=lambda x: x[1], reverse=True)
        primary_id = sorted_outcomes[0][0]
        rec_outcomes[primary_id] = _outcome_rationale(primary_id, taxonomy, all_text, primary=True)

        if len(sorted_outcomes) > 1 and sorted_outcomes[1][1] >= 3:
            sec_id = sorted_outcomes[1][0]
            rec_outcomes[sec_id] = _outcome_rationale(sec_id, taxonomy, all_text, primary=False)
    else:
        rec_outcomes["reduce_costs"] = _outcome_rationale("reduce_costs", taxonomy, all_text, primary=True)

    primary_goal = list(rec_outcomes.keys())[0]

    goal_systems = {
        "increase_revenue": ["revenue_engine", "communication_engine"],
        "reduce_costs": ["operations_engine", "finance_engine"],
        "improve_cx": ["customer_engine", "communication_engine"],
        "scale_operations": ["operations_engine", "intelligence_engine"],
        "improve_decisions": ["intelligence_engine", "operations_engine"],
    }

    rec_systems = {}
    for sys_id in goal_systems.get(primary_goal, ["operations_engine"]):
        rec_systems[sys_id] = _system_rationale(sys_id, primary_goal, taxonomy)

    if len(rec_systems) >= 2 and "intelligence_engine" not in rec_systems:
        rec_systems["intelligence_engine"] = _system_rationale(
            "intelligence_engine", primary_goal, taxonomy, coordinator=True
        )

    result = {
        "recommended_outcomes": rec_outcomes,
        "recommended_systems": rec_systems,
    }
    if taxonomy:
        result["industry_label"] = taxonomy.get("label")
        result["industry_source"] = taxonomy.get("_meta", {}).get("source")
        result["industry_key"] = taxonomy.get("_meta", {}).get("industry_key")
    return result


def _resolve_taxonomy(session: dict, idea: str, all_text: str) -> dict | None:
    """Look up taxonomy for this session's industry. Returns None on any failure.

    Industry detection uses idea + the first answer (the explicit "what does
    your business do and what industry?" question). Using the full Q&A corpus
    here causes false positives — e.g., a board-game publisher saying
    "...manufacturing..." as a step in their value chain gets routed to the
    Manufacturing profile. The full corpus IS still passed as session_context
    so the LLM has it on a generation miss, and the rationale layer uses it
    for pain-citation overlap.
    """
    try:
        from execution.advisory.taxonomy_registry import lookup_taxonomy
    except Exception:
        return None

    answers = session.get("answers", [])
    industry_text = idea
    if answers:
        industry_text = f"{idea}\n\n{answers[0].get('answer_text', '')}"

    try:
        return lookup_taxonomy(industry_text.strip(), all_text)
    except Exception:
        return None


def _score_outcomes(all_text: str, taxonomy: dict | None) -> dict:
    """Score outcomes using generic keywords plus taxonomy pain_catalog.

    A pain is mapped to an outcome when any of that outcome's keyword roots
    appear in the pain label or root_cause. Pain matches are weighted 2x
    because they are industry-specific signal.
    """
    generic_keywords = {
        "increase_revenue": ["revenue", "sales", "lead", "conversion", "pipeline", "close", "deal", "prospect", "outreach", "quota"],
        "reduce_costs": ["cost", "manual", "automat", "efficien", "reduce", "save", "expensive", "waste", "overhead"],
        "improve_cx": ["customer", "support", "response", "satisfaction", "service", "chat", "ticket", "complaint", "nps"],
        "scale_operations": ["scale", "grow", "expand", "volume", "hire", "headcount", "capacity", "throughput"],
        "improve_decisions": ["data", "decision", "report", "dashboard", "analytics", "insight", "visibility", "metric"],
    }

    scores: dict[str, int] = {}
    for outcome_id, kws in generic_keywords.items():
        score = sum(1 for kw in kws if kw in all_text)
        if score:
            scores[outcome_id] = score

    if not taxonomy:
        return scores

    for pain in taxonomy.get("pain_catalog", []):
        pain_text = f"{pain.get('label', '')} {pain.get('root_cause', '')}".lower()
        if not any(kw in all_text for kw in pain_text.split() if len(kw) > 4):
            continue
        for outcome_id, roots in PAIN_TO_OUTCOME_KEYWORDS.items():
            if any(root in pain_text for root in roots):
                scores[outcome_id] = scores.get(outcome_id, 0) + 2
    return scores


def _outcome_rationale(outcome_id: str, taxonomy: dict | None, user_text: str, primary: bool) -> str:
    """Build a rationale string. Cites the taxonomy pain that best matches what the user actually said.

    Pains are first filtered to those relevant to the outcome (root-keyword match),
    then ranked by how many distinct words from the pain's label/root_cause appear
    in the user's combined Q&A text. The pain with the strongest user-text overlap
    wins — so the rationale references something the user literally described.
    """
    label = _get_outcome_label_for_rec(outcome_id)
    prefix = "Primary goal" if primary else "Secondary"

    if not taxonomy:
        return f"{prefix}: {label}"

    roots = PAIN_TO_OUTCOME_KEYWORDS.get(outcome_id, ())
    candidates = []
    fallback = None
    for pain in taxonomy.get("pain_catalog", []):
        pain_text = f"{pain.get('label', '')} {pain.get('root_cause', '')}".lower()
        if not any(root in pain_text for root in roots):
            continue
        if fallback is None:
            fallback = pain
        overlap = sum(
            1 for word in set(pain_text.split())
            if len(word) > 3 and word in user_text
        )
        if overlap > 0:
            candidates.append((overlap, pain))

    industry_label = taxonomy.get("label", "your industry")
    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        best_pain = candidates[0][1]
        return f"{prefix}: {label} — addresses {best_pain['label']} in {industry_label}"
    if fallback is not None:
        return f"{prefix}: {label} — addresses {fallback['label']} in {industry_label}"
    return f"{prefix}: {label} for {industry_label}"


def _system_rationale(sys_id: str, primary_goal: str, taxonomy: dict | None, coordinator: bool = False) -> str:
    """Label a recommended system using the taxonomy system_names where possible."""
    goal_label = _get_outcome_label_for_rec(primary_goal)

    if taxonomy:
        dept = SYSTEM_TO_DEPT.get(sys_id)
        industry_name = taxonomy.get("system_names", {}).get(dept) if dept else None
        if industry_name:
            if coordinator:
                return f"{industry_name} — coordinates your AI systems"
            return f"{industry_name} — core system for {goal_label}"

    if coordinator:
        return "Coordinates your AI systems"
    return f"Core system for {goal_label}"


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
