"""Smart auto-selection engine for features and skills.

Analyzes a project's idea/profile to select ALL genuinely applicable
features and skills — no hardcoded min/max limits.  Uses LLM when
available, falls back to deterministic keyword scoring (threshold >= 2).
"""

import json
import logging

from execution.llm_client import LLMClientError, LLMUnavailableError, chat, is_available

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LLM prompts
# ---------------------------------------------------------------------------

FEATURE_SELECT_SYSTEM = (
    "You are a senior product architect. Given a project profile and a "
    "feature catalog, select every feature that genuinely applies to the "
    "project. Do not pad — only include features that add real value."
)

FEATURE_SELECT_USER = """Project profile:
Problem: {problem_definition}
Target User: {target_user}
Value Proposition: {value_proposition}
Deployment: {deployment_type}
AI Depth: {ai_depth}
Monetization: {monetization_model}
MVP Scope: {mvp_scope}

Project idea:
{idea}

Feature catalog (id → name: description):
{feature_list}

Select ALL features that genuinely apply to this project.
Return ONLY a JSON object: {{"selected": ["feature_id_1", "feature_id_2", ...]}}
Rules:
- Include every feature that adds real value to this specific project
- Do NOT pad with irrelevant features
- No minimum or maximum — return however many truly apply
- Return ONLY the JSON object, no markdown"""

SKILL_SELECT_SYSTEM = (
    "You are a Claude-tools expert. Given a project profile and its "
    "selected features, choose every skill/tool from the registry that "
    "genuinely applies. Do not pad — only include skills that add real value."
)

SKILL_SELECT_USER = """Project profile:
Problem: {problem_definition}
Target User: {target_user}
Value Proposition: {value_proposition}
Deployment: {deployment_type}
AI Depth: {ai_depth}

Selected features:
{feature_list}

Skill registry (id → name: description):
{skill_list}

Select ALL skills that genuinely apply to this project.
Return ONLY a JSON object: {{"selected": ["skill_id_1", "skill_id_2", ...]}}
Rules:
- Include every skill that adds real value given the project and its features
- Do NOT pad with irrelevant skills
- No minimum or maximum — return however many truly apply
- Return ONLY the JSON object, no markdown"""

# ---------------------------------------------------------------------------
# Keyword scoring threshold for deterministic fallback
# ---------------------------------------------------------------------------

KEYWORD_THRESHOLD = 2


# ---------------------------------------------------------------------------
# Feature selection
# ---------------------------------------------------------------------------


def smart_select_features(
    profile: dict,
    idea: str,
    catalog: list[dict],
) -> list[str]:
    """Select all genuinely applicable features using LLM with keyword fallback.

    Args:
        profile: The project_profile dictionary with confirmed fields.
        idea: The raw project idea text.
        catalog: Full feature catalog list.

    Returns:
        List of selected feature IDs.
    """
    if not catalog:
        return []

    # Extract profile fields
    fields = _extract_profile_fields(profile)

    # Try LLM
    if is_available() and any(fields.values()):
        try:
            feature_list = "\n".join(
                f"- {f['id']}: {f['name']} — {f.get('description', '')}"
                for f in catalog
            )
            prompt = FEATURE_SELECT_USER.format(
                **fields,
                idea=idea or "(no idea text)",
                feature_list=feature_list,
            )
            response = chat(
                system_prompt=FEATURE_SELECT_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                response_format={"type": "json_object"},
            )
            result = _parse_id_list(response.content, {f["id"] for f in catalog})
            if len(result) >= 5:
                logger.info("LLM smart-selected %d features", len(result))
                return result
        except (LLMUnavailableError, LLMClientError) as e:
            logger.warning("LLM feature selection failed: %s. Using keyword fallback.", e)
        except Exception as e:
            logger.warning("Unexpected error in feature selection: %s. Using keyword fallback.", e)

    # Deterministic fallback
    return _select_features_by_keywords(profile, idea, catalog)


def _select_features_by_keywords(
    profile: dict,
    idea: str,
    catalog: list[dict],
) -> list[str]:
    """Score each feature by keyword overlap; select all with score >= threshold."""
    keywords = _build_keyword_set(profile, idea)
    if not keywords:
        # No keywords to match — select all (let the user trim)
        return [f["id"] for f in catalog]

    selected = []
    for feat in catalog:
        score = _score_item(feat, keywords)
        if score >= KEYWORD_THRESHOLD:
            selected.append(feat["id"])

    # If threshold is too strict and we get very few, relax to >= 1
    if len(selected) < 10 and len(catalog) > 15:
        selected = [f["id"] for f in catalog if _score_item(f, keywords) >= 1]

    return selected


# ---------------------------------------------------------------------------
# Skill selection
# ---------------------------------------------------------------------------


def smart_select_skills(
    profile: dict,
    features: list[dict],
    registry: list[dict],
) -> list[str]:
    """Select all genuinely applicable skills using LLM with keyword fallback.

    Args:
        profile: The project_profile dictionary.
        features: List of selected feature dicts.
        registry: Full skill registry list.

    Returns:
        List of selected skill IDs.
    """
    if not registry:
        return []

    fields = _extract_profile_fields(profile)

    if is_available() and any(fields.values()):
        try:
            feature_list = "\n".join(
                f"- {f['name']}: {f.get('description', '')}"
                for f in features[:40]
            ) or "- No features selected"

            skill_list = "\n".join(
                f"- {s['id']}: {s['name']} — {s.get('description', '')}"
                for s in registry
            )

            prompt = SKILL_SELECT_USER.format(
                **fields,
                feature_list=feature_list,
                skill_list=skill_list,
            )
            response = chat(
                system_prompt=SKILL_SELECT_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                response_format={"type": "json_object"},
            )
            result = _parse_id_list(response.content, {s["id"] for s in registry})
            if len(result) >= 3:
                logger.info("LLM smart-selected %d skills", len(result))
                return result
        except (LLMUnavailableError, LLMClientError) as e:
            logger.warning("LLM skill selection failed: %s. Using keyword fallback.", e)
        except Exception as e:
            logger.warning("Unexpected error in skill selection: %s. Using keyword fallback.", e)

    return _select_skills_by_keywords(profile, features, registry)


def _select_skills_by_keywords(
    profile: dict,
    features: list[dict],
    registry: list[dict],
) -> list[str]:
    """Score each skill by keyword overlap; select all with score >= threshold."""
    keywords = _build_keyword_set(profile, "")
    # Add feature keywords
    for feat in features:
        keywords.update(w.lower() for w in feat.get("name", "").split() if len(w) > 2)
        keywords.update(w.lower() for w in feat.get("description", "").split() if len(w) > 2)

    if not keywords:
        return []

    selected = []
    for skill in registry:
        score = _score_item(skill, keywords, use_tags=True)
        if score >= KEYWORD_THRESHOLD:
            selected.append(skill["id"])

    return selected


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _extract_profile_fields(profile: dict) -> dict:
    """Extract selected values from profile fields."""
    fields = {}
    for field_name in [
        "problem_definition", "target_user", "value_proposition",
        "deployment_type", "ai_depth", "monetization_model", "mvp_scope",
    ]:
        field_data = profile.get(field_name, {})
        fields[field_name] = field_data.get("selected", "") or ""
    return fields


def _build_keyword_set(profile: dict, idea: str) -> set[str]:
    """Build a set of lowercase keywords from profile and idea text."""
    keywords: set[str] = set()
    for field_name in [
        "problem_definition", "target_user", "value_proposition",
        "deployment_type", "ai_depth", "monetization_model", "mvp_scope",
    ]:
        field_data = profile.get(field_name, {})
        selected = field_data.get("selected", "") or ""
        keywords.update(w.lower() for w in selected.split() if len(w) > 2)

    if idea:
        keywords.update(w.lower() for w in idea.split() if len(w) > 2)

    return keywords


def _score_item(item: dict, keywords: set[str], use_tags: bool = False) -> int:
    """Score an item (feature or skill) against keywords."""
    name_words = [w.lower() for w in item.get("name", "").split()]
    desc_words = [w.lower() for w in item.get("description", "").split()]
    all_terms = name_words + desc_words

    if use_tags:
        all_terms += item.get("tags", [])

    return sum(1 for term in all_terms if term in keywords)


def _parse_id_list(raw_json: str, valid_ids: set[str]) -> list[str]:
    """Parse LLM JSON response containing a 'selected' list of IDs."""
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return []

    selected = data.get("selected", [])
    if not isinstance(selected, list):
        return []

    return [sid for sid in selected if isinstance(sid, str) and sid in valid_ids]
