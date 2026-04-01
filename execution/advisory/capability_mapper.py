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
    "Customer Support": "support",
    "Operations": "operations",
    "Finance": "finance",
    "Human Resources": "hr",
    "Technology": "technology",
    "Communication": "support",  # Communication supports CX domain
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


# ── Domain Anchoring (Problem-First) ────────────────────────────────

_DOMAIN_KEYWORDS = {
    "support": ["support", "ticket", "customer issue", "response time", "help desk", "chat",
                 "intercom", "zendesk", "churn", "customer frustration", "complaint", "nps",
                 "satisfaction", "wait time", "sla", "escalation"],
    "sales": ["lead", "pipeline", "deal", "conversion", "prospect", "outreach", "close",
              "quota", "sales", "crm", "follow-up", "demo"],
    "operations": ["logistics", "inventory", "routing", "dispatch", "supply chain", "warehouse",
                    "fleet", "delivery", "scheduling", "workflow", "manufacturing", "capacity"],
    "finance": ["invoice", "billing", "accounts payable", "expenses", "accounting", "forecast",
                "reconciliation", "payroll", "cash flow"],
    "hr": ["hiring", "onboarding", "employee", "training", "recruit", "retention", "talent",
            "performance review"],
}

# Hard domain enforcement: which departments are ALLOWED per primary domain
_DOMAIN_ALLOWED_DEPTS = {
    "support": {"Customer Support": 4, "Communication": 2, "Technology": 1},
    "sales": {"Sales": 4, "Marketing": 3, "Communication": 1, "Technology": 1},
    "operations": {"Operations": 5, "Technology": 2, "Communication": 1},
    "finance": {"Finance": 3, "Technology": 2, "Operations": 1},
    "hr": {"Human Resources": 4, "Communication": 1, "Technology": 1},
}

# Domain-specific system labels
_DOMAIN_SYSTEM_LABELS = {
    "support": "AI Customer Support & Retention System",
    "sales": "AI Revenue Growth System",
    "operations": "AI Operational Efficiency System",
    "finance": "AI Financial Intelligence System",
    "hr": "AI People & Talent System",
}


def _anchor_domain(text: str) -> tuple[str, float, dict]:
    """Deterministic domain anchoring from problem text.

    Returns (primary_domain, confidence, all_scores).
    """
    scores = {}
    word_count = max(len(text.split()), 1)

    for domain, keywords in _DOMAIN_KEYWORDS.items():
        count = sum(1 for kw in keywords if kw in text)
        scores[domain] = round(count / max(len(keywords) * 0.3, 1), 2)  # Normalize

    primary = max(scores, key=scores.get)
    confidence = scores[primary]

    return primary, confidence, scores


def map_capabilities(session: dict) -> dict:
    """Deterministic, CEO-safe capability selection engine.

    PRINCIPLE: Problem context ALWAYS overrides goals.
    STEP 1: Anchor primary domain from problem text
    STEP 2: Hard enforce allowed departments
    STEP 3: Score = domain match (0.5) + entity signals (0.3) + goal alignment (0.2)
    STEP 4: Finance safety lock
    STEP 5: QC checks

    Returns dict with recommended/excluded lists, scores, justifications, and QC status.
    """
    outcomes = session.get("selected_outcomes", [])
    idea = session.get("business_idea", "").lower()
    answers_text = " ".join(
        a.get("answer_text", "") for a in session.get("answers", [])
    ).lower()
    all_text = f"{idea} {answers_text}"
    primary_goal = outcomes[0] if outcomes else "reduce_costs"
    primary_label = _get_outcome_label(primary_goal)

    # ═══ STEP 1: Domain Anchoring (from problem text, NOT goals) ═══
    primary_domain, domain_confidence, domain_scores = _anchor_domain(all_text)
    system_label = _DOMAIN_SYSTEM_LABELS.get(primary_domain, "AI System")

    # ═══ STEP 2: Hard Domain Enforcement ═══════════════════════════
    allowed_depts = _DOMAIN_ALLOWED_DEPTS.get(primary_domain, {"Operations": 3, "Technology": 2})

    # ═══ STEP 3: Extract entity signals ════════════════════════════
    entity_boosts = _extract_entity_signals(all_text)

    # ═══ STEP 4: Build problem vector (blended) ═══════════════════
    problem_vector = _build_problem_vector(outcomes)

    # ═══ STEP 5: Finance safety lock ══════════════════════════════
    has_finance_keywords = any(kw in all_text for kw in _DOMAIN_KEYWORDS.get("finance", []))

    # ═══ STEP 6: Score capabilities ════════════════════════════════
    # Load adaptive weights
    try:
        from execution.advisory.outcome_tracker import get_capability_weight_adjustment
        has_learning = True
    except Exception:
        has_learning = False

    scored = []
    hard_blocked = []

    for cap in CAPABILITY_CATALOG:
        dept = cap["department"]

        # HARD BLOCK: department not in allowed list
        if dept not in allowed_depts:
            hard_blocked.append({"cap": cap, "reason": f"{dept} is not relevant to your {primary_domain} focus"})
            continue

        # HARD BLOCK: finance capabilities without finance keywords
        if dept == "Finance" and not has_finance_keywords:
            hard_blocked.append({"cap": cap, "reason": "No finance-related signals detected in your description"})
            continue

        # SCORE: domain match (0.5) + entity signals (0.3) + goal alignment (0.2)
        cap_domain = _DEPT_TO_DOMAIN.get(dept, "other")
        domain_match = 1.0 if cap_domain == primary_domain else 0.3
        entity_match = min(entity_boosts.get(cap["id"], 0) * 0.5, 1.0)
        goal_score = _score_capability(cap, problem_vector)

        score = (domain_match * 0.5) + (entity_match * 0.3) + (goal_score * 0.2)

        # Apply learned weight adjustment
        if has_learning:
            score *= get_capability_weight_adjustment(cap["id"])

        # Small keyword bonus
        for keyword, cap_ids in _KEYWORD_CAPABILITIES.items():
            if keyword in all_text and cap["id"] in cap_ids:
                score += 0.03
                break

        scored.append({"cap": cap, "score": round(score, 3), "entity_matches": entity_boosts.get(cap["id"], 0)})

    scored.sort(key=lambda x: x["score"], reverse=True)

    # ═══ STEP 7: Threshold Filter ═════════════════════════════════
    above_threshold = [s for s in scored if s["score"] >= MIN_SCORE_THRESHOLD]
    below_threshold = [s for s in scored if s["score"] < MIN_SCORE_THRESHOLD]

    # ═══ STEP 8: Department Limits (from domain enforcement) ══════
    dept_counts = {}
    filtered = []
    dept_excluded = []

    for s in above_threshold:
        dept = s["cap"]["department"]
        dept_counts[dept] = dept_counts.get(dept, 0)
        limit = allowed_depts.get(dept, 0)
        if dept_counts[dept] < limit:
            filtered.append(s)
            dept_counts[dept] += 1
        else:
            dept_excluded.append(s)

    # ═══ STEP 9: Redundancy Elimination ═══════════════════════════
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

    # ═══ STEP 10: Max Cap ══════════════════════════════════════════
    recommended_scored = deduped[:MAX_CAPABILITIES]

    # ═══ STEP 11: QC Checks ═══════════════════════════════════════
    qc_passed = True
    qc_notes = []

    # QC1: Every capability must have a justification
    for s in recommended_scored:
        if s["score"] < 0.3:
            qc_notes.append(f"Low confidence: {s['cap']['name']} (score {s['score']:.2f})")

    # QC2: No irrelevant departments (should be caught by hard block, but double-check)
    for s in recommended_scored:
        if s["cap"]["department"] not in allowed_depts:
            recommended_scored.remove(s)
            qc_notes.append(f"QC removed: {s['cap']['name']} (dept not in domain)")
            qc_passed = False

    # QC3: Fail-safe — if uncertain, select fewer
    if domain_confidence < 0.3:
        recommended_scored = recommended_scored[:MIN_CAPABILITIES]
        qc_notes.append("Low domain confidence — reduced to minimum capabilities")

    # ═══ BUILD OUTPUT ══════════════════════════════════════════════
    recommended = [s["cap"]["id"] for s in recommended_scored]
    all_excluded = set()
    for s in below_threshold + dept_excluded + redundancy_excluded:
        if s["cap"]["id"] not in recommended:
            all_excluded.add(s["cap"]["id"])
    for s in hard_blocked:
        all_excluded.add(s["cap"]["id"])

    # Scores
    scores = {s["cap"]["id"]: s["score"] for s in scored}

    # Reasoning (with justification for each)
    reasoning = {}
    for s in recommended_scored:
        cap = s["cap"]
        reasons = []
        # Primary reason: domain match
        cap_domain = _DEPT_TO_DOMAIN.get(cap["department"], "other")
        if cap_domain == primary_domain:
            reasons.append(f"Core {primary_domain} capability (score: {s['score']:.2f})")
        else:
            reasons.append(f"Supporting capability (score: {s['score']:.2f})")
        # Entity match detail
        if s.get("entity_matches", 0) > 0:
            matched = [e for e, caps in _ENTITY_SIGNALS.items() if cap["id"] in caps and e in all_text]
            if matched:
                reasons[0] += f" | Matched: {', '.join(matched[:3])}"
        reasoning[cap["id"]] = reasons

    # Exclusion reasoning
    exclusion_reasons = {}
    for s in hard_blocked:
        exclusion_reasons[s["cap"]["id"]] = s["reason"]
    for s in below_threshold:
        exclusion_reasons[s["cap"]["id"]] = "Below score threshold"
    for s in dept_excluded:
        exclusion_reasons[s["cap"]["id"]] = f"Department limit reached for {s['cap']['department']}"
    for s in redundancy_excluded:
        exclusion_reasons[s["cap"]["id"]] = f"Similar capability already selected"

    # Confidence normalized
    max_score = max(scores.values()) if scores else 1
    confidence = {
        cap_id: min(round((score / max_score) * 100), 100)
        for cap_id, score in scores.items()
    }

    return {
        "recommended": recommended,
        "optional": list(all_excluded)[:10],
        "confidence_scores": confidence,
        "reasoning": reasoning,
        "exclusion_reasons": exclusion_reasons,
        "primary_goal": primary_goal,
        "primary_label": primary_label,
        "primary_domain": primary_domain,
        "domain_confidence": domain_confidence,
        "system_label": system_label,
        "problem_vector": problem_vector,
        "total_scored": len(scored),
        "total_excluded": len(all_excluded),
        "total_hard_blocked": len(hard_blocked),
        "qc_passed": qc_passed,
        "qc_notes": qc_notes,
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
