"""AI Organization simulation engine.

Generates a realistic sequence of events showing how AI agents
work together in the user's organization. Events are personalized
based on selected capabilities, agents, and business context.

Produces a timeline of 15-25 events with agent interactions,
decisions, cross-agent coordination, and Cory intelligence actions.
"""

import random
from datetime import datetime, timezone


# ── Scenario Templates ──────────────────────────────────────────────
# Each template generates events for a specific department/capability

_SCENARIO_TEMPLATES = {
    "auto_lead_scoring": [
        {"event": "New lead submitted via website form", "action": "Scored lead at {score}/100 — {verdict}", "impact": "High-priority lead fast-tracked to sales"},
        {"event": "Lead data enriched from CRM", "action": "Updated lead profile with company size and industry", "impact": "Lead context improved for personalized outreach"},
    ],
    "outreach_automation": [
        {"event": "High-score lead detected", "action": "Triggered personalized email sequence", "impact": "First touch sent within 30 seconds of lead capture"},
        {"event": "Email opened by prospect", "action": "Scheduled follow-up call for sales rep", "impact": "Engagement window captured automatically"},
    ],
    "sales_pipeline_forecast": [
        {"event": "End-of-day pipeline snapshot", "action": "Forecasted {amount} in closable deals this month", "impact": "Sales team refocused on highest-probability deals"},
    ],
    "deal_intelligence": [
        {"event": "Deal stage changed to Negotiation", "action": "Generated competitive analysis and pricing recommendation", "impact": "Win probability increased from 45% to 68%"},
    ],
    "ai_chat_support": [
        {"event": "Customer initiated live chat", "action": "Resolved shipping inquiry in 12 seconds", "impact": "Customer satisfied — no human escalation needed"},
        {"event": "Complex technical question received", "action": "Escalated to specialist with full context summary", "impact": "Resolution time reduced by 70%"},
    ],
    "ticket_auto_triage": [
        {"event": "Support ticket #{ticket} created", "action": "Categorized as {category}, priority {priority}", "impact": "Routed to correct team in under 2 seconds"},
    ],
    "sentiment_monitoring": [
        {"event": "Negative sentiment spike detected on social media", "action": "Alerted customer success team with context", "impact": "Proactive outreach prevented 3 potential churns"},
    ],
    "churn_prediction": [
        {"event": "At-risk customer pattern detected", "action": "Flagged 5 accounts showing disengagement signals", "impact": "Retention campaign triggered — projected to save {amount}/mo"},
    ],
    "content_generation": [
        {"event": "Content calendar gap identified", "action": "Generated 3 blog post drafts and 5 social posts", "impact": "Content pipeline filled for next 2 weeks"},
    ],
    "campaign_optimization": [
        {"event": "Campaign performance dipped below threshold", "action": "Adjusted audience targeting and bid strategy", "impact": "Cost-per-acquisition reduced by 23%"},
    ],
    "workflow_automation": [
        {"event": "Approval request submitted", "action": "Auto-routed through 3-step approval chain", "impact": "Process completed in 4 minutes vs. typical 2 days"},
    ],
    "resource_scheduling": [
        {"event": "Schedule conflict detected", "action": "Rearranged 3 assignments to optimize coverage", "impact": "Zero downtime — all shifts covered"},
    ],
    "inventory_optimization": [
        {"event": "Stock level dropped below reorder threshold", "action": "Generated purchase order for {quantity} units", "impact": "Stockout prevented — delivery scheduled for Thursday"},
    ],
    "route_optimization": [
        {"event": "12 new delivery orders received", "action": "Optimized route plan saving 47 miles", "impact": "Fuel cost reduced by ${savings} today"},
    ],
    "project_management": [
        {"event": "Sprint deadline approaching with 3 tasks at risk", "action": "Reallocated resources and notified stakeholders", "impact": "Sprint delivery back on track"},
    ],
    "invoice_processing": [
        {"event": "Invoice batch received (23 invoices)", "action": "Extracted, validated, and matched to POs automatically", "impact": "Processing time: 45 seconds vs. 4 hours manual"},
    ],
    "expense_categorization": [
        {"event": "Employee expense report submitted", "action": "Auto-categorized 15 line items, flagged 1 policy violation", "impact": "Finance review time reduced by 80%"},
    ],
    "financial_forecasting": [
        {"event": "Monthly close data available", "action": "Generated Q{quarter} cash flow forecast", "impact": "Board-ready financial projections delivered in 3 minutes"},
    ],
    "fraud_detection": [
        {"event": "Unusual transaction pattern detected", "action": "Flagged transaction #{tx_id} for review — confidence 94%", "impact": "Potential fraud caught before settlement"},
    ],
    "resume_screening": [
        {"event": "{count} applications received for open role", "action": "Screened and ranked top 8 candidates", "impact": "Hiring manager review time cut from 6 hours to 20 minutes"},
    ],
    "auto_reporting": [
        {"event": "Daily report cycle triggered", "action": "Generated 4 department dashboards with key metrics", "impact": "Leadership briefing ready before 8 AM"},
    ],
    "data_pipeline_automation": [
        {"event": "Hourly data sync initiated", "action": "Synced {records} records across 3 systems", "impact": "All dashboards reflect real-time data"},
    ],
    "system_integration": [
        {"event": "CRM update detected", "action": "Propagated customer status change to billing and support systems", "impact": "Zero manual data entry — all systems in sync"},
    ],
    "security_monitoring": [
        {"event": "Unusual login pattern detected", "action": "Blocked access and triggered MFA verification", "impact": "Potential breach prevented — security team notified"},
    ],
    "email_drafting": [
        {"event": "Sales rep requested proposal email", "action": "Generated personalized email with deal-specific talking points", "impact": "Professional response sent in 30 seconds"},
    ],
    "meeting_summarization": [
        {"event": "Team standup meeting ended", "action": "Generated summary with 5 action items and owners", "impact": "Action items tracked automatically — no notes needed"},
    ],
    "knowledge_base_qa": [
        {"event": "Employee asked: 'What is our refund policy?'", "action": "Retrieved answer from policy docs in 0.8 seconds", "impact": "Instant answer — no manager escalation needed"},
    ],
    "internal_chatbot": [
        {"event": "New hire asked about benefits enrollment", "action": "Walked through enrollment steps with personalized guidance", "impact": "HR ticket avoided — self-service resolution"},
    ],
    "document_qa": [
        {"event": "Legal team searched for contract clause", "action": "Found relevant clause across 200+ documents in 2 seconds", "impact": "Contract review accelerated by 5 hours"},
    ],
    "quality_monitoring": [
        {"event": "Quality metric deviation detected in Line B", "action": "Paused production line and alerted supervisor", "impact": "Defective batch prevented — saved ${savings} in recalls"},
    ],
    "compliance_automation": [
        {"event": "Monthly compliance audit triggered", "action": "Scanned 1,200 records — 99.7% compliant", "impact": "4 items flagged for review, audit report auto-generated"},
    ],
}

# ── Cory Intelligence Events ────────────────────────────────────────

_CORY_EVENTS = [
    {"event": "Cross-department pattern detected", "action": "Revenue dip correlates with increased support tickets — recommended pricing review", "impact": "Projected recovery: +{pct}% revenue next month"},
    {"event": "Efficiency opportunity identified", "action": "3 departments running similar manual processes — recommended unified automation", "impact": "Estimated savings: ${savings}/month across departments"},
    {"event": "Strategic alert generated", "action": "Competitor activity spike detected — recommended accelerated campaign launch", "impact": "Market response time reduced from 2 weeks to 2 days"},
    {"event": "Resource optimization detected", "action": "Support volume dropping Tuesdays — recommended staff reallocation", "impact": "Labor costs optimized without service impact"},
    {"event": "Executive briefing prepared", "action": "Compiled daily performance across all AI systems", "impact": "Leadership has full visibility in a 60-second summary"},
]


def run_simulation(session: dict) -> dict:
    """Generate a personalized, problem-weighted simulation.

    When problem_analysis is available, generates more events for the
    dominant system and includes specialist agent actions.

    Returns:
        Dict with events list, summary stats, and impact highlights.
    """
    agents = session.get("agents", [])
    capabilities = session.get("selected_capabilities", [])
    has_cory = any(a.get("is_cory") for a in agents)
    problem_analysis = session.get("problem_analysis")

    events = _generate_events(agents, capabilities, has_cory)

    # Intersperse specialist agent events throughout the timeline
    if problem_analysis and problem_analysis.get("primary_problem"):
        specialist_agents = [a for a in agents if a.get("is_specialist")]
        if specialist_agents and events:
            # Distribute specialists evenly across the timeline
            total_time = events[-1]["time_offset"] if events else 30
            for i, agent in enumerate(specialist_agents):
                # Place at 30%, 50%, 70% of timeline
                frac = (i + 1) / (len(specialist_agents) + 1)
                time_offset = int(total_time * frac) + random.randint(1, 3)
                events.append({
                    "timestamp": f"T+{time_offset}s",
                    "time_offset": time_offset,
                    "agent": agent["name"],
                    "department": agent["department"],
                    "event": agent["trigger"],
                    "action": agent["role"][:100],
                    "impact": ", ".join(agent.get("outputs", ["Optimized result"])[:2]),
                    "type": "specialist_action",
                })

    events.sort(key=lambda e: e["time_offset"])
    events = events[:25]

    summary = _compute_summary(events)

    # Add primary focus to summary
    if problem_analysis and problem_analysis.get("primary_problem"):
        from execution.advisory.problem_analyzer import get_domain_label
        summary["primary_focus"] = get_domain_label(problem_analysis["primary_problem"])

    return {
        "events": events,
        "summary": summary,
        "total_events": len(events),
        "simulation_duration_seconds": len(events) * 2,
    }


def _generate_events(agents: list[dict], capabilities: list[str], has_cory: bool) -> list[dict]:
    """Generate a weighted timeline of simulation events."""
    events = []
    time_offset = 0
    agent_lookup = {a.get("capability_id", ""): a for a in agents}

    # Determine which capabilities are primary focus
    primary_cap_ids = set()
    for a in agents:
        if a.get("is_primary_focus") and a.get("capability_id"):
            primary_cap_ids.add(a["capability_id"])

    for cap_id in capabilities:
        templates = _SCENARIO_TEMPLATES.get(cap_id, [])
        if not templates:
            continue

        agent = agent_lookup.get(cap_id)
        agent_name = agent["name"] if agent else f"AI {cap_id.replace('_', ' ').title()} Agent"
        dept = agent["department"] if agent else "Operations"

        # Primary focus capabilities get 2 events, others get 1
        is_primary = cap_id in primary_cap_ids
        event_count = min(len(templates), 2) if is_primary else 1
        selected = random.sample(templates, min(len(templates), event_count))
        for template in selected:
            time_offset += random.randint(2, 5)
            event = {
                "timestamp": f"T+{time_offset}s",
                "time_offset": time_offset,
                "agent": agent_name,
                "department": dept,
                "event": _fill_template(template["event"]),
                "action": _fill_template(template["action"]),
                "impact": _fill_template(template["impact"]),
                "type": "agent_action",
            }
            events.append(event)

    # Add Cory events if included (interspersed at key moments)
    if has_cory and events:
        cory_templates = random.sample(_CORY_EVENTS, min(3, len(_CORY_EVENTS)))
        for i, template in enumerate(cory_templates):
            # Insert Cory events at ~25%, 50%, 75% through the timeline
            insert_time = events[-1]["time_offset"] * (i + 1) // (len(cory_templates) + 1)
            events.append({
                "timestamp": f"T+{insert_time}s",
                "time_offset": insert_time,
                "agent": "AI COO | Central Intelligence",
                "department": "Executive",
                "event": _fill_template(template["event"]),
                "action": _fill_template(template["action"]),
                "impact": _fill_template(template["impact"]),
                "type": "cory_intelligence",
            })

    # Sort by time
    events.sort(key=lambda e: e["time_offset"])

    # Cap at 25 events
    return events[:25]


def _fill_template(text: str) -> str:
    """Replace template placeholders with realistic random values."""
    replacements = {
        "{score}": str(random.randint(72, 97)),
        "{verdict}": random.choice(["Sales Qualified", "High Intent", "Enterprise Fit"]),
        "{amount}": f"${random.randint(50, 500)}K",
        "{ticket}": str(random.randint(10000, 99999)),
        "{category}": random.choice(["Billing", "Technical", "Account", "Feature Request"]),
        "{priority}": random.choice(["P1 — Urgent", "P2 — High", "P3 — Medium"]),
        "{quantity}": str(random.randint(100, 5000)),
        "{savings}": str(random.randint(200, 2000)),
        "{quarter}": str(random.randint(1, 4)),
        "{tx_id}": str(random.randint(100000, 999999)),
        "{count}": str(random.randint(15, 85)),
        "{records}": f"{random.randint(1, 50)}K",
        "{pct}": str(random.randint(8, 25)),
    }
    for key, val in replacements.items():
        text = text.replace(key, val)
    return text


def _compute_summary(events: list[dict]) -> dict:
    """Compute impact summary from simulation events."""
    agent_events = [e for e in events if e["type"] == "agent_action"]
    cory_events = [e for e in events if e["type"] == "cory_intelligence"]
    departments = set(e["department"] for e in events)

    return {
        "total_agent_actions": len(agent_events),
        "cory_interventions": len(cory_events),
        "departments_active": len(departments),
        "highlights": [
            f"{len(agent_events)} autonomous actions taken across {len(departments)} departments",
            f"{len(cory_events)} strategic interventions by AI COO",
            "All actions executed in under {duration} seconds".format(
                duration=events[-1]["time_offset"] if events else 0
            ),
            "Zero manual intervention required",
        ],
    }
