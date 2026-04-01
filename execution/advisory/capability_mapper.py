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


def map_capabilities(session: dict) -> dict:
    """Compute capability recommendations from session context.

    Combines:
    1. Outcome-based mapping (selected business outcomes)
    2. System-based mapping (selected AI systems)
    3. Keyword-based mapping (from answers + idea text)

    Returns dict with recommended/optional lists, confidence scores, and reasoning.
    """
    outcomes = session.get("selected_outcomes", [])
    systems = session.get("selected_ai_systems", [])
    idea = session.get("business_idea", "").lower()
    answers_text = " ".join(
        a.get("answer_text", "") for a in session.get("answers", [])
    ).lower()

    all_text = f"{idea} {answers_text}"

    # Collect capability IDs with scores
    scores = {}  # cap_id -> score
    reasons = {}  # cap_id -> list of reasons

    # 1. Outcome-based (strong signal — +30 per match)
    for outcome_id in outcomes:
        cap_ids = _OUTCOME_CAPABILITIES.get(outcome_id, [])
        outcome_label = _get_outcome_label(outcome_id)
        for cap_id in cap_ids:
            scores[cap_id] = scores.get(cap_id, 0) + 30
            reasons.setdefault(cap_id, []).append(f"Supports your goal: {outcome_label}")

    # 2. System-based (strong signal — +25 per match)
    for system_id in systems:
        cap_ids = _SYSTEM_CAPABILITIES.get(system_id, [])
        system_label = _get_system_label(system_id)
        for cap_id in cap_ids:
            scores[cap_id] = scores.get(cap_id, 0) + 25
            reasons.setdefault(cap_id, []).append(f"Part of {system_label}")

    # 3. Keyword-based (moderate signal — +15 per match)
    for keyword, cap_ids in _KEYWORD_CAPABILITIES.items():
        if keyword in all_text:
            for cap_id in cap_ids:
                scores[cap_id] = scores.get(cap_id, 0) + 15
                if not any("mentioned in your" in r for r in reasons.get(cap_id, [])):
                    reasons.setdefault(cap_id, []).append(
                        "Matches what you described about your business"
                    )

    # Separate into recommended (score >= 25) and optional
    all_cap_ids = {c["id"] for c in CAPABILITY_CATALOG}
    recommended = []
    optional = []

    for cap_id in sorted(scores, key=lambda x: scores[x], reverse=True):
        if cap_id not in all_cap_ids:
            continue
        if scores[cap_id] >= 25:
            recommended.append(cap_id)
        else:
            optional.append(cap_id)

    # Cap recommended at 20 to avoid overwhelming
    if len(recommended) > 20:
        optional = recommended[20:] + optional
        recommended = recommended[:20]

    # Confidence scores normalized to 0-100
    max_score = max(scores.values()) if scores else 1
    confidence = {
        cap_id: min(round((score / max_score) * 100), 100)
        for cap_id, score in scores.items()
    }

    return {
        "recommended": recommended,
        "optional": optional,
        "confidence_scores": confidence,
        "reasoning": reasons,
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
