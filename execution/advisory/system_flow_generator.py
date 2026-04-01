"""System flow generator for AI Advisory.

Generates step-by-step flows showing how agents work together,
with conditional logic and cause-effect-outcome chains.
Also generates AI COO insights and implementation timeline.
"""


# ── Flow Templates (capability -> step chain) ───────────────────────

_FLOW_CHAINS = {
    # Sales flows
    "auto_lead_scoring": [
        {"step": "Lead enters system", "type": "event", "icon": "bi-person-plus"},
        {"step": "AI Lead Qualifier scores lead", "type": "agent", "icon": "bi-robot"},
        {"step": "IF score > 80", "type": "condition", "icon": "bi-signpost-split"},
        {"step": "Route to sales team + trigger outreach", "type": "action", "icon": "bi-send"},
        {"step": "Lead prioritized in pipeline", "type": "outcome", "icon": "bi-check-circle"},
    ],
    "outreach_automation": [
        {"step": "Qualified lead flagged", "type": "event", "icon": "bi-flag"},
        {"step": "AI Outreach Manager activates", "type": "agent", "icon": "bi-robot"},
        {"step": "Personalized email sequence sent", "type": "action", "icon": "bi-envelope"},
        {"step": "IF prospect engages", "type": "condition", "icon": "bi-signpost-split"},
        {"step": "Schedule meeting + notify sales rep", "type": "action", "icon": "bi-calendar-event"},
        {"step": "Conversion opportunity created", "type": "outcome", "icon": "bi-trophy"},
    ],
    # Support flows
    "ai_chat_support": [
        {"step": "Customer initiates chat", "type": "event", "icon": "bi-chat-dots"},
        {"step": "AI Support Agent responds", "type": "agent", "icon": "bi-robot"},
        {"step": "IF resolvable automatically", "type": "condition", "icon": "bi-signpost-split"},
        {"step": "Resolve and close ticket", "type": "action", "icon": "bi-check-lg"},
        {"step": "ELSE escalate with full context", "type": "condition", "icon": "bi-arrow-up-right"},
        {"step": "Customer satisfied, zero wait time", "type": "outcome", "icon": "bi-emoji-smile"},
    ],
    "ticket_auto_triage": [
        {"step": "Support ticket created", "type": "event", "icon": "bi-ticket"},
        {"step": "AI Triage Bot categorizes", "type": "agent", "icon": "bi-robot"},
        {"step": "Priority assigned (P1/P2/P3)", "type": "action", "icon": "bi-sort-down"},
        {"step": "Routed to correct team", "type": "action", "icon": "bi-people"},
        {"step": "Resolution time reduced 60%", "type": "outcome", "icon": "bi-speedometer"},
    ],
    # Operations flows
    "workflow_automation": [
        {"step": "Process step completed", "type": "event", "icon": "bi-gear"},
        {"step": "AI Process Optimizer evaluates", "type": "agent", "icon": "bi-robot"},
        {"step": "Next step auto-triggered", "type": "action", "icon": "bi-arrow-right-circle"},
        {"step": "IF approval needed", "type": "condition", "icon": "bi-signpost-split"},
        {"step": "Route to approver with context", "type": "action", "icon": "bi-person-check"},
        {"step": "Process completed in minutes vs days", "type": "outcome", "icon": "bi-lightning"},
    ],
    "resource_scheduling": [
        {"step": "Schedule request received", "type": "event", "icon": "bi-calendar"},
        {"step": "AI Scheduler optimizes allocation", "type": "agent", "icon": "bi-robot"},
        {"step": "Conflicts resolved automatically", "type": "action", "icon": "bi-puzzle"},
        {"step": "Optimal schedule published", "type": "outcome", "icon": "bi-calendar-check"},
    ],
    # Finance flows
    "invoice_processing": [
        {"step": "Invoice received", "type": "event", "icon": "bi-receipt"},
        {"step": "AI Invoice Processor extracts data", "type": "agent", "icon": "bi-robot"},
        {"step": "Matched to purchase order", "type": "action", "icon": "bi-link"},
        {"step": "IF discrepancy found", "type": "condition", "icon": "bi-signpost-split"},
        {"step": "Flag for review with details", "type": "action", "icon": "bi-exclamation-triangle"},
        {"step": "Invoice processed in seconds", "type": "outcome", "icon": "bi-check-circle"},
    ],
    # Communication flows
    "email_drafting": [
        {"step": "Email composition requested", "type": "event", "icon": "bi-envelope"},
        {"step": "AI Email Manager drafts response", "type": "agent", "icon": "bi-robot"},
        {"step": "Context from CRM + history applied", "type": "action", "icon": "bi-database"},
        {"step": "Professional email ready in seconds", "type": "outcome", "icon": "bi-send-check"},
    ],
}


def generate_system_flows(selected_capability_ids: list[str]) -> list[dict]:
    """Generate system flow visualizations from selected capabilities.

    Returns a list of flow dicts, each with a label and steps.
    Picks the most impactful flows (max 4).
    """
    flows = []
    for cap_id in selected_capability_ids:
        chain = _FLOW_CHAINS.get(cap_id)
        if chain:
            # Get a friendly label
            label = cap_id.replace("_", " ").title()
            flows.append({
                "id": cap_id,
                "label": label,
                "steps": chain,
            })

    # Return max 4 most interesting flows
    return flows[:4]


def generate_insights(session: dict) -> list[dict]:
    """Generate AI COO insights from session data.

    Returns top opportunities with projected impact.
    """
    insights = []
    capabilities = session.get("selected_capabilities", [])
    impact = session.get("impact_model", {})
    answers = session.get("answers", [])

    all_text = " ".join(a.get("answer_text", "") for a in answers).lower()

    # Insight templates based on capabilities
    if "auto_lead_scoring" in capabilities or "outreach_automation" in capabilities:
        insights.append({
            "icon": "bi-graph-up-arrow",
            "color": "primary",
            "title": "Automate lead follow-ups",
            "impact": "+22% conversion potential",
            "detail": "AI-driven lead scoring and automated outreach eliminate missed opportunities",
        })

    if "workflow_automation" in capabilities or "resource_scheduling" in capabilities:
        insights.append({
            "icon": "bi-gear",
            "color": "warning",
            "title": "Optimize operational workflows",
            "impact": "-18% operational cost",
            "detail": "Process automation reduces manual handoffs and approval delays",
        })

    if "ai_chat_support" in capabilities or "ticket_auto_triage" in capabilities:
        insights.append({
            "icon": "bi-headset",
            "color": "info",
            "title": "Improve response time",
            "impact": "+15% customer satisfaction",
            "detail": "24/7 AI support with instant triage eliminates wait times",
        })

    if "auto_reporting" in capabilities or "data_pipeline_automation" in capabilities:
        insights.append({
            "icon": "bi-bar-chart",
            "color": "success",
            "title": "Eliminate manual reporting",
            "impact": "10+ hours saved per week",
            "detail": "Automated dashboards and reports replace spreadsheet work",
        })

    if "invoice_processing" in capabilities or "expense_categorization" in capabilities:
        insights.append({
            "icon": "bi-cash-stack",
            "color": "danger",
            "title": "Accelerate financial processing",
            "impact": "80% faster invoice handling",
            "detail": "AI extraction and matching replaces manual data entry",
        })

    if "content_generation" in capabilities or "campaign_optimization" in capabilities:
        insights.append({
            "icon": "bi-megaphone",
            "color": "success",
            "title": "Scale content and campaigns",
            "impact": "+30% marketing output",
            "detail": "AI-generated content and optimized campaigns run continuously",
        })

    if "resume_screening" in capabilities:
        insights.append({
            "icon": "bi-people",
            "color": "secondary",
            "title": "Accelerate hiring",
            "impact": "5x faster candidate screening",
            "detail": "AI screens and ranks candidates instantly from application data",
        })

    # Add generic insights if few specific ones matched
    if len(insights) < 3:
        if "manual" in all_text or "automat" in all_text:
            insights.append({
                "icon": "bi-lightning",
                "color": "warning",
                "title": "Reduce manual workload",
                "impact": "40-60% task automation",
                "detail": "Replace repetitive tasks with AI-driven workflows",
            })

    return insights[:5]


def generate_implementation_timeline(session: dict) -> list[dict]:
    """Generate phased implementation timeline."""
    caps = session.get("selected_capabilities", [])
    total = len(caps)

    if total <= 5:
        return [
            {"phase": "Week 1-2", "title": "Setup & Configuration",
             "tasks": ["Connect data sources", "Configure AI agents", "Initial testing"]},
            {"phase": "Week 3-4", "title": "Launch & Optimize",
             "tasks": ["Go live with all agents", "Monitor performance", "Fine-tune automations"]},
            {"phase": "Month 2+", "title": "Full ROI",
             "tasks": ["All systems operational", "Continuous optimization", "Scale to new areas"]},
        ]

    return [
        {"phase": "Month 1", "title": "Quick Wins",
         "tasks": ["Deploy highest-impact agents first", "Connect core data systems", "Start seeing results within weeks"]},
        {"phase": "Month 2-3", "title": "Core Build",
         "tasks": ["Roll out department-specific agents", "Integrate cross-system workflows", "Train team on AI collaboration"]},
        {"phase": "Month 4-6", "title": "Full Scale",
         "tasks": ["All agents operational", "AI Control Tower monitoring all systems", "Full ROI realized"]},
        {"phase": "Month 6+", "title": "Continuous Growth",
         "tasks": ["AI learns and improves", "Expand to new capabilities", "Compound efficiency gains"]},
    ]


def generate_personalized_cost_of_inaction(session: dict) -> list[str]:
    """Generate personalized cost-of-inaction messages from session context."""
    messages = []
    answers = session.get("answers", [])
    all_text = " ".join(a.get("answer_text", "") for a in answers).lower()
    impact = session.get("impact_model", {})

    monthly_loss = impact.get("opportunity_cost", {}).get("monthly_cost_of_inaction", 0)

    if "lead" in all_text or "sales" in all_text or "follow" in all_text:
        messages.append("Delayed follow-ups are likely costing you 15-25% of potential conversions every month.")

    if "manual" in all_text or "spreadsheet" in all_text or "data entry" in all_text:
        messages.append("Manual processes are consuming employee hours that could be redirected to revenue-generating work.")

    if "slow" in all_text or "response" in all_text or "support" in all_text:
        messages.append("Slow response times directly impact customer retention and lifetime value.")

    if "scale" in all_text or "grow" in all_text or "hire" in all_text:
        messages.append("Without AI automation, scaling requires proportional headcount increases.")

    if not messages:
        messages.append("Every month without AI automation compounds lost efficiency and missed opportunities.")

    return messages[:3]
