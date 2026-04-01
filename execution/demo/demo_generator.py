"""Demo configuration generator for Project Builder projects.

Transforms project data (advisory metadata, features, capabilities) into a
rich demo configuration that powers the interactive demo UI.

All generation is deterministic and fast — no LLM calls required.
"""

import hashlib
import random
from datetime import datetime, timezone


def generate_demo_config(state: dict) -> dict:
    """Generate a complete demo configuration from project state.

    Args:
        state: The project state dict (with advisory metadata, features, idea, etc.)

    Returns:
        Demo config dict with departments, agents, flows, scenarios, metrics, and graph.
    """
    advisory = state.get("advisory") or {}
    idea = state.get("idea", {}).get("original_raw", "")
    prefill = state.get("advisory_prefill", "")
    source_text = idea or prefill
    features = state.get("features", {}).get("core", [])

    # Seed RNG from project slug for deterministic output
    slug = state.get("project", {}).get("slug", "demo")
    seed = int(hashlib.md5(slug.encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)

    company = advisory.get("company_name", state.get("project", {}).get("name", "Your Company"))
    capabilities = advisory.get("selected_capabilities", [])
    outcomes = advisory.get("selected_outcomes", [])

    departments = _build_departments(capabilities, features, source_text)
    agents = _build_agents(departments, capabilities, rng)
    flows = _build_flows(agents, departments, rng)
    scenarios = _build_scenarios(departments, capabilities, rng)
    metrics = _build_metrics(state, rng)
    graph = _build_network_graph(departments, agents)

    return {
        "project_slug": slug,
        "company": company,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "departments": departments,
        "agents": agents,
        "flows": flows,
        "scenarios": scenarios,
        "metrics": metrics,
        "graph": graph,
    }


# ── Department Detection ──────────────────────────────────────────

_DEPT_KEYWORDS = {
    "Sales": ["lead", "pipeline", "deal", "crm", "outreach", "sales", "conversion", "revenue"],
    "Operations": ["route", "schedule", "workflow", "dispatch", "inventory", "process", "operations", "automation"],
    "Customer Support": ["ticket", "chat", "support", "customer", "resolve", "escalat", "sentiment"],
    "Marketing": ["campaign", "content", "seo", "email", "marketing", "audience", "brand"],
    "Finance": ["invoice", "expense", "forecast", "budget", "financial", "payment", "fraud"],
    "HR": ["hire", "recruit", "onboard", "employee", "talent", "resume", "hr"],
    "Engineering": ["code", "deploy", "build", "test", "sprint", "devops", "engineering"],
    "Logistics": ["delivery", "freight", "shipping", "warehouse", "tracking", "logistics"],
}


def _build_departments(capabilities: list[str], features: list[dict], source_text: str) -> list[dict]:
    """Detect relevant departments from project data."""
    text = source_text.lower()
    for f in features:
        text += " " + f.get("name", "").lower() + " " + f.get("description", "").lower()
    for cap in capabilities:
        text += " " + cap.replace("_", " ")

    scored = {}
    for dept, keywords in _DEPT_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text)
        if score > 0:
            scored[dept] = score

    if not scored:
        scored = {"Operations": 3, "Sales": 2, "Customer Support": 1}

    # Take top 4 departments
    top = sorted(scored.items(), key=lambda x: x[1], reverse=True)[:4]

    dept_colors = {
        "Sales": "#3b82f6", "Operations": "#f59e0b", "Customer Support": "#10b981",
        "Marketing": "#8b5cf6", "Finance": "#ef4444", "HR": "#06b6d4",
        "Engineering": "#6366f1", "Logistics": "#f97316",
    }

    return [
        {"name": d, "score": s, "color": dept_colors.get(d, "#64748b"), "agent_count": max(2, s)}
        for d, s in top
    ]


# ── Agent Generation ──────────────────────────────────────────────

_AGENT_TEMPLATES = {
    "Sales": [
        {"name": "Lead Qualifier", "role": "Scores and qualifies inbound leads", "icon": "bi-bullseye"},
        {"name": "Pipeline Manager", "role": "Monitors deal stages and forecasts revenue", "icon": "bi-graph-up-arrow"},
        {"name": "Outreach Agent", "role": "Triggers personalized follow-up sequences", "icon": "bi-send"},
    ],
    "Operations": [
        {"name": "Workflow Automator", "role": "Routes approvals and automates processes", "icon": "bi-gear"},
        {"name": "Resource Optimizer", "role": "Allocates resources across teams", "icon": "bi-people"},
        {"name": "Process Monitor", "role": "Tracks SLAs and flags bottlenecks", "icon": "bi-speedometer2"},
    ],
    "Customer Support": [
        {"name": "Ticket Router", "role": "Triages and routes support tickets", "icon": "bi-headset"},
        {"name": "Chat Agent", "role": "Resolves customer queries in real-time", "icon": "bi-chat-dots"},
        {"name": "Sentiment Analyzer", "role": "Monitors customer satisfaction signals", "icon": "bi-emoji-smile"},
    ],
    "Marketing": [
        {"name": "Campaign Optimizer", "role": "Adjusts targeting and bids in real-time", "icon": "bi-megaphone"},
        {"name": "Content Generator", "role": "Creates blog posts and social content", "icon": "bi-pencil-square"},
    ],
    "Finance": [
        {"name": "Invoice Processor", "role": "Extracts, validates, and matches invoices", "icon": "bi-receipt"},
        {"name": "Fraud Detector", "role": "Flags suspicious transactions in real-time", "icon": "bi-shield-exclamation"},
        {"name": "Forecast Analyst", "role": "Generates cash flow and revenue forecasts", "icon": "bi-graph-up"},
    ],
    "HR": [
        {"name": "Resume Screener", "role": "Ranks candidates by fit and experience", "icon": "bi-file-earmark-person"},
        {"name": "Onboarding Guide", "role": "Walks new hires through setup steps", "icon": "bi-person-plus"},
    ],
    "Logistics": [
        {"name": "Route Optimizer", "role": "Calculates optimal delivery routes", "icon": "bi-map"},
        {"name": "Tracking Agent", "role": "Provides real-time shipment updates", "icon": "bi-truck"},
        {"name": "Dispatch Agent", "role": "Assigns drivers to optimized routes", "icon": "bi-signpost-2"},
    ],
    "Engineering": [
        {"name": "Sprint Manager", "role": "Tracks sprint progress and flags risks", "icon": "bi-kanban"},
        {"name": "Code Reviewer", "role": "Automated code quality and security checks", "icon": "bi-code-slash"},
    ],
}


def _build_agents(departments: list[dict], capabilities: list[str], rng: random.Random) -> list[dict]:
    """Generate agents for each department."""
    agents = [
        {"id": "control-tower", "name": "AI Control Tower", "role": "Central orchestrator monitoring all systems",
         "department": "Executive", "icon": "bi-cpu", "color": "#1a1a2e", "is_hub": True},
    ]
    agent_id = 1
    for dept in departments:
        templates = _AGENT_TEMPLATES.get(dept["name"], [])
        if not templates:
            templates = [{"name": f"{dept['name']} Agent", "role": f"Manages {dept['name'].lower()} operations", "icon": "bi-robot"}]
        count = min(len(templates), dept.get("agent_count", 2))
        selected = templates[:count]
        for t in selected:
            agents.append({
                "id": f"agent-{agent_id}",
                "name": t["name"],
                "role": t["role"],
                "department": dept["name"],
                "icon": t.get("icon", "bi-robot"),
                "color": dept["color"],
                "is_hub": False,
            })
            agent_id += 1
    return agents


# ── Flow Generation ──────────────────────────────────────────────

def _build_flows(agents: list[dict], departments: list[dict], rng: random.Random) -> list[dict]:
    """Generate execution flows showing agent coordination."""
    flows = []
    non_hub = [a for a in agents if not a.get("is_hub")]

    if len(non_hub) < 2:
        return flows

    # Flow 1: Primary department chain
    if departments:
        primary_dept = departments[0]["name"]
        dept_agents = [a for a in non_hub if a["department"] == primary_dept]
        if len(dept_agents) >= 2:
            steps = [{"agent": a["name"], "action": a["role"]} for a in dept_agents[:3]]
            steps.append({"agent": "AI Control Tower", "action": "Logged outcome and updated dashboards"})
            flows.append({"name": f"{primary_dept} Automation", "steps": steps})

    # Flow 2: Cross-department
    if len(departments) >= 2:
        a1 = next((a for a in non_hub if a["department"] == departments[0]["name"]), None)
        a2 = next((a for a in non_hub if a["department"] == departments[1]["name"]), None)
        if a1 and a2:
            flows.append({
                "name": "Cross-Department Coordination",
                "steps": [
                    {"agent": "AI Control Tower", "action": "Detected cross-department opportunity"},
                    {"agent": a1["name"], "action": a1["role"]},
                    {"agent": a2["name"], "action": a2["role"]},
                    {"agent": "AI Control Tower", "action": "Coordinated handoff and reported results"},
                ],
            })

    # Flow 3: Escalation flow
    if len(non_hub) >= 2:
        flows.append({
            "name": "Intelligent Escalation",
            "steps": [
                {"agent": non_hub[0]["name"], "action": "Initial processing and classification"},
                {"agent": non_hub[0]["name"], "action": "Confidence below threshold — escalating"},
                {"agent": "AI Control Tower", "action": "Routed to specialist with full context"},
                {"agent": non_hub[1]["name"], "action": "Resolved with specialized handling"},
            ],
        })

    return flows


# ── Scenario Generation ──────────────────────────────────────────

_SCENARIO_BANK = {
    "Sales": {
        "name": "Lead Conversion Pipeline",
        "description": "Watch AI qualify, score, and route a new lead through your sales pipeline",
        "icon": "bi-funnel",
        "events": [
            {"agent": "Lead Qualifier", "event": "New lead submitted via website", "action": "Scored at 91/100 — Enterprise Fit", "impact": "+$45K pipeline", "delay": 2000},
            {"agent": "Pipeline Manager", "event": "High-value lead detected", "action": "Added to priority queue", "impact": "Response time: 28 seconds", "delay": 1500},
            {"agent": "Outreach Agent", "event": "Engagement sequence triggered", "action": "Sent personalized email with case study", "impact": "Open rate: 67%", "delay": 2000},
            {"agent": "AI Control Tower", "event": "Pipeline velocity alert", "action": "Lead progressing 3x faster than average", "impact": "Forecasted close: 14 days", "delay": 2500},
        ],
    },
    "Operations": {
        "name": "Operations Optimization",
        "description": "See AI streamline workflows, allocate resources, and eliminate bottlenecks",
        "icon": "bi-gear",
        "events": [
            {"agent": "Process Monitor", "event": "Approval bottleneck detected", "action": "Flagged 5 pending approvals past SLA", "impact": "Average wait: 4.2 hours", "delay": 2000},
            {"agent": "Workflow Automator", "event": "Auto-routing triggered", "action": "Escalated 3 approvals to backup approvers", "impact": "SLA compliance restored", "delay": 1500},
            {"agent": "Resource Optimizer", "event": "Team utilization analyzed", "action": "Rebalanced 2 assignments across teams", "impact": "Utilization: 87% → 94%", "delay": 2000},
            {"agent": "AI Control Tower", "event": "Efficiency report generated", "action": "Operations running 31% faster than baseline", "impact": "Projected savings: $18K/month", "delay": 2500},
        ],
    },
    "Customer Support": {
        "name": "Support Automation",
        "description": "Watch AI resolve tickets, route escalations, and improve satisfaction scores",
        "icon": "bi-headset",
        "events": [
            {"agent": "Ticket Router", "event": "Ticket #48291 created", "action": "Classified: Billing, Priority P2", "impact": "Routed in 0.8 seconds", "delay": 1500},
            {"agent": "Chat Agent", "event": "Customer initiated live chat", "action": "Resolved shipping inquiry autonomously", "impact": "Resolution: 14 seconds", "delay": 2000},
            {"agent": "Sentiment Analyzer", "event": "Satisfaction dip detected", "action": "Flagged 3 accounts with declining CSAT", "impact": "Proactive outreach triggered", "delay": 2000},
            {"agent": "AI Control Tower", "event": "Support dashboard updated", "action": "Resolution rate: 94% | Avg time: 2.1 min", "impact": "CSAT score: 4.8/5.0", "delay": 2500},
        ],
    },
    "Marketing": {
        "name": "Campaign Intelligence",
        "description": "See AI optimize campaigns, generate content, and maximize ROI",
        "icon": "bi-megaphone",
        "events": [
            {"agent": "Campaign Optimizer", "event": "Campaign CTR dropped below 2%", "action": "Adjusted targeting and creative rotation", "impact": "CTR recovered to 3.4%", "delay": 2000},
            {"agent": "Content Generator", "event": "Content gap identified", "action": "Drafted 3 blog posts aligned to search trends", "impact": "Pipeline filled for 2 weeks", "delay": 2000},
            {"agent": "AI Control Tower", "event": "Marketing ROI report", "action": "Campaign ROAS: 4.2x across channels", "impact": "Cost per acquisition down 28%", "delay": 2500},
        ],
    },
    "Finance": {
        "name": "Financial Processing",
        "description": "Watch AI process invoices, detect fraud, and generate forecasts",
        "icon": "bi-currency-dollar",
        "events": [
            {"agent": "Invoice Processor", "event": "Batch received: 23 invoices", "action": "Extracted, validated, matched to POs", "impact": "45 seconds vs 4 hours manual", "delay": 2000},
            {"agent": "Fraud Detector", "event": "Anomaly detected", "action": "Flagged transaction #847291 — confidence 96%", "impact": "Potential fraud caught pre-settlement", "delay": 2000},
            {"agent": "Forecast Analyst", "event": "Monthly close data ready", "action": "Generated Q2 cash flow projection", "impact": "Board-ready in 3 minutes", "delay": 2500},
        ],
    },
    "Logistics": {
        "name": "Logistics Optimization",
        "description": "See AI optimize routes, dispatch drivers, and track deliveries in real-time",
        "icon": "bi-truck",
        "events": [
            {"agent": "Route Optimizer", "event": "18 new orders received", "action": "Optimized routes saving 52 miles", "impact": "Fuel savings: $340 today", "delay": 2000},
            {"agent": "Dispatch Agent", "event": "Driver assignments updated", "action": "Balanced load across 6 drivers", "impact": "All deliveries on-time projected", "delay": 1500},
            {"agent": "Tracking Agent", "event": "Delivery update", "action": "12 packages delivered, 6 in transit", "impact": "Customer notifications sent", "delay": 2000},
            {"agent": "AI Control Tower", "event": "Logistics dashboard", "action": "Fleet utilization: 96% | On-time: 99.2%", "impact": "Operational cost: -22% vs. last month", "delay": 2500},
        ],
    },
}


def _build_scenarios(departments: list[dict], capabilities: list[str], rng: random.Random) -> list[dict]:
    """Build 3-5 demo scenarios based on project departments."""
    scenarios = []
    for dept in departments:
        scenario = _SCENARIO_BANK.get(dept["name"])
        if scenario:
            scenarios.append(scenario)
    if not scenarios:
        scenarios.append(_SCENARIO_BANK["Operations"])
    return scenarios[:5]


# ── Metrics ──────────────────────────────────────────────────────

def _build_metrics(state: dict, rng: random.Random) -> dict:
    """Build KPI metrics from project impact data or advisory data."""
    advisory = state.get("advisory") or {}

    # Try to pull from advisory session impact data
    try:
        from execution.advisory.advisory_state_manager import load_session
        session_id = advisory.get("advisory_session_id", "")
        if session_id:
            session = load_session(session_id)
            impact = session.get("impact_model") or {}
            cost_savings = impact.get("cost_savings", {}).get("total_annual", 0)
            revenue_gain = impact.get("revenue_impact", {}).get("estimated_annual_revenue_gain", 0)
            agents = session.get("agents", [])
            return {
                "revenue_impact": revenue_gain or rng.randint(50000, 300000),
                "cost_savings": cost_savings or rng.randint(80000, 400000),
                "efficiency_gain": rng.randint(25, 45),
                "active_agents": len(agents) if agents else rng.randint(6, 18),
                "time_saved_hours": rng.randint(120, 600),
                "tasks_automated": rng.randint(200, 2000),
            }
    except Exception:
        pass

    return {
        "revenue_impact": rng.randint(50000, 300000),
        "cost_savings": rng.randint(80000, 400000),
        "efficiency_gain": rng.randint(25, 45),
        "active_agents": rng.randint(6, 18),
        "time_saved_hours": rng.randint(120, 600),
        "tasks_automated": rng.randint(200, 2000),
    }


# ── Network Graph ────────────────────────────────────────────────

def _build_network_graph(departments: list[dict], agents: list[dict]) -> dict:
    """Build D3-compatible network graph data."""
    nodes = []
    links = []

    # Hub node
    nodes.append({"id": "control-tower", "name": "AI Control Tower", "group": "Executive",
                  "color": "#1a1a2e", "size": 24, "icon": "bi-cpu"})

    for agent in agents:
        if agent.get("is_hub"):
            continue
        nodes.append({
            "id": agent["id"],
            "name": agent["name"],
            "group": agent["department"],
            "color": agent["color"],
            "size": 14,
            "icon": agent.get("icon", "bi-robot"),
        })
        # Connect every agent to control tower
        links.append({"source": "control-tower", "target": agent["id"], "strength": 0.3})

    # Connect agents within same department
    by_dept = {}
    for agent in agents:
        if not agent.get("is_hub"):
            by_dept.setdefault(agent["department"], []).append(agent["id"])

    for dept_agents in by_dept.values():
        for i in range(len(dept_agents) - 1):
            links.append({"source": dept_agents[i], "target": dept_agents[i + 1], "strength": 0.6})

    return {"nodes": nodes, "links": links}
