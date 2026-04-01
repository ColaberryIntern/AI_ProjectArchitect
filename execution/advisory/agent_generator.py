"""Agent architecture generator for AI Advisory.

Transforms selected capabilities into a detailed agent architecture
with roles, inputs/outputs, triggers, and system connections.
Each capability produces one or more agents with event-driven behavior.
"""

from execution.advisory.capability_catalog import get_capabilities_by_ids


# ── Trigger type templates ──────────────────────────────────────────

_TRIGGER_TYPES = {
    "auto_lead_scoring": {"trigger_type": "event", "trigger": "New lead enters CRM"},
    "sales_pipeline_forecast": {"trigger_type": "time", "trigger": "Daily at 6:00 AM"},
    "outreach_automation": {"trigger_type": "event", "trigger": "Lead score exceeds threshold"},
    "deal_intelligence": {"trigger_type": "event", "trigger": "Deal stage changes"},
    "proposal_generator": {"trigger_type": "event", "trigger": "Sales rep requests proposal"},
    "ai_chat_support": {"trigger_type": "event", "trigger": "Customer initiates chat"},
    "ticket_auto_triage": {"trigger_type": "event", "trigger": "New support ticket created"},
    "sentiment_monitoring": {"trigger_type": "time", "trigger": "Every 15 minutes"},
    "knowledge_base_qa": {"trigger_type": "event", "trigger": "Question submitted"},
    "churn_prediction": {"trigger_type": "time", "trigger": "Daily analysis at 7:00 AM"},
    "content_generation": {"trigger_type": "event", "trigger": "Content request submitted"},
    "campaign_optimization": {"trigger_type": "time", "trigger": "Every 4 hours"},
    "audience_segmentation": {"trigger_type": "time", "trigger": "Weekly on Monday"},
    "social_media_management": {"trigger_type": "time", "trigger": "Scheduled post times"},
    "seo_optimization": {"trigger_type": "time", "trigger": "Weekly analysis"},
    "workflow_automation": {"trigger_type": "event", "trigger": "Process step completed"},
    "resource_scheduling": {"trigger_type": "event", "trigger": "Schedule request or conflict"},
    "inventory_optimization": {"trigger_type": "threshold", "trigger": "Stock level below minimum"},
    "quality_monitoring": {"trigger_type": "threshold", "trigger": "Quality metric deviation"},
    "route_optimization": {"trigger_type": "event", "trigger": "New delivery order placed"},
    "project_management": {"trigger_type": "event", "trigger": "Task status change"},
    "invoice_processing": {"trigger_type": "event", "trigger": "Invoice received"},
    "expense_categorization": {"trigger_type": "event", "trigger": "New expense submitted"},
    "financial_forecasting": {"trigger_type": "time", "trigger": "Monthly on 1st"},
    "fraud_detection": {"trigger_type": "threshold", "trigger": "Anomalous transaction detected"},
    "resume_screening": {"trigger_type": "event", "trigger": "Application received"},
    "onboarding_automation": {"trigger_type": "event", "trigger": "New hire confirmed"},
    "performance_analytics": {"trigger_type": "time", "trigger": "Weekly on Friday"},
    "training_delivery": {"trigger_type": "event", "trigger": "Skill gap identified"},
    "data_pipeline_automation": {"trigger_type": "time", "trigger": "Hourly sync"},
    "system_integration": {"trigger_type": "event", "trigger": "Data change in source system"},
    "auto_reporting": {"trigger_type": "time", "trigger": "Daily at 8:00 AM"},
    "security_monitoring": {"trigger_type": "threshold", "trigger": "Threat indicator detected"},
    "compliance_automation": {"trigger_type": "time", "trigger": "Monthly audit cycle"},
    "system_health_monitoring": {"trigger_type": "threshold", "trigger": "System metric anomaly"},
    "email_drafting": {"trigger_type": "event", "trigger": "Email composition requested"},
    "meeting_summarization": {"trigger_type": "event", "trigger": "Meeting ends"},
    "notification_routing": {"trigger_type": "event", "trigger": "Alert generated"},
    "document_qa": {"trigger_type": "event", "trigger": "Question asked"},
    "internal_chatbot": {"trigger_type": "event", "trigger": "Employee message received"},
}


def generate_agents(
    selected_capability_ids: list[str],
    include_cory: bool = False,
    problem_analysis: dict | None = None,
) -> list[dict]:
    """Generate agent architecture from selected capabilities.

    When problem_analysis is provided, adds specialized agents for the
    dominant problem domain and focuses the AI Control Tower on that domain.

    Args:
        selected_capability_ids: List of capability IDs.
        include_cory: Whether to include the AI Control Tower intelligence layer.
        problem_analysis: Output from problem_analyzer.analyze_problems().

    Returns:
        List of agent architecture dicts.
    """
    capabilities = get_capabilities_by_ids(selected_capability_ids)
    agents = []

    for cap in capabilities:
        agent_names = cap.get("agents", [])
        primary_name = agent_names[0] if agent_names else f"AI {cap['name']} Agent"
        trigger_info = _TRIGGER_TYPES.get(cap["id"], {"trigger_type": "event", "trigger": "On demand"})

        agent = {
            "id": f"agent_{cap['id']}",
            "name": primary_name,
            "capability_id": cap["id"],
            "capability_name": cap["name"],
            "department": cap["department"],
            "role": cap["description"],
            "trigger_type": trigger_info["trigger_type"],
            "trigger": trigger_info["trigger"],
            "inputs": _generate_inputs(cap),
            "outputs": _generate_outputs(cap),
            "connected_mcp_servers": cap.get("mcp_servers", []),
            "connected_skills": cap.get("skills", []),
            "is_primary_focus": False,
        }
        agents.append(agent)

    # Add specialized agents for dominant problem domain
    if problem_analysis and problem_analysis.get("primary_problem"):
        primary = problem_analysis["primary_problem"]
        primary_weight = problem_analysis.get("primary_weight", 0)

        # Mark existing agents in the primary domain
        for agent in agents:
            if _agent_matches_domain(agent, primary):
                agent["is_primary_focus"] = True

        # Add specialized agents if primary weight is strong (>0.3)
        if primary_weight >= 0.3:
            specialists = _get_domain_specialists(primary)
            for spec in specialists:
                # Avoid duplicates
                if not any(a["name"] == spec["name"] for a in agents):
                    agents.append(spec)

    # Add AI COO intelligence layer
    if include_cory:
        agents.append(_build_cory_agent(agents, problem_analysis))

    return agents


def _agent_matches_domain(agent: dict, domain: str) -> bool:
    """Check if an agent belongs to a problem domain."""
    domain_depts = {
        "operations": ["Operations"],
        "sales": ["Sales"],
        "support": ["Customer Support"],
        "marketing": ["Marketing"],
        "finance": ["Finance"],
        "data": ["Technology"],
        "hr": ["Human Resources"],
        "communication": ["Communication"],
    }
    return agent.get("department", "") in domain_depts.get(domain, [])


# ── Domain Specialist Templates ─────────────────────────────────────

_DOMAIN_SPECIALISTS = {
    "operations": [
        {"name": "AI Route Optimizer", "role": "Optimize delivery routes, minimize travel time and fuel costs",
         "trigger_type": "event", "trigger": "New delivery batch or route change",
         "inputs": ["Delivery orders", "Traffic data", "Driver availability"],
         "outputs": ["Optimized route plans", "ETAs", "Cost projections"]},
        {"name": "AI Dispatch Coordinator", "role": "Assign tasks and resources to the right teams in real-time",
         "trigger_type": "event", "trigger": "New task or resource change",
         "inputs": ["Task queue", "Resource availability", "Priority rules"],
         "outputs": ["Assignments", "Schedule updates", "Capacity alerts"]},
        {"name": "AI Delay Predictor", "role": "Predict and prevent operational delays before they happen",
         "trigger_type": "threshold", "trigger": "Delay risk score exceeds threshold",
         "inputs": ["Historical delays", "Current conditions", "Pipeline status"],
         "outputs": ["Delay alerts", "Mitigation recommendations", "Risk scores"]},
    ],
    "sales": [
        {"name": "AI Pipeline Accelerator", "role": "Identify stalled deals and recommend actions to move them forward",
         "trigger_type": "time", "trigger": "Daily pipeline review at 7:00 AM",
         "inputs": ["Deal stages", "Activity history", "Engagement signals"],
         "outputs": ["Stalled deal alerts", "Next-best-action recommendations"]},
        {"name": "AI Conversion Optimizer", "role": "Analyze conversion patterns and recommend pipeline improvements",
         "trigger_type": "time", "trigger": "Weekly conversion analysis",
         "inputs": ["Win/loss data", "Stage durations", "Lead sources"],
         "outputs": ["Conversion insights", "Process optimization suggestions"]},
        {"name": "AI Meeting Prep Agent", "role": "Prepare sales reps with context and talking points before calls",
         "trigger_type": "event", "trigger": "Meeting scheduled within 24 hours",
         "inputs": ["CRM data", "Recent interactions", "Company intel"],
         "outputs": ["Meeting brief", "Talking points", "Competitive context"]},
    ],
    "support": [
        {"name": "AI Escalation Predictor", "role": "Predict which tickets will escalate and intervene early",
         "trigger_type": "threshold", "trigger": "Escalation probability exceeds 70%",
         "inputs": ["Ticket history", "Sentiment signals", "Response times"],
         "outputs": ["Escalation alerts", "Suggested interventions"]},
        {"name": "AI Customer Health Monitor", "role": "Track customer health scores and flag at-risk accounts",
         "trigger_type": "time", "trigger": "Hourly health score refresh",
         "inputs": ["Support tickets", "Usage data", "Billing history"],
         "outputs": ["Health scores", "Churn risk alerts", "Retention actions"]},
        {"name": "AI Knowledge Builder", "role": "Automatically create and update knowledge base from resolved tickets",
         "trigger_type": "event", "trigger": "Ticket resolved with new solution",
         "inputs": ["Resolved tickets", "Agent notes", "Customer feedback"],
         "outputs": ["New KB articles", "Updated answers", "Gap reports"]},
    ],
    "marketing": [
        {"name": "AI Campaign Intelligence", "role": "Monitor all campaigns and reallocate budget to best performers",
         "trigger_type": "time", "trigger": "Every 4 hours",
         "inputs": ["Campaign metrics", "Budget allocations", "Audience data"],
         "outputs": ["Budget recommendations", "Pause/boost alerts"]},
        {"name": "AI Content Pipeline Manager", "role": "Manage content calendar, identify gaps, and prioritize production",
         "trigger_type": "time", "trigger": "Daily content review",
         "inputs": ["Content calendar", "SEO data", "Engagement metrics"],
         "outputs": ["Content priorities", "Gap alerts", "Topic suggestions"]},
    ],
    "finance": [
        {"name": "AI Cash Flow Predictor", "role": "Forecast cash flow and flag potential shortfalls",
         "trigger_type": "time", "trigger": "Daily at 6:00 AM",
         "inputs": ["AR/AP data", "Revenue projections", "Expense patterns"],
         "outputs": ["Cash flow forecast", "Shortfall alerts", "Scenario models"]},
        {"name": "AI Expense Anomaly Detector", "role": "Flag unusual expenses and policy violations automatically",
         "trigger_type": "event", "trigger": "New expense submitted",
         "inputs": ["Expense data", "Policy rules", "Historical patterns"],
         "outputs": ["Anomaly flags", "Policy violations", "Approval recommendations"]},
    ],
    "data": [
        {"name": "AI Data Quality Monitor", "role": "Monitor data pipelines and flag quality issues",
         "trigger_type": "threshold", "trigger": "Data quality score drops below threshold",
         "inputs": ["Pipeline metrics", "Schema changes", "Row counts"],
         "outputs": ["Quality alerts", "Fix suggestions", "Impact assessments"]},
    ],
}


def _get_domain_specialists(domain: str) -> list[dict]:
    """Get specialized agent templates for a domain."""
    templates = _DOMAIN_SPECIALISTS.get(domain, [])
    dept_map = {
        "operations": "Operations", "sales": "Sales", "support": "Customer Support",
        "marketing": "Marketing", "finance": "Finance", "data": "Technology",
        "hr": "Human Resources", "communication": "Communication",
    }
    dept = dept_map.get(domain, "Operations")

    agents = []
    for t in templates:
        agents.append({
            "id": f"agent_specialist_{domain}_{len(agents)}",
            "name": t["name"],
            "capability_id": f"specialist_{domain}",
            "capability_name": f"{domain.title()} Specialist",
            "department": dept,
            "role": t["role"],
            "trigger_type": t["trigger_type"],
            "trigger": t["trigger"],
            "inputs": t["inputs"],
            "outputs": t["outputs"],
            "connected_mcp_servers": [],
            "connected_skills": [],
            "is_primary_focus": True,
            "is_specialist": True,
        })
    return agents


def _build_cory_agent(existing_agents: list[dict], problem_analysis: dict | None = None) -> dict:
    """Build the AI Control Tower central intelligence agent, focused on primary problem."""
    monitored_depts = sorted(set(a["department"] for a in existing_agents))
    monitored_agents = [a["name"] for a in existing_agents[:10]]

    # Default role
    role = (
        "Monitors all AI systems, detects cross-department patterns, "
        "triggers proactive actions, and provides executive-level insights. "
        "The orchestration layer that makes individual agents work as a team."
    )
    focus_outputs = [
        "Executive briefing reports",
        "Cross-department alerts",
        "Proactive action triggers",
        "Performance optimization recommendations",
    ]

    # Customize for primary problem
    if problem_analysis and problem_analysis.get("primary_problem"):
        primary = problem_analysis["primary_problem"]
        label = problem_analysis.get("primary_label", primary)
        focus_map = {
            "operations": {
                "role": f"Primary focus: {label}. Monitors delivery delays, route efficiency, resource utilization, and operational throughput. Triggers rerouting, reassignment, and proactive alerts.",
                "outputs": ["Delay predictions", "Route optimization triggers", "Resource reallocation", "Operational efficiency reports"],
            },
            "sales": {
                "role": f"Primary focus: {label}. Monitors pipeline velocity, conversion rates, deal health, and rep performance. Triggers outreach, escalations, and coaching recommendations.",
                "outputs": ["Stalled deal alerts", "Conversion optimization", "Pipeline forecasts", "Rep coaching triggers"],
            },
            "support": {
                "role": f"Primary focus: {label}. Monitors ticket volume, resolution times, customer sentiment, and escalation patterns. Triggers proactive outreach and resource reallocation.",
                "outputs": ["Escalation predictions", "Sentiment alerts", "Staffing recommendations", "Customer health reports"],
            },
            "finance": {
                "role": f"Primary focus: {label}. Monitors cash flow, expense anomalies, payment patterns, and forecast accuracy. Triggers alerts and optimization recommendations.",
                "outputs": ["Cash flow alerts", "Anomaly flags", "Forecast updates", "Cost optimization recommendations"],
            },
        }
        if primary in focus_map:
            role = focus_map[primary]["role"]
            focus_outputs = focus_map[primary]["outputs"]

    return {
        "id": "agent_cory_intelligence",
        "name": "AI Control Tower",
        "capability_id": "cory_intelligence",
        "capability_name": "Central AI Brain",
        "department": "Executive",
        "role": role,
        "trigger_type": "threshold",
        "trigger": "Cross-system pattern detected or scheduled executive briefing",
        "inputs": [f"Output from {a}" for a in monitored_agents],
        "outputs": focus_outputs,
        "connected_mcp_servers": ["mcp_postgres", "mcp_slack", "mcp_memory"],
        "connected_skills": ["data_analytics", "anomaly_detection", "recommendation_engine", "notification_hub"],
        "monitors": monitored_depts,
        "is_cory": True,
        "is_primary_focus": True,
    }


def _generate_inputs(cap: dict) -> list[str]:
    """Generate input descriptions for an agent."""
    dept = cap["department"]
    inputs_map = {
        "Sales": ["CRM lead data", "Pipeline activity", "Customer interactions"],
        "Customer Support": ["Support tickets", "Chat transcripts", "Customer history"],
        "Marketing": ["Campaign metrics", "Content calendar", "Audience data"],
        "Operations": ["Process logs", "Resource availability", "Schedule data"],
        "Finance": ["Transaction records", "Invoice data", "Budget allocations"],
        "Human Resources": ["Applications", "Employee records", "Performance data"],
        "Technology": ["System logs", "API metrics", "Database state"],
        "Communication": ["Email threads", "Meeting recordings", "Document repository"],
    }
    return inputs_map.get(dept, ["Business data", "User requests"])[:3]


def _generate_outputs(cap: dict) -> list[str]:
    """Generate output descriptions for an agent."""
    name = cap["name"].lower()
    if "scoring" in name or "qualification" in name:
        return ["Scored leads", "Priority rankings", "Qualification reports"]
    if "forecast" in name:
        return ["Forecast reports", "Trend analysis", "Risk assessments"]
    if "automation" in name:
        return ["Completed workflows", "Status updates", "Exception alerts"]
    if "chat" in name or "chatbot" in name:
        return ["Customer responses", "Escalation requests", "Conversation logs"]
    if "monitoring" in name or "detection" in name:
        return ["Alert notifications", "Trend reports", "Anomaly flags"]
    if "report" in name or "analytics" in name:
        return ["Generated reports", "Dashboard updates", "Insight summaries"]
    if "content" in name or "generation" in name:
        return ["Generated content", "Draft documents", "Optimization suggestions"]
    return ["Processed results", "Status notifications", "Performance metrics"]


def get_agent_stats(agents: list[dict]) -> dict:
    """Compute summary statistics for the agent architecture."""
    depts = set()
    trigger_types = {}
    for a in agents:
        depts.add(a.get("department", ""))
        tt = a.get("trigger_type", "unknown")
        trigger_types[tt] = trigger_types.get(tt, 0) + 1

    has_cory = any(a.get("is_cory") for a in agents)

    return {
        "total_agents": len(agents),
        "departments": len(depts),
        "department_names": sorted(depts),
        "trigger_breakdown": trigger_types,
        "has_cory": has_cory,
    }
