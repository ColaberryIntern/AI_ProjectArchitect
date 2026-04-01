"""Business interpretation engine for AI Advisory sessions.

Converts question answers + selected business capabilities into a
BusinessCapabilityMap with departments, capabilities, automation potential,
recommended AI agents, MCP servers, and skills.

Uses capability catalog selections for precise mapping, with LLM
enhancement and deterministic fallback for answer-only sessions.
"""

import json
import logging
from datetime import datetime, timezone

from execution.advisory.question_engine import ADVISORY_QUESTIONS

logger = logging.getLogger(__name__)

# Standard departments used in fallback mapping
STANDARD_DEPARTMENTS = [
    {"id": "operations", "name": "Operations", "keywords": [
        "logistics", "shipping", "warehouse", "supply chain", "manufacturing",
        "delivery", "inventory", "scheduling", "routing", "dispatch",
    ]},
    {"id": "sales", "name": "Sales", "keywords": [
        "sales", "leads", "pipeline", "quota", "closing", "prospecting",
        "revenue", "deal", "account", "upsell", "cross-sell",
    ]},
    {"id": "marketing", "name": "Marketing", "keywords": [
        "marketing", "campaign", "content", "seo", "social media",
        "advertising", "brand", "email", "newsletter", "engagement",
    ]},
    {"id": "customer_support", "name": "Customer Support", "keywords": [
        "support", "customer", "ticket", "help desk", "chat", "call center",
        "complaint", "feedback", "satisfaction", "nps", "response time",
    ]},
    {"id": "finance", "name": "Finance", "keywords": [
        "finance", "accounting", "invoice", "billing", "payment", "budget",
        "expense", "audit", "reconciliation", "forecast", "cash flow",
    ]},
    {"id": "hr", "name": "Human Resources", "keywords": [
        "hr", "hiring", "recruitment", "onboarding", "payroll", "employee",
        "training", "performance review", "talent", "retention",
    ]},
    {"id": "technology", "name": "Technology", "keywords": [
        "engineering", "development", "devops", "infrastructure", "security",
        "database", "api", "cloud", "deployment", "monitoring",
    ]},
]


def interpret_answers(
    answers: list[dict],
    business_idea: str,
    selected_capability_ids: list[str] | None = None,
) -> dict:
    """Convert advisory answers + selected capabilities into a capability map.

    If capability IDs are provided (from the selector), builds the map
    directly from the capability catalog — most accurate path.
    Otherwise falls back to LLM interpretation or keyword matching.

    Args:
        answers: List of answer dicts from the advisory session.
        business_idea: The original business idea text.
        selected_capability_ids: IDs from the capability selector.

    Returns:
        A BusinessCapabilityMap dict with departments, capabilities, agents, and MCP servers.
    """
    if selected_capability_ids:
        from execution.advisory.capability_catalog import get_ai_mappings_for_selection
        mappings = get_ai_mappings_for_selection(selected_capability_ids)
        mappings["generated_at"] = datetime.now(timezone.utc).isoformat()
        mappings["source"] = "capability_selection"
        return mappings

    try:
        return _llm_interpret(answers, business_idea)
    except Exception:
        logger.info("LLM interpretation unavailable, using fallback")
        return _fallback_capability_map(answers, business_idea)


def _llm_interpret(answers: list[dict], business_idea: str) -> dict:
    """Use LLM to interpret answers into capability map."""
    from execution.llm_client import chat

    answers_text = "\n".join(
        f"Q: {a['question_text']}\nA: {a['answer_text']}" for a in answers
    )

    system_prompt = """You are a business strategy analyst. Given a business idea and answers to 10 business questions, produce a structured JSON capability map.

Output ONLY valid JSON with this structure:
{
  "departments": [
    {
      "id": "department_id",
      "name": "Department Name",
      "capabilities": [
        {
          "id": "capability_id",
          "name": "Capability Name",
          "description": "What this capability does",
          "automation_potential": "high" | "medium" | "low",
          "current_pain_points": ["pain point 1", "pain point 2"]
        }
      ]
    }
  ]
}

Rules:
- Identify 3-7 departments relevant to the business
- Each department should have 2-4 capabilities
- automation_potential should reflect how well AI can automate each capability
- pain_points should be derived from the answers
- Use clear business language, no technical jargon"""

    user_message = f"Business Idea: {business_idea}\n\nInterview Answers:\n{answers_text}"

    response = chat(
        system_prompt=system_prompt,
        messages=[{"role": "user", "content": user_message}],
        max_tokens=2048,
        temperature=0.3,
        response_format={"type": "json_object"},
    )

    result = json.loads(response.content)
    result["generated_at"] = datetime.now(timezone.utc).isoformat()
    return result


def _fallback_capability_map(answers: list[dict], business_idea: str) -> dict:
    """Deterministic fallback: map answers to departments via keyword matching."""
    all_text = business_idea.lower()
    for a in answers:
        all_text += " " + a.get("answer_text", "").lower()

    departments = []
    for dept in STANDARD_DEPARTMENTS:
        relevance_score = sum(1 for kw in dept["keywords"] if kw in all_text)
        if relevance_score >= 1:
            capabilities = _generate_default_capabilities(dept["id"], all_text)
            departments.append({
                "id": dept["id"],
                "name": dept["name"],
                "capabilities": capabilities,
            })

    # Always include at least Operations and Technology
    dept_ids = {d["id"] for d in departments}
    for required_id in ["operations", "technology"]:
        if required_id not in dept_ids:
            dept_def = next(d for d in STANDARD_DEPARTMENTS if d["id"] == required_id)
            departments.append({
                "id": required_id,
                "name": dept_def["name"],
                "capabilities": _generate_default_capabilities(required_id, all_text),
            })

    return {
        "departments": departments,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _generate_default_capabilities(dept_id: str, context_text: str) -> list[dict]:
    """Generate default capabilities for a department."""
    capability_templates = {
        "operations": [
            {"id": "ops_workflow", "name": "Workflow Automation", "description": "Automate repetitive operational processes", "automation_potential": "high"},
            {"id": "ops_monitoring", "name": "Performance Monitoring", "description": "Track and optimize operational KPIs", "automation_potential": "medium"},
            {"id": "ops_scheduling", "name": "Resource Scheduling", "description": "Optimize allocation of resources and schedules", "automation_potential": "high"},
        ],
        "sales": [
            {"id": "sales_lead_qual", "name": "Lead Qualification", "description": "Automatically score and qualify incoming leads", "automation_potential": "high"},
            {"id": "sales_forecasting", "name": "Revenue Forecasting", "description": "Predict sales outcomes using historical data", "automation_potential": "medium"},
        ],
        "marketing": [
            {"id": "mktg_content", "name": "Content Generation", "description": "Generate marketing content and campaigns", "automation_potential": "high"},
            {"id": "mktg_analytics", "name": "Campaign Analytics", "description": "Analyze campaign performance and ROI", "automation_potential": "medium"},
        ],
        "customer_support": [
            {"id": "cs_triage", "name": "Ticket Triage", "description": "Automatically categorize and route support tickets", "automation_potential": "high"},
            {"id": "cs_chatbot", "name": "Conversational Support", "description": "AI-powered customer chat and FAQ resolution", "automation_potential": "high"},
            {"id": "cs_sentiment", "name": "Sentiment Analysis", "description": "Monitor customer satisfaction and sentiment trends", "automation_potential": "medium"},
        ],
        "finance": [
            {"id": "fin_invoicing", "name": "Invoice Processing", "description": "Automate invoice creation and reconciliation", "automation_potential": "high"},
            {"id": "fin_forecasting", "name": "Financial Forecasting", "description": "Predict cash flow and budget scenarios", "automation_potential": "medium"},
        ],
        "hr": [
            {"id": "hr_screening", "name": "Resume Screening", "description": "Automatically screen and rank job applicants", "automation_potential": "high"},
            {"id": "hr_onboarding", "name": "Onboarding Automation", "description": "Streamline new employee onboarding workflows", "automation_potential": "medium"},
        ],
        "technology": [
            {"id": "tech_monitoring", "name": "System Monitoring", "description": "Automated health checks and incident detection", "automation_potential": "high"},
            {"id": "tech_data_pipeline", "name": "Data Pipeline Management", "description": "Automate data ingestion and transformation", "automation_potential": "high"},
        ],
    }

    templates = capability_templates.get(dept_id, [
        {"id": f"{dept_id}_general", "name": "Process Automation", "description": "Automate key departmental processes", "automation_potential": "medium"},
    ])

    return [
        {**t, "current_pain_points": _extract_pain_points(context_text, dept_id)}
        for t in templates
    ]


def _extract_pain_points(context_text: str, dept_id: str) -> list[str]:
    """Extract generic pain points based on keyword presence."""
    pain_point_patterns = {
        "manual": "Manual processes requiring significant employee time",
        "slow": "Slow response times impacting productivity",
        "error": "Error-prone processes due to human involvement",
        "cost": "High operational costs from inefficient processes",
        "scale": "Difficulty scaling current processes",
        "data": "Lack of data-driven decision making",
    }
    found = [desc for keyword, desc in pain_point_patterns.items() if keyword in context_text]
    return found[:3] if found else ["Opportunity for process improvement"]
