"""Capability auto-selection engine for AI Advisory.

Maps business outcomes + AI systems + answers into recommended capabilities.
Uses a hybrid approach: deterministic rules + system mapping + keyword analysis.

This is the intelligence layer that makes the capability selector
"think for the user" — pre-selecting capabilities with confidence scores
and reasoning.
"""

import logging
from execution.advisory.capability_catalog import CAPABILITY_CATALOG, get_capabilities_by_ids

logger = logging.getLogger(__name__)


# ── Business Outcomes ───────────────────────────────────────────────

BUSINESS_OUTCOMES = [
    {"id": "increase_revenue", "label": "Increase Revenue", "icon": "bi-graph-up-arrow",
     "description": "Grow top-line revenue through AI-enhanced sales, marketing, and pricing"},
    {"id": "reduce_costs", "label": "Reduce Operational Costs", "icon": "bi-piggy-bank",
     "description": "Cut costs through automation, efficiency gains, and process optimization"},
    {"id": "improve_cx", "label": "Improve Customer Experience", "icon": "bi-emoji-smile",
     "description": "Deliver faster, smarter, more personalized customer interactions"},
    {"id": "scale_operations", "label": "Scale Operations", "icon": "bi-arrows-angle-expand",
     "description": "Handle more volume without proportional headcount increase"},
    {"id": "improve_decisions", "label": "Improve Decision-Making", "icon": "bi-bullseye",
     "description": "Make faster, data-driven decisions with AI-powered insights"},
]

# ── AI Systems ──────────────────────────────────────────────────────

AI_SYSTEMS = [
    {"id": "revenue_engine", "label": "Revenue Engine", "icon": "bi-currency-dollar",
     "description": "AI-powered sales pipeline, lead scoring, forecasting, and outreach",
     "departments": ["Sales", "Marketing"],
     "color": "primary"},
    {"id": "customer_engine", "label": "Customer Lifecycle Engine", "icon": "bi-people",
     "description": "End-to-end customer experience from acquisition to retention",
     "departments": ["Customer Support", "Sales"],
     "color": "info"},
    {"id": "operations_engine", "label": "Operations Engine", "icon": "bi-gear-wide-connected",
     "description": "Workflow automation, resource optimization, and process intelligence",
     "departments": ["Operations"],
     "color": "warning"},
    {"id": "finance_engine", "label": "Finance Intelligence Engine", "icon": "bi-cash-stack",
     "description": "Automated accounting, forecasting, compliance, and financial analytics",
     "departments": ["Finance"],
     "color": "danger"},
    {"id": "communication_engine", "label": "Communication Engine", "icon": "bi-chat-square-text",
     "description": "AI-powered email, meetings, notifications, and knowledge management",
     "departments": ["Communication"],
     "color": "success"},
    {"id": "intelligence_engine", "label": "Intelligence Engine (AI COO)", "icon": "bi-cpu",
     "description": "Central AI brain that monitors all systems, detects patterns, and triggers actions",
     "departments": ["Technology"],
     "color": "dark"},
]

# ── Outcome → Capability Mapping ────────────────────────────────────

_OUTCOME_CAPABILITIES = {
    "increase_revenue": [
        "auto_lead_scoring", "sales_pipeline_forecast", "outreach_automation",
        "deal_intelligence", "proposal_generator", "content_generation",
        "campaign_optimization", "audience_segmentation",
    ],
    "reduce_costs": [
        "workflow_automation", "invoice_processing", "expense_categorization",
        "resource_scheduling", "auto_reporting", "data_pipeline_automation",
    ],
    "improve_cx": [
        "ai_chat_support", "ticket_auto_triage", "sentiment_monitoring",
        "knowledge_base_qa", "churn_prediction", "email_drafting",
    ],
    "scale_operations": [
        "workflow_automation", "resource_scheduling", "inventory_optimization",
        "project_management", "system_integration", "data_pipeline_automation",
        "quality_monitoring",
    ],
    "improve_decisions": [
        "auto_reporting", "financial_forecasting", "sentiment_monitoring",
        "performance_analytics", "data_pipeline_automation", "system_health_monitoring",
    ],
}

# ── System → Capability Mapping ─────────────────────────────────────

_SYSTEM_CAPABILITIES = {
    "revenue_engine": [
        "auto_lead_scoring", "sales_pipeline_forecast", "outreach_automation",
        "deal_intelligence", "proposal_generator", "content_generation",
        "campaign_optimization", "audience_segmentation", "seo_optimization",
    ],
    "customer_engine": [
        "ai_chat_support", "ticket_auto_triage", "sentiment_monitoring",
        "knowledge_base_qa", "churn_prediction",
    ],
    "operations_engine": [
        "workflow_automation", "resource_scheduling", "inventory_optimization",
        "quality_monitoring", "route_optimization", "project_management",
    ],
    "finance_engine": [
        "invoice_processing", "expense_categorization", "financial_forecasting",
        "fraud_detection",
    ],
    "communication_engine": [
        "email_drafting", "meeting_summarization", "notification_routing",
        "document_qa", "internal_chatbot",
    ],
    "intelligence_engine": [
        "auto_reporting", "data_pipeline_automation", "system_integration",
        "security_monitoring", "compliance_automation", "system_health_monitoring",
    ],
}

# ── Keyword → Capability Mapping (from answers/idea) ────────────────

_KEYWORD_CAPABILITIES = {
    "hiring": ["resume_screening", "onboarding_automation"],
    "recruit": ["resume_screening", "onboarding_automation"],
    "onboard": ["onboarding_automation", "training_delivery"],
    "lead": ["auto_lead_scoring", "outreach_automation"],
    "sales": ["auto_lead_scoring", "sales_pipeline_forecast", "deal_intelligence"],
    "marketing": ["content_generation", "campaign_optimization"],
    "social media": ["social_media_management", "content_generation"],
    "invoice": ["invoice_processing", "expense_categorization"],
    "accounting": ["invoice_processing", "financial_forecasting"],
    "support": ["ai_chat_support", "ticket_auto_triage"],
    "ticket": ["ticket_auto_triage", "knowledge_base_qa"],
    "chat": ["ai_chat_support", "internal_chatbot"],
    "schedule": ["resource_scheduling", "meeting_summarization"],
    "route": ["route_optimization", "workflow_automation"],
    "inventory": ["inventory_optimization", "quality_monitoring"],
    "logistics": ["route_optimization", "inventory_optimization"],
    "shipping": ["route_optimization", "inventory_optimization"],
    "warehouse": ["inventory_optimization", "quality_monitoring"],
    "report": ["auto_reporting", "financial_forecasting"],
    "dashboard": ["auto_reporting", "data_pipeline_automation"],
    "security": ["security_monitoring", "compliance_automation"],
    "compliance": ["compliance_automation", "security_monitoring"],
    "email": ["email_drafting", "outreach_automation"],
    "meeting": ["meeting_summarization", "notification_routing"],
    "training": ["training_delivery", "onboarding_automation"],
    "forecast": ["sales_pipeline_forecast", "financial_forecasting"],
    "churn": ["churn_prediction", "sentiment_monitoring"],
    "feedback": ["sentiment_monitoring", "churn_prediction"],
    "data": ["data_pipeline_automation", "auto_reporting"],
    "integration": ["system_integration", "data_pipeline_automation"],
    "automat": ["workflow_automation", "resource_scheduling"],
}


# ── Problem Vector Profiles ─────────────────────────────────────────
# Each outcome maps to a weighted problem vector (sums to 1.0)

_OUTCOME_VECTORS = {
    "increase_revenue":  {"revenue": 0.7, "cost": 0.1, "cx": 0.1, "ops": 0.1},
    "reduce_costs":      {"revenue": 0.1, "cost": 0.6, "cx": 0.1, "ops": 0.2},
    "improve_cx":        {"revenue": 0.1, "cost": 0.1, "cx": 0.7, "ops": 0.1},
    "scale_operations":  {"revenue": 0.1, "cost": 0.2, "cx": 0.1, "ops": 0.6},
    "improve_decisions": {"revenue": 0.2, "cost": 0.2, "cx": 0.2, "ops": 0.4},
}

# Department limits per primary goal (caps per department)
_DEPT_LIMITS = {
    "increase_revenue":  {"Sales": 4, "Marketing": 3, "Communication": 1, "Technology": 1, "Customer Support": 0, "Operations": 0, "Finance": 0, "Human Resources": 0},
    "reduce_costs":      {"Operations": 5, "Technology": 2, "Finance": 1, "Communication": 1, "Sales": 0, "Marketing": 0, "Customer Support": 0, "Human Resources": 0},
    "improve_cx":        {"Customer Support": 4, "Communication": 2, "Technology": 1, "Sales": 1, "Marketing": 0, "Operations": 0, "Finance": 0, "Human Resources": 0},
    "scale_operations":  {"Operations": 5, "Technology": 2, "Finance": 1, "Communication": 1, "Sales": 0, "Marketing": 0, "Customer Support": 0, "Human Resources": 0},
    "improve_decisions": {"Technology": 4, "Operations": 2, "Finance": 1, "Sales": 1, "Marketing": 0, "Communication": 0, "Customer Support": 0, "Human Resources": 0},
}

MIN_SCORE_THRESHOLD = 0.40
MAX_CAPABILITIES = 12
MIN_CAPABILITIES = 5

# ── Entity Signal Mapping ───────────────────────────────────────────
# Maps keywords found in answers to specific capability IDs for strong boosting

_ENTITY_SIGNALS = {
    # Logistics/supply chain
    "routing": ["route_optimization", "workflow_automation"],
    "delivery": ["route_optimization", "quality_monitoring"],
    "dispatch": ["route_optimization", "resource_scheduling"],
    "fleet": ["route_optimization", "resource_scheduling"],
    "shipping": ["route_optimization", "inventory_optimization"],
    "warehouse": ["inventory_optimization", "quality_monitoring"],
    "inventory": ["inventory_optimization"],
    "supply chain": ["inventory_optimization", "route_optimization"],
    "last-mile": ["route_optimization"],
    # Scheduling/resource
    "scheduling": ["resource_scheduling"],
    "staffing": ["resource_scheduling", "resume_screening"],
    "capacity": ["resource_scheduling", "workflow_automation"],
    # Sales-specific
    "pipeline": ["sales_pipeline_forecast", "auto_lead_scoring"],
    "conversion": ["auto_lead_scoring", "outreach_automation"],
    "leads": ["auto_lead_scoring", "outreach_automation"],
    "outreach": ["outreach_automation", "email_drafting"],
    "follow-up": ["outreach_automation"],
    # Support-specific
    "tickets": ["ticket_auto_triage", "ai_chat_support"],
    "response time": ["ai_chat_support", "ticket_auto_triage"],
    "customer complaints": ["sentiment_monitoring", "ai_chat_support"],
    # Finance-specific
    "invoicing": ["invoice_processing"],
    "billing": ["invoice_processing", "expense_categorization"],
    "forecasting": ["financial_forecasting", "sales_pipeline_forecast"],
    # General
    "reporting": ["auto_reporting"],
    "data entry": ["workflow_automation"],
    "manual process": ["workflow_automation"],
}

# Domain mapping for each department
_DEPT_TO_DOMAIN = {
    "Sales": "sales",
    "Marketing": "sales",
    "Customer Support": "customer",
    "Operations": "operations",
    "Finance": "finance",
    "Human Resources": "hr",
    "Technology": "technology",
    "Communication": "communication",
}

# Which domains are relevant for each primary goal
_GOAL_DOMAINS = {
    "increase_revenue":  {"primary": ["sales"], "secondary": ["communication", "technology"]},
    "reduce_costs":      {"primary": ["operations", "finance"], "secondary": ["technology"]},
    "improve_cx":        {"primary": ["customer", "communication"], "secondary": ["technology"]},
    "scale_operations":  {"primary": ["operations", "technology"], "secondary": ["communication"]},
    "improve_decisions": {"primary": ["technology", "operations"], "secondary": ["finance"]},
}


def _extract_entity_signals(text: str) -> dict:
    """Extract entity signals from text. Returns {cap_id: boost_count}."""
    boosts = {}
    for entity, cap_ids in _ENTITY_SIGNALS.items():
        if entity in text:
            for cap_id in cap_ids:
                boosts[cap_id] = boosts.get(cap_id, 0) + 1
    return boosts


def _build_problem_vector(outcomes: list[str]) -> dict:
    """Build a unified problem vector from selected outcomes.

    First outcome gets 60% weight, remaining split 40%.
    """
    if not outcomes:
        return {"revenue": 0.25, "cost": 0.25, "cx": 0.25, "ops": 0.25}

    primary_vec = _OUTCOME_VECTORS.get(outcomes[0], {"revenue": 0.25, "cost": 0.25, "cx": 0.25, "ops": 0.25})

    if len(outcomes) == 1:
        return primary_vec

    # Weighted blend: primary=0.6, secondary=0.4/n
    secondary_weight = 0.4 / (len(outcomes) - 1)
    result = {dim: primary_vec[dim] * 0.6 for dim in primary_vec}

    for outcome_id in outcomes[1:]:
        vec = _OUTCOME_VECTORS.get(outcome_id, {"revenue": 0.25, "cost": 0.25, "cx": 0.25, "ops": 0.25})
        for dim in result:
            result[dim] += vec[dim] * secondary_weight

    return result


def _score_capability(cap: dict, problem_vector: dict) -> float:
    """Vector dot product: capability impact vs problem vector."""
    iv = cap.get("impact_vector", {"revenue": 0.2, "cost": 0.2, "cx": 0.2, "ops": 0.2})
    return sum(iv.get(dim, 0) * problem_vector.get(dim, 0) for dim in ["revenue", "cost", "cx", "ops"])


def map_capabilities(session: dict) -> dict:
    """Problem-first capability selection engine.

    Uses vector dot-product scoring against the user's problem vector,
    then applies hard filters: threshold, department limits, redundancy
    elimination, and max cap.

    Returns dict with recommended/excluded lists, scores, and reasoning.
    """
    outcomes = session.get("selected_outcomes", [])
    idea = session.get("business_idea", "").lower()
    answers_text = " ".join(
        a.get("answer_text", "") for a in session.get("answers", [])
    ).lower()
    all_text = f"{idea} {answers_text}"

    # 1. Build problem vector from outcomes
    problem_vector = _build_problem_vector(outcomes)
    primary_goal = outcomes[0] if outcomes else "reduce_costs"
    primary_label = _get_outcome_label(primary_goal)

    # 2. Extract entity signals from user text
    entity_boosts = _extract_entity_signals(all_text)

    # 3. Determine relevant domains for this goal
    goal_domains = _GOAL_DOMAINS.get(primary_goal, {"primary": ["operations"], "secondary": ["technology"]})
    primary_domains = set(goal_domains["primary"])
    secondary_domains = set(goal_domains["secondary"])

    # 4. Load adaptive weights (learned from outcomes)
    try:
        from execution.advisory.outcome_tracker import get_capability_weight_adjustment
        has_learning = True
    except Exception:
        has_learning = False

    # 5. Score every capability: vector + entities + domain penalty + learned adjustment
    scored = []
    for cap in CAPABILITY_CATALOG:
        score = _score_capability(cap, problem_vector)

        # Apply learned weight adjustment (multiplier ~0.85-1.15)
        if has_learning:
            score *= get_capability_weight_adjustment(cap["id"])

        # Entity signal boost (+0.15 per entity match, up to +0.45)
        entity_match_count = entity_boosts.get(cap["id"], 0)
        if entity_match_count > 0:
            score += min(entity_match_count * 0.15, 0.45)

        # Small keyword bonus (+0.05) for keyword catalog match
        for keyword, cap_ids in _KEYWORD_CAPABILITIES.items():
            if keyword in all_text and cap["id"] in cap_ids:
                score += 0.05
                break

        # Domain relevance penalty (CRITICAL)
        cap_domain = _DEPT_TO_DOMAIN.get(cap["department"], "other")
        if cap_domain in primary_domains:
            score += 0.1  # Small boost for being in primary domain
        elif cap_domain in secondary_domains:
            pass  # Neutral
        else:
            # Penalty for irrelevant domain (unless entity-matched)
            if entity_match_count == 0:
                score -= 0.25

        scored.append({"cap": cap, "score": round(score, 3), "entity_matches": entity_match_count})

    scored.sort(key=lambda x: x["score"], reverse=True)

    # 3. HARD FILTER 1: Min threshold
    above_threshold = [s for s in scored if s["score"] >= MIN_SCORE_THRESHOLD]
    below_threshold = [s for s in scored if s["score"] < MIN_SCORE_THRESHOLD]

    # 4. HARD FILTER 2: Department limits
    dept_limits = _DEPT_LIMITS.get(primary_goal, {d: 3 for d in ["Sales", "Marketing", "Customer Support", "Operations", "Finance", "Human Resources", "Technology", "Communication"]})
    dept_counts = {}
    filtered = []
    dept_excluded = []

    for s in above_threshold:
        dept = s["cap"]["department"]
        dept_counts[dept] = dept_counts.get(dept, 0)
        limit = dept_limits.get(dept, 2)
        if dept_counts[dept] < limit:
            filtered.append(s)
            dept_counts[dept] += 1
        else:
            dept_excluded.append(s)

    # 5. HARD FILTER 3: Redundancy elimination (keep top 1 per group)
    seen_groups = {}
    deduped = []
    redundancy_excluded = []

    for s in filtered:
        group = s["cap"].get("redundancy_group", s["cap"]["id"])
        if group not in seen_groups:
            seen_groups[group] = True
            deduped.append(s)
        else:
            redundancy_excluded.append(s)

    # 6. HARD FILTER 4: Max cap (take top N by score)
    recommended_scored = deduped[:MAX_CAPABILITIES]

    # Ensure minimum
    if len(recommended_scored) < MIN_CAPABILITIES and dept_excluded:
        for s in dept_excluded:
            if len(recommended_scored) >= MIN_CAPABILITIES:
                break
            recommended_scored.append(s)

    # 7. Build output
    recommended = [s["cap"]["id"] for s in recommended_scored]
    excluded_ids = set()
    for s in below_threshold + dept_excluded + redundancy_excluded:
        if s["cap"]["id"] not in recommended:
            excluded_ids.add(s["cap"]["id"])

    # Scores and reasoning
    scores = {s["cap"]["id"]: s["score"] for s in scored}
    reasoning = {}
    for s in recommended_scored:
        cap = s["cap"]
        iv = cap.get("impact_vector", {})
        # Find the dominant dimension
        top_dim = max(iv, key=iv.get) if iv else "ops"
        dim_labels = {"revenue": "revenue growth", "cost": "cost reduction", "cx": "customer experience", "ops": "operational efficiency"}
        reason = f"High impact on {dim_labels.get(top_dim, top_dim)} (score: {s['score']:.2f})"
        if s.get("entity_matches", 0) > 0:
            # Find which entities matched
            matched_entities = [e for e, caps in _ENTITY_SIGNALS.items() if cap["id"] in caps and e in all_text]
            if matched_entities:
                reason += f" | Matched: {', '.join(matched_entities[:3])}"
        reasoning[cap["id"]] = [reason]

    # Confidence normalized to 0-100
    max_score = max(scores.values()) if scores else 1
    confidence = {
        cap_id: min(round((score / max_score) * 100), 100)
        for cap_id, score in scores.items()
    }

    # Exclusion reasoning
    exclusion_reasons = {}
    for s in below_threshold:
        exclusion_reasons[s["cap"]["id"]] = "Below impact threshold for your primary goal"
    for s in dept_excluded:
        exclusion_reasons[s["cap"]["id"]] = f"{s['cap']['department']} is not a primary focus area"
    for s in redundancy_excluded:
        exclusion_reasons[s["cap"]["id"]] = f"Similar capability already selected ({s['cap'].get('redundancy_group', '')})"

    return {
        "recommended": recommended,
        "optional": list(excluded_ids)[:10],
        "confidence_scores": confidence,
        "reasoning": reasoning,
        "exclusion_reasons": exclusion_reasons,
        "primary_goal": primary_goal,
        "primary_label": primary_label,
        "problem_vector": problem_vector,
        "total_scored": len(scored),
        "total_excluded": len(excluded_ids),
    }


def should_include_cory(session: dict) -> bool:
    """Determine if the Intelligence Engine (Cory) should be auto-included."""
    systems = session.get("selected_ai_systems", [])
    outcomes = session.get("selected_outcomes", [])

    if "intelligence_engine" in systems:
        return True
    if "improve_decisions" in outcomes:
        return True
    if len(systems) >= 2:
        return True
    return False


def _get_outcome_label(outcome_id: str) -> str:
    for o in BUSINESS_OUTCOMES:
        if o["id"] == outcome_id:
            return o["label"]
    return outcome_id.replace("_", " ").title()


def _get_system_label(system_id: str) -> str:
    for s in AI_SYSTEMS:
        if s["id"] == system_id:
            return s["label"]
    return system_id.replace("_", " ").title()
