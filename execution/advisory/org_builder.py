"""AI Organization structure builder for advisory sessions.

Generates a hierarchical org chart of AI roles based on the
business capability map. Uses LLM for creative role design
with a deterministic fallback.
"""

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Default AI role templates per department
_DEFAULT_ROLES = {
    "operations": [
        {"title": "AI Operations Manager", "type": "department_head", "responsibilities": [
            "Oversee all AI-driven operational workflows",
            "Monitor automation performance and efficiency metrics",
            "Coordinate between AI systems and human operations teams",
        ], "fte": 2.0},
        {"title": "AI Process Optimizer", "type": "specialist", "responsibilities": [
            "Identify and automate repetitive operational tasks",
            "Optimize resource scheduling and allocation",
            "Reduce operational bottlenecks through intelligent routing",
        ], "fte": 1.5},
    ],
    "sales": [
        {"title": "AI Sales Director", "type": "department_head", "responsibilities": [
            "Drive AI-enhanced lead qualification and scoring",
            "Optimize sales pipeline with predictive analytics",
            "Automate sales outreach and follow-up sequences",
        ], "fte": 1.5},
        {"title": "AI Lead Qualifier", "type": "specialist", "responsibilities": [
            "Score and prioritize incoming leads automatically",
            "Enrich prospect data with external intelligence",
            "Route qualified leads to appropriate sales teams",
        ], "fte": 1.0},
    ],
    "marketing": [
        {"title": "AI Marketing Strategist", "type": "department_head", "responsibilities": [
            "Orchestrate AI-powered marketing campaigns",
            "Optimize content creation and distribution",
            "Analyze campaign performance and audience behavior",
        ], "fte": 1.5},
        {"title": "AI Content Creator", "type": "specialist", "responsibilities": [
            "Generate personalized marketing content at scale",
            "Optimize messaging for different audience segments",
            "Maintain brand voice consistency across AI outputs",
        ], "fte": 1.0},
    ],
    "customer_support": [
        {"title": "AI Customer Experience Lead", "type": "department_head", "responsibilities": [
            "Manage AI-powered support channels",
            "Monitor customer satisfaction and AI response quality",
            "Escalate complex issues from AI to human agents",
        ], "fte": 2.0},
        {"title": "AI Support Agent", "type": "agent", "responsibilities": [
            "Handle tier-1 customer inquiries automatically",
            "Provide 24/7 multilingual customer support",
            "Learn from resolved tickets to improve responses",
        ], "fte": 3.0},
    ],
    "finance": [
        {"title": "AI Finance Analyst", "type": "department_head", "responsibilities": [
            "Automate financial reporting and reconciliation",
            "Generate cash flow forecasts and budget projections",
            "Detect anomalies in transactions and expenses",
        ], "fte": 1.0},
    ],
    "hr": [
        {"title": "AI Talent Manager", "type": "department_head", "responsibilities": [
            "Automate resume screening and candidate ranking",
            "Optimize onboarding workflows for new hires",
            "Analyze workforce performance patterns",
        ], "fte": 1.0},
    ],
    "technology": [
        {"title": "AI Systems Architect", "type": "department_head", "responsibilities": [
            "Design and maintain AI infrastructure",
            "Monitor system health and performance",
            "Manage data pipelines and model deployment",
        ], "fte": 1.5},
        {"title": "AI Data Engineer", "type": "specialist", "responsibilities": [
            "Build and maintain automated data pipelines",
            "Ensure data quality and governance",
            "Optimize data storage and retrieval for AI workloads",
        ], "fte": 1.0},
    ],
}


def build_org_structure(
    capability_map: dict,
    maturity_score: dict | None = None,
    business_idea: str = "",
) -> list[dict]:
    """Generate an AI organization structure from capability map.

    Attempts LLM-powered creative role design first, falls back
    to template-based mapping.

    Args:
        capability_map: Business capability map with departments.
        maturity_score: Optional maturity assessment.
        business_idea: The original business idea text.

    Returns:
        List of OrgNode dicts forming a hierarchical tree.
    """
    try:
        nodes = _llm_build_org(capability_map, business_idea)
        if nodes:  # LLM may return empty list
            return nodes
    except Exception:
        pass
    logger.info("Using fallback org builder")
    return _fallback_org_structure(capability_map)


def _llm_build_org(capability_map: dict, business_idea: str) -> list[dict]:
    """Use LLM to generate creative, business-appropriate AI role names."""
    from execution.llm_client import chat

    dept_summary = json.dumps(
        [{"name": d["name"], "capabilities": [c["name"] for c in d.get("capabilities", [])]}
         for d in capability_map.get("departments", [])],
        indent=2,
    )

    system_prompt = """You are an AI organizational design expert. Given a business description and its AI capability map, design an AI workforce organization structure.

Output ONLY valid JSON as an array of org nodes:
[
  {
    "id": "unique_id",
    "title": "Role Title",
    "type": "executive" | "department_head" | "specialist" | "agent",
    "parent_id": null (for root) or "parent_node_id",
    "department": "Department Name",
    "responsibilities": ["responsibility 1", "responsibility 2", "responsibility 3"],
    "ai_tools": ["tool or capability"],
    "estimated_fte_equivalent": 1.5
  }
]

Rules:
- Start with ONE root node: an AI executive (e.g., "AI Control Tower")
- Each department gets a department_head reporting to the root
- Add 1-3 specialists or agents per department based on capabilities
- Use clear, executive-friendly role titles (no technical jargon)
- estimated_fte_equivalent represents how many human FTEs this AI role replaces
- Total org should have 8-20 nodes"""

    user_message = f"Business: {business_idea}\n\nDepartments and Capabilities:\n{dept_summary}"

    response = chat(
        system_prompt=system_prompt,
        messages=[{"role": "user", "content": user_message}],
        max_tokens=2048,
        temperature=0.4,
        response_format={"type": "json_object"},
    )

    result = json.loads(response.content)
    # Handle both direct array and wrapped object responses
    if isinstance(result, dict):
        nodes = result.get("org_nodes", result.get("nodes", []))
    else:
        nodes = result

    return nodes


def _fallback_org_structure(capability_map: dict) -> list[dict]:
    """Deterministic fallback: map departments to standard AI roles."""
    nodes = []
    node_counter = 0

    # Root node: AI COO
    root_id = f"node_{node_counter}"
    nodes.append({
        "id": root_id,
        "title": "AI Control Tower",
        "type": "executive",
        "parent_id": None,
        "department": "Executive",
        "responsibilities": [
            "Oversee all AI operations across the organization",
            "Align AI initiatives with business strategy",
            "Report AI performance and ROI to leadership",
        ],
        "ai_tools": ["Strategic Planning AI", "Executive Dashboard"],
        "estimated_fte_equivalent": 1.0,
    })
    node_counter += 1

    for dept in capability_map.get("departments", []):
        dept_id = dept.get("id", "unknown")
        dept_name = dept.get("name", dept_id.replace("_", " ").title())

        # Use recommended_agents from AI mappings if available, else default roles
        recommended_agents = dept.get("recommended_agents", [])
        if recommended_agents:
            roles = _build_roles_from_agents(recommended_agents, dept_name)
        else:
            roles = _DEFAULT_ROLES.get(dept_id, [
                {"title": f"AI {dept_name} Manager", "type": "department_head",
                 "responsibilities": [f"Manage AI operations for {dept_name}"], "fte": 1.0}
            ])

        parent_for_dept = root_id
        for role in roles:
            node_id = f"node_{node_counter}"
            ai_tools = [c.get("name", "AI Tool") for c in dept.get("capabilities", [])]

            nodes.append({
                "id": node_id,
                "title": role["title"],
                "type": role["type"],
                "parent_id": parent_for_dept,
                "department": dept_name,
                "responsibilities": role["responsibilities"],
                "ai_tools": ai_tools[:3],
                "estimated_fte_equivalent": role["fte"],
            })

            # First role in dept becomes the parent for subsequent roles
            if role["type"] == "department_head":
                parent_for_dept = node_id

            node_counter += 1

    return nodes


def _build_roles_from_agents(agent_names: list[str], dept_name: str) -> list[dict]:
    """Build org roles from recommended AI agent names."""
    roles = []
    for i, agent_name in enumerate(agent_names):
        role_type = "department_head" if i == 0 else "specialist"
        fte = 1.5 if i == 0 else 1.0
        roles.append({
            "title": agent_name,
            "type": role_type,
            "responsibilities": [
                f"Automate {dept_name.lower()} processes using AI",
                f"Execute {agent_name.replace('AI ', '').lower()} workflows",
                f"Report performance metrics to leadership",
            ],
            "fte": fte,
        })
    return roles


def flatten_org_tree(nodes: list[dict]) -> dict:
    """Convert flat node list to a nested tree structure for D3.js.

    Returns a single root dict with nested 'children' arrays.
    """
    node_map = {n["id"]: {**n, "children": []} for n in nodes}

    root = None
    for node in nodes:
        if node.get("parent_id") is None:
            root = node_map[node["id"]]
        else:
            parent = node_map.get(node["parent_id"])
            if parent:
                parent["children"].append(node_map[node["id"]])

    return root or {"id": "root", "title": "AI Organization", "children": []}


def get_org_stats(nodes: list[dict]) -> dict:
    """Calculate summary statistics for the org structure."""
    total_fte = sum(n.get("estimated_fte_equivalent", 0) for n in nodes)
    departments = set(n.get("department", "") for n in nodes if n.get("type") != "executive")

    type_counts = {}
    for n in nodes:
        t = n.get("type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1

    return {
        "total_roles": len(nodes),
        "total_fte_equivalent": round(total_fte, 1),
        "departments": len(departments),
        "department_names": sorted(departments),
        "role_type_breakdown": type_counts,
    }
