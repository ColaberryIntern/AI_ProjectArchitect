"""LLM-powered outline generator for Outline Generation phase.

Generates a 7-section Requirements Document outline based on the
project idea and selected features. Falls back to default titles
with empty summaries if LLM is unavailable.
"""

import json
import logging

from execution.llm_client import LLMClientError, LLMUnavailableError, chat, is_available

logger = logging.getLogger(__name__)

OUTLINE_SYSTEM_PROMPT = (
    "You are a software requirements architect. "
    "Generate structured document outlines for software projects."
)

OUTLINE_USER_PROMPT = """Given this project:

**Idea:** {idea}

**Selected Features:**
{feature_list}

Generate a 7-section Requirements Document outline.
Return ONLY valid JSON with this structure:
{{"sections": [
  {{"index": 1, "title": "Section Title", "type": "required", "summary": "2-3 sentence summary of what this section covers"}},
  ...
]}}

The 7 sections MUST cover these topics in this order:
1. System Purpose & Context — Why this system exists and the problem it solves
2. Target Users & Roles — Who uses the system and their different access levels
3. Core Capabilities — The main features and what they do (reference the selected features)
4. Non-Goals & Explicit Exclusions — What this system intentionally does NOT do
5. High-Level Architecture — Technical approach, major components, data flow
6. Execution Phases — Build order, milestones, MVP vs future phases
7. Risks, Constraints, and Assumptions — Technical risks, timeline constraints, key assumptions

Rules:
- Exactly 7 sections
- Each summary is 2-3 sentences, specific to THIS project
- Reference actual features from the list above
- Return ONLY the JSON object, no markdown"""

DEFAULT_SECTIONS = [
    {"index": 1, "title": "System Purpose & Context", "type": "required", "summary": ""},
    {"index": 2, "title": "Target Users & Roles", "type": "required", "summary": ""},
    {"index": 3, "title": "Core Capabilities", "type": "required", "summary": ""},
    {"index": 4, "title": "Non-Goals & Explicit Exclusions", "type": "required", "summary": ""},
    {"index": 5, "title": "High-Level Architecture", "type": "required", "summary": ""},
    {"index": 6, "title": "Execution Phases", "type": "required", "summary": ""},
    {"index": 7, "title": "Risks, Constraints, and Assumptions", "type": "required", "summary": ""},
]


def generate_outline(idea: str, features: list[dict]) -> list[dict]:
    """Generate a 7-section outline via LLM. Falls back to defaults."""
    if not idea or not idea.strip():
        return [dict(s) for s in DEFAULT_SECTIONS]

    if not is_available():
        return [dict(s) for s in DEFAULT_SECTIONS]

    feature_list = "\n".join(
        f"- {f['name']}: {f.get('description', '')}" for f in features
    ) or "- No specific features selected"

    try:
        prompt = OUTLINE_USER_PROMPT.format(
            idea=idea.strip(), feature_list=feature_list
        )
        response = chat(
            system_prompt=OUTLINE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            response_format={"type": "json_object"},
        )
        return _parse_outline_response(response.content)
    except (LLMUnavailableError, LLMClientError) as e:
        logger.warning("LLM outline generation failed: %s. Using defaults.", e)
        return [dict(s) for s in DEFAULT_SECTIONS]
    except Exception as e:
        logger.warning("Unexpected error generating outline: %s. Using defaults.", e)
        return [dict(s) for s in DEFAULT_SECTIONS]


ENHANCED_SECTIONS = [
    {"index": 1, "title": "Executive Summary", "type": "required", "summary": ""},
    {"index": 2, "title": "Problem & Market Context", "type": "required", "summary": ""},
    {"index": 3, "title": "User Personas & Core Use Cases", "type": "required", "summary": ""},
    {"index": 4, "title": "Functional Requirements", "type": "required", "summary": ""},
    {"index": 5, "title": "AI & Intelligence Architecture", "type": "required", "summary": ""},
    {"index": 6, "title": "Non-Functional Requirements", "type": "required", "summary": ""},
    {"index": 7, "title": "Technical Architecture & Data Model", "type": "required", "summary": ""},
    {"index": 8, "title": "Security & Compliance", "type": "required", "summary": ""},
    {"index": 9, "title": "Success Metrics & KPIs", "type": "required", "summary": ""},
    {"index": 10, "title": "Roadmap & Phased Delivery", "type": "required", "summary": ""},
]

# Light mode: 5 sections (preserves Functional Requirements for intelligence goals)
LIGHT_SECTIONS = [
    {"index": 1, "title": "Executive Summary", "type": "required", "summary": ""},
    {"index": 2, "title": "User Personas & Core Use Cases", "type": "required", "summary": ""},
    {"index": 3, "title": "Functional Requirements", "type": "required", "summary": ""},
    {"index": 4, "title": "Technical Architecture & Data Model", "type": "required", "summary": ""},
    {"index": 5, "title": "Roadmap & Phased Delivery", "type": "required", "summary": ""},
]

# Standard mode: 8 sections (preserves both Functional Requirements and AI Architecture)
STANDARD_SECTIONS = [
    {"index": 1, "title": "Executive Summary", "type": "required", "summary": ""},
    {"index": 2, "title": "Problem & Market Context", "type": "required", "summary": ""},
    {"index": 3, "title": "User Personas & Core Use Cases", "type": "required", "summary": ""},
    {"index": 4, "title": "Functional Requirements", "type": "required", "summary": ""},
    {"index": 5, "title": "AI & Intelligence Architecture", "type": "required", "summary": ""},
    {"index": 6, "title": "Technical Architecture & Data Model", "type": "required", "summary": ""},
    {"index": 7, "title": "Security & Compliance", "type": "required", "summary": ""},
    {"index": 8, "title": "Roadmap & Phased Delivery", "type": "required", "summary": ""},
]


def get_sections_for_depth(depth_mode: str) -> list[dict]:
    """Return the appropriate section template list for a given depth mode.

    Args:
        depth_mode: One of 'light', 'standard', 'professional', 'enterprise'.

    Returns:
        List of section dicts (copies, not originals).
    """
    from execution.build_depth import resolve_depth_mode
    resolved = resolve_depth_mode(depth_mode)
    if resolved == "light":
        return [dict(s) for s in LIGHT_SECTIONS]
    elif resolved == "standard":
        return [dict(s) for s in STANDARD_SECTIONS]
    else:
        return [dict(s) for s in ENHANCED_SECTIONS]

OUTLINE_FROM_PROFILE_PROMPT = """Generate a {section_count}-section Requirements Document outline using ONLY this structured project profile:

**Problem:** {problem_definition}
**Target User:** {target_user}
**Value Proposition:** {value_proposition}
**Deployment:** {deployment_type}
**AI Depth:** {ai_depth}
**Monetization:** {monetization_model}
**MVP Scope:** {mvp_scope}

**Technical Constraints:** {technical_constraints}
**Non-Functional Requirements:** {nfrs}
**Success Metrics:** {success_metrics}
**Risks:** {risks}
**Core Use Cases:** {use_cases}

**Selected Features:**
{feature_list}

**Intelligence Goals:**
{intelligence_goals_section}

Return ONLY valid JSON with this structure:
{{"sections": [
  {{"index": 1, "title": "Section Title", "type": "required", "summary": "200-400 word summary"}},
  ...
]}}

The {section_count} sections MUST be:
{section_list}

Rules:
- Exactly {section_count} sections
- Each summary is 200-400 words, specific to THIS project profile
- NEVER reference raw idea text — use only the structured profile fields
- Reference actual features and use cases from the lists above
- Return ONLY the JSON object, no markdown"""


def generate_outline_from_profile(
    profile: dict,
    features: list[dict],
    depth_mode: str = "professional",
) -> list[dict]:
    """Generate an outline from project_profile, scaled by depth mode.

    Args:
        profile: The project_profile dictionary with confirmed fields.
        features: List of selected feature dicts.
        depth_mode: Build depth mode controlling section count.

    Returns:
        List of section dicts (count varies by depth mode).
    """
    default_sections = get_sections_for_depth(depth_mode)
    section_count = len(default_sections)

    # Extract selected values from profile fields
    fields = {}
    for field_name in ["problem_definition", "target_user", "value_proposition",
                       "deployment_type", "ai_depth", "monetization_model", "mvp_scope"]:
        field_data = profile.get(field_name, {})
        fields[field_name] = field_data.get("selected", "") or ""

    if not any(fields.values()):
        return [dict(s) for s in default_sections]

    if not is_available():
        return [dict(s) for s in default_sections]

    feature_list = "\n".join(
        f"- {f['name']}: {f.get('description', '')}" for f in features
    ) or "- No specific features selected"

    # Build intelligence goals section (simple list for outline)
    intelligence_goals = profile.get("intelligence_goals", [])
    if intelligence_goals:
        intelligence_goals_section = "\n".join(
            f"- {g.get('user_facing_label') or g.get('label', 'Goal')}: "
            f"{g.get('description', '')}"
            for g in intelligence_goals
        )
    else:
        intelligence_goals_section = "No intelligence goals for this project."

    # Build section list for prompt
    section_list = "\n".join(
        f"{s['index']}. {s['title']}" for s in default_sections
    )

    # Build derived field strings
    tc = profile.get("technical_constraints", [])
    nfrs = profile.get("non_functional_requirements", [])
    sm = profile.get("success_metrics", [])
    risks = profile.get("risk_assessment", [])
    ucs = profile.get("core_use_cases", [])

    try:
        prompt = OUTLINE_FROM_PROFILE_PROMPT.format(
            **fields,
            section_count=section_count,
            section_list=section_list,
            technical_constraints=", ".join(tc) if tc else "None specified",
            nfrs=", ".join(nfrs) if nfrs else "None specified",
            success_metrics=", ".join(sm) if sm else "None specified",
            risks=", ".join(risks) if risks else "None specified",
            use_cases=", ".join(ucs) if ucs else "None specified",
            feature_list=feature_list,
            intelligence_goals_section=intelligence_goals_section,
        )
        response = chat(
            system_prompt=OUTLINE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=4096,
            temperature=0.7,
            response_format={"type": "json_object"},
        )
        return _parse_enhanced_outline_response(response.content, default_sections)
    except (LLMUnavailableError, LLMClientError) as e:
        logger.warning("LLM enhanced outline generation failed: %s. Using defaults.", e)
        return [dict(s) for s in default_sections]
    except Exception as e:
        logger.warning("Unexpected error generating enhanced outline: %s. Using defaults.", e)
        return [dict(s) for s in default_sections]


def _parse_enhanced_outline_response(
    raw_json: str,
    fallback_sections: list[dict] | None = None,
) -> list[dict]:
    """Parse LLM JSON into sections list. Falls back on failure.

    Args:
        raw_json: The raw JSON string from the LLM.
        fallback_sections: Section templates to use on failure (defaults to ENHANCED_SECTIONS).

    Returns:
        List of section dicts.
    """
    if fallback_sections is None:
        fallback_sections = ENHANCED_SECTIONS
    expected_count = len(fallback_sections)

    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return [dict(s) for s in fallback_sections]

    sections = data.get("sections", [])
    if not isinstance(sections, list) or len(sections) < expected_count:
        return [dict(s) for s in fallback_sections]

    result = []
    for i, sec in enumerate(sections[:expected_count]):
        result.append({
            "index": i + 1,
            "title": sec.get("title", fallback_sections[i]["title"]),
            "type": "required",
            "summary": sec.get("summary", ""),
        })
    return result


def _parse_outline_response(raw_json: str) -> list[dict]:
    """Parse LLM JSON into sections list. Falls back on failure."""
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return [dict(s) for s in DEFAULT_SECTIONS]

    sections = data.get("sections", [])
    if not isinstance(sections, list) or len(sections) < 7:
        return [dict(s) for s in DEFAULT_SECTIONS]

    result = []
    for i, sec in enumerate(sections[:7]):
        result.append({
            "index": i + 1,
            "title": sec.get("title", DEFAULT_SECTIONS[i]["title"]),
            "type": "required",
            "summary": sec.get("summary", ""),
        })
    return result
