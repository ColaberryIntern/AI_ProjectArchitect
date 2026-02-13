"""Project profile generator for the Requirements Intelligence Engine.

Extracts structured intelligence from a raw project idea via a ONE-TIME
LLM call. Produces options for 7 required profile fields plus derived
lists (constraints, NFRs, metrics, risks, use cases).

Falls back to generic options if LLM is unavailable or returns
an unparseable response.
"""

import json
import logging

from execution.llm_client import LLMClientError, LLMUnavailableError, chat, is_available

logger = logging.getLogger(__name__)

PROFILE_SYSTEM_PROMPT = (
    "You are a software product strategist. Given a raw project idea, "
    "extract structured intelligence about the product: who it's for, "
    "what problem it solves, how AI is used, deployment model, monetization, "
    "and MVP scope. Provide multiple options per field with a recommended choice."
)

PROFILE_USER_PROMPT = """Analyze this project idea and extract structured intelligence:

"{idea}"

Return ONLY valid JSON with this structure:
{{
  "fields": {{
    "problem_definition": {{
      "options": [{{"value": "short_id", "label": "Short label", "description": "1-2 sentence explanation"}}],
      "recommended": "short_id of best option",
      "confidence": 0.85
    }},
    "target_user": {{ same structure }},
    "value_proposition": {{ same structure }},
    "deployment_type": {{ same structure }},
    "ai_depth": {{ same structure }},
    "monetization_model": {{ same structure }},
    "mvp_scope": {{ same structure }}
  }},
  "derived": {{
    "technical_constraints": ["constraint 1", "constraint 2"],
    "non_functional_requirements": ["NFR 1", "NFR 2"],
    "success_metrics": ["metric 1", "metric 2"],
    "risk_assessment": ["risk 1", "risk 2"],
    "core_use_cases": ["use case 1", "use case 2"]
  }}
}}

Rules:
- Each field must have 3-5 options
- Options must be specific to THIS project idea
- recommended must match one option's value
- confidence is 0.0-1.0 (how certain you are about the recommendation)
- Derived lists should have 2-5 items each
- Return ONLY the JSON object, no markdown or explanation"""


FALLBACK_OPTIONS = {
    "problem_definition": [
        {"value": "inefficient_manual", "label": "Inefficient manual processes", "description": "Users spend excessive time on repetitive tasks that could be automated"},
        {"value": "lack_of_tools", "label": "Lack of specialized tools", "description": "No existing solution adequately addresses the specific domain needs"},
        {"value": "poor_data_insights", "label": "Poor data insights", "description": "Organizations cannot extract actionable insights from their data"},
        {"value": "communication_gaps", "label": "Communication gaps", "description": "Stakeholders struggle to collaborate effectively across teams"},
        {"value": "scaling_challenges", "label": "Scaling challenges", "description": "Current approaches do not scale with growing user or data volume"},
    ],
    "target_user": [
        {"value": "business_users", "label": "Non-technical business users", "description": "Business professionals who need intuitive, no-code interfaces"},
        {"value": "data_analysts", "label": "Data analysts", "description": "Analysts who work with data but may not write production code"},
        {"value": "developers", "label": "Software developers", "description": "Technical users building or integrating software systems"},
        {"value": "managers", "label": "Project managers", "description": "Team leads coordinating work across multiple contributors"},
        {"value": "end_consumers", "label": "End consumers", "description": "General public users of a consumer-facing application"},
    ],
    "value_proposition": [
        {"value": "time_savings", "label": "Significant time savings", "description": "Reduce time spent on key workflows by 50% or more"},
        {"value": "ai_automation", "label": "AI-powered automation", "description": "Replace manual steps with intelligent automation"},
        {"value": "better_decisions", "label": "Better decision making", "description": "Provide data-driven insights for improved outcomes"},
        {"value": "unified_platform", "label": "Unified platform", "description": "Consolidate multiple tools into a single integrated experience"},
        {"value": "accessibility", "label": "Democratize access", "description": "Make complex capabilities available to non-technical users"},
    ],
    "deployment_type": [
        {"value": "saas_multi", "label": "SaaS multi-tenant", "description": "Cloud-hosted platform shared across multiple organizations"},
        {"value": "saas_single", "label": "SaaS single-tenant", "description": "Dedicated cloud instances per customer for data isolation"},
        {"value": "internal_tool", "label": "Internal enterprise tool", "description": "Deployed within a single organization's infrastructure"},
        {"value": "on_premise", "label": "On-premise deployment", "description": "Installed on customer-managed servers for full control"},
        {"value": "hybrid", "label": "Hybrid cloud/on-premise", "description": "Core in cloud with on-premise components for sensitive data"},
    ],
    "ai_depth": [
        {"value": "no_ai", "label": "No AI", "description": "Traditional software without machine learning components"},
        {"value": "light_automation", "label": "Light automation", "description": "Rule-based automation with simple heuristics"},
        {"value": "ai_assisted", "label": "AI-assisted", "description": "AI augments human workflows with suggestions and insights"},
        {"value": "predictive_ml", "label": "Predictive ML", "description": "Machine learning models for prediction and classification"},
        {"value": "autonomous_ai", "label": "Autonomous AI", "description": "AI drives core product decisions with minimal human oversight"},
    ],
    "monetization_model": [
        {"value": "freemium", "label": "Freemium SaaS", "description": "Free tier with paid upgrades for advanced features"},
        {"value": "subscription", "label": "Subscription tiers", "description": "Monthly or annual subscriptions at multiple price points"},
        {"value": "usage_based", "label": "Usage-based pricing", "description": "Pay per API call, document, or compute unit consumed"},
        {"value": "enterprise_license", "label": "Enterprise license", "description": "Annual contracts with volume discounts for large organizations"},
        {"value": "open_core", "label": "Open core", "description": "Open-source base with proprietary enterprise features"},
    ],
    "mvp_scope": [
        {"value": "core_only", "label": "Core features only", "description": "Minimum viable set of features to validate the core hypothesis"},
        {"value": "core_plus_ai", "label": "Core + basic AI", "description": "Core features with one key AI-powered capability"},
        {"value": "full_vertical", "label": "Full vertical slice", "description": "Complete workflow for one user type, end-to-end"},
        {"value": "platform_foundation", "label": "Platform foundation", "description": "Extensible platform with plugin architecture from day one"},
        {"value": "proof_of_concept", "label": "Proof of concept", "description": "Minimal demo to validate technical feasibility only"},
    ],
}


def generate_profile(idea: str) -> dict:
    """Generate a project profile from the raw idea text.

    Makes a ONE-TIME LLM call to extract structured intelligence.
    Falls back to generic options if LLM is unavailable.

    Args:
        idea: The user's raw project idea text.

    Returns:
        Dict with 'fields' (7 profile fields with options/recommended/confidence)
        and 'derived' (lists of constraints, NFRs, metrics, risks, use cases).
    """
    if not idea or not idea.strip():
        logger.info("No idea provided, using fallback profile options")
        return _fallback_profile()

    if not is_available():
        logger.info("LLM unavailable, using fallback profile options")
        return _fallback_profile()

    try:
        prompt = PROFILE_USER_PROMPT.format(idea=idea.strip())
        response = chat(
            system_prompt=PROFILE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2048,
            temperature=0.7,
            response_format={"type": "json_object"},
        )
        return _parse_profile_response(response.content)
    except (LLMUnavailableError, LLMClientError) as e:
        logger.warning("LLM profile generation failed: %s. Using fallback.", e)
        return _fallback_profile()
    except Exception as e:
        logger.warning("Unexpected error generating profile: %s. Using fallback.", e)
        return _fallback_profile()


def _fallback_profile() -> dict:
    """Return a generic profile with fallback options for all fields."""
    fields = {}
    for field_name, options in FALLBACK_OPTIONS.items():
        fields[field_name] = {
            "options": options,
            "recommended": options[0]["value"],
            "confidence": 0.0,
        }
    return {
        "fields": fields,
        "derived": {
            "technical_constraints": [],
            "non_functional_requirements": [],
            "success_metrics": [],
            "risk_assessment": [],
            "core_use_cases": [],
        },
    }


def _parse_profile_response(raw_json: str) -> dict:
    """Parse LLM JSON response into a structured profile dict.

    Expects: {"fields": {...}, "derived": {...}}
    Falls back to generic options if parsing fails or result is invalid.
    """
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse profile JSON, using fallback")
        return _fallback_profile()

    if not isinstance(data, dict) or "fields" not in data:
        logger.warning("Profile JSON missing 'fields' key, using fallback")
        return _fallback_profile()

    fields = data["fields"]
    required_fields = [
        "problem_definition", "target_user", "value_proposition",
        "deployment_type", "ai_depth", "monetization_model", "mvp_scope",
    ]

    # Validate all 7 fields exist and have valid structure
    for field_name in required_fields:
        field_data = fields.get(field_name)
        if not field_data or not isinstance(field_data, dict):
            logger.warning("Profile field '%s' missing or invalid, using fallback", field_name)
            return _fallback_profile()

        options = field_data.get("options", [])
        if not isinstance(options, list) or len(options) < 2:
            logger.warning("Profile field '%s' has < 2 options, using fallback", field_name)
            return _fallback_profile()

        # Ensure each option has required keys
        for opt in options:
            if not isinstance(opt, dict) or "value" not in opt:
                logger.warning("Invalid option in field '%s', using fallback", field_name)
                return _fallback_profile()
            opt.setdefault("label", opt["value"])
            opt.setdefault("description", "")

        # Ensure recommended matches an option
        recommended = field_data.get("recommended")
        option_values = {o["value"] for o in options}
        if recommended not in option_values:
            field_data["recommended"] = options[0]["value"]

        # Clamp confidence
        confidence = field_data.get("confidence", 0.0)
        if not isinstance(confidence, (int, float)):
            confidence = 0.0
        field_data["confidence"] = max(0.0, min(1.0, float(confidence)))

    # Parse derived lists
    derived_raw = data.get("derived", {})
    derived = {
        "technical_constraints": _safe_string_list(derived_raw.get("technical_constraints")),
        "non_functional_requirements": _safe_string_list(derived_raw.get("non_functional_requirements")),
        "success_metrics": _safe_string_list(derived_raw.get("success_metrics")),
        "risk_assessment": _safe_string_list(derived_raw.get("risk_assessment")),
        "core_use_cases": _safe_string_list(derived_raw.get("core_use_cases")),
    }

    return {"fields": fields, "derived": derived}


def _safe_string_list(value) -> list[str]:
    """Safely convert a value to a list of strings."""
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item]
