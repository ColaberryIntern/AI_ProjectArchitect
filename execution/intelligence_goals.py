"""Intelligence Goals: project-specific AI behavior goal generation.

Generates dynamic AI/ML intelligence goals based on project idea, selected
features, and AI depth. Goals describe what the system should predict,
classify, recommend, or optimize.

Pure deterministic logic for trigger detection and fallback. LLM call
for dynamic generation when triggered.

Goal dicts use canonical field names:
  id, user_facing_label, description, goal_type, confidence_required, impact_level
"""

import json
import logging
import re

from execution.llm_client import LLMClientError, LLMUnavailableError, chat, is_available

logger = logging.getLogger(__name__)

# Goal types that the system can generate
GOAL_TYPES = [
    "prediction",
    "classification",
    "anomaly_detection",
    "recommendation",
    "optimization",
    "nlp_analysis",
    "forecasting",
    "adaptive_system",
]

# Confidence levels for prediction-type goals
CONFIDENCE_LEVELS = [
    {"value": "informational", "label": "Informational estimate", "description": "Advisory only, no business decisions depend on accuracy"},
    {"value": "business_reliable", "label": "Business reliable", "description": "Used for business decisions, ~80% accuracy expected"},
    {"value": "high_confidence", "label": "High confidence", "description": "Critical decisions depend on this, ~95% accuracy expected"},
    {"value": "critical_accuracy", "label": "Critical accuracy", "description": "Safety or compliance critical, >99% accuracy required"},
]

# Only these goal types show the confidence dropdown
CONFIDENCE_GOAL_TYPES = {"prediction", "forecasting", "classification"}

# Regex patterns that indicate AI/ML relevance in project idea or features
AI_TRIGGER_KEYWORDS = [
    r"\bai\b",
    r"\bartificial intelligence\b",
    r"\bmachine learning\b",
    r"\bml\b",
    r"\bprediction\b",
    r"\bpredict\b",
    r"\brecommendation\b",
    r"\brecommend\b",
    r"\bclassification\b",
    r"\bclassify\b",
    r"\banomaly detection\b",
    r"\bnatural language\b",
    r"\bnlp\b",
    r"\bsentiment\b",
    r"\bforecast\b",
    r"\boptimization\b",
    r"\boptimize\b",
    r"\badaptive\b",
    r"\bintelligent\b",
    r"\bautonomous\b",
    r"\bneural\b",
    r"\bdeep learning\b",
    r"\bcomputer vision\b",
    r"\bimage recognition\b",
    r"\bchatbot\b",
    r"\bconversational\b",
    r"\bpersonaliz",
    r"\bautomation\b",
    r"\banalysis\b",
]

# AI depth values that trigger the intelligence goals section
AI_DEPTH_TRIGGERS = {"predictive_ml", "autonomous_ai", "ai_assisted"}

# ---------------------------------------------------------------------------
# Goal-Type Architecture Rules
# ---------------------------------------------------------------------------
# Maps each goal_type to the architectural considerations injected into
# chapter prompts. Used by build_intelligence_goals_prompt_section().

GOAL_TYPE_ARCHITECTURE_RULES = {
    "prediction": {
        "functional_requirements": (
            "Define prediction inputs, outputs, accuracy thresholds, and feedback mechanisms. "
            "Specify what data is required, how predictions are surfaced to users, "
            "and how prediction accuracy is measured over time."
        ),
        "architecture_sections": [
            "Retraining cadence",
            "Model monitoring",
            "Drift detection",
            "Evaluation metrics",
        ],
        "high_confidence_additions": [
            "Shadow scoring pipeline",
            "A/B model comparison",
            "Automated retraining triggers",
        ],
    },
    "classification": {
        "functional_requirements": (
            "Define classification categories, confidence thresholds, and human review triggers. "
            "Specify the taxonomy of categories, how low-confidence classifications are handled, "
            "and how the system improves classification accuracy."
        ),
        "architecture_sections": [
            "Category taxonomy",
            "Confidence scoring",
            "Human-in-the-loop review",
            "Model evaluation",
        ],
        "high_confidence_additions": [
            "Multi-model consensus",
            "Automated quality sampling",
            "Category drift monitoring",
        ],
    },
    "anomaly_detection": {
        "functional_requirements": (
            "Define normal baselines, anomaly thresholds, and alert escalation paths. "
            "Specify what constitutes normal behavior, how thresholds are calibrated, "
            "and what actions are triggered when anomalies are detected."
        ),
        "architecture_sections": [
            "Threshold management",
            "Alert escalation logic",
            "False positive handling",
            "Baseline calibration",
        ],
    },
    "recommendation": {
        "functional_requirements": (
            "Define recommendation context, ranking criteria, and personalization signals. "
            "Specify what data drives recommendations, how items are ranked, "
            "and how the system handles cold-start scenarios for new users."
        ),
        "architecture_sections": [
            "Ranking logic",
            "Feedback loop",
            "Personalization engine",
            "Cold-start strategy",
        ],
    },
    "optimization": {
        "functional_requirements": (
            "Define optimization objectives, constraints, and acceptable trade-offs. "
            "Specify what is being optimized, what constraints must be respected, "
            "and how optimization results are evaluated and applied."
        ),
        "architecture_sections": [
            "Objective function definition",
            "Constraint handling",
            "Solution evaluation",
            "Optimization loop design",
        ],
    },
    "nlp_analysis": {
        "functional_requirements": (
            "Define text inputs, extraction targets, and language handling requirements. "
            "Specify supported languages, text preprocessing steps, "
            "and how extracted information is structured and used."
        ),
        "architecture_sections": [
            "Text preprocessing pipeline",
            "Entity extraction",
            "Language model selection",
            "Output formatting",
        ],
    },
    "forecasting": {
        "functional_requirements": (
            "Define forecast horizons, confidence intervals, and update frequency. "
            "Specify the time periods being forecasted, how uncertainty is communicated, "
            "and how forecasts are validated against actual outcomes."
        ),
        "architecture_sections": [
            "Time series pipeline",
            "Seasonality handling",
            "Forecast accuracy tracking",
            "Data freshness requirements",
        ],
        "high_confidence_additions": [
            "Ensemble methods",
            "Backtesting framework",
            "Confidence interval calibration",
        ],
    },
    "adaptive_system": {
        "functional_requirements": (
            "Define adaptation triggers, learning signals, and behavior boundaries. "
            "Specify what user actions drive adaptation, how the system changes over time, "
            "and what safety rails prevent harmful adaptations."
        ),
        "architecture_sections": [
            "Behavior tracking",
            "Dynamic update logic",
            "Learning rate controls",
            "Rollback mechanisms",
        ],
    },
}

GOALS_SYSTEM_PROMPT = (
    "You are an AI architect defining intelligence goals for a software project. "
    "Generate specific, measurable AI behavior goals that describe what the system "
    "should predict, classify, recommend, detect, or optimize."
)

GOALS_USER_PROMPT = """Based on this project, generate 4-8 intelligence goals:

**Project Idea:** {idea}

**AI Depth Level:** {ai_depth}

**Selected Features:**
{feature_list}

Return ONLY valid JSON:
{{"goals": [
  {{
    "id": "goal_1",
    "user_facing_label": "Short descriptive label (5-10 words)",
    "description": "What this goal achieves and why it matters (1-2 sentences)",
    "goal_type": "{goal_types_hint}"
  }},
  ...
]}}

Rules:
- Generate 4-8 goals
- Each goal must have a unique id (goal_1, goal_2, etc.)
- goal_type must be one of: {goal_types}
- Goals should be specific to THIS project, not generic
- Labels should be concise, actionable, and use domain-specific language
- Description should explain measurable outcomes
- Do NOT use generic phrases like "Predict numeric values" — be specific to the project domain
- Return ONLY the JSON object"""


def should_show_intelligence_goals(
    idea: str,
    features: list[dict],
    ai_depth: str,
) -> bool:
    """Determine if the Intelligence Goals section should be shown.

    Returns True if any of these conditions are met:
    1. AI depth is in AI_DEPTH_TRIGGERS
    2. Project idea matches AI_TRIGGER_KEYWORDS
    3. Any selected feature matches AI_TRIGGER_KEYWORDS

    Args:
        idea: The original project idea text.
        features: List of selected feature dicts.
        ai_depth: The ai_depth profile field value.

    Returns:
        True if intelligence goals should be displayed.
    """
    # Check AI depth
    if ai_depth in AI_DEPTH_TRIGGERS:
        return True

    # Check idea text
    idea_lower = (idea or "").lower()
    if any(re.search(pat, idea_lower) for pat in AI_TRIGGER_KEYWORDS):
        return True

    # Check features
    for feat in features:
        text = f"{feat.get('name', '')} {feat.get('description', '')}".lower()
        if any(re.search(pat, text) for pat in AI_TRIGGER_KEYWORDS):
            return True

    return False


def generate_intelligence_goals(
    idea: str,
    features: list[dict],
    ai_depth: str,
) -> list[dict]:
    """Generate project-specific intelligence goals via LLM.

    Args:
        idea: The original project idea text.
        features: List of selected feature dicts.
        ai_depth: The ai_depth profile field value.

    Returns:
        List of goal dicts with canonical field names.
        Falls back to keyword-matching if LLM unavailable.
    """
    if not is_available():
        logger.info("LLM unavailable, using fallback intelligence goals")
        return _fallback_goals(idea, features)

    feature_list = "\n".join(
        f"- {f['name']}: {f.get('description', '')}" for f in features
    ) or "- No specific features selected"

    prompt = GOALS_USER_PROMPT.format(
        idea=idea or "Not specified",
        ai_depth=ai_depth or "Not specified",
        feature_list=feature_list,
        goal_types=", ".join(GOAL_TYPES),
        goal_types_hint="prediction|classification|anomaly_detection|recommendation|optimization|nlp_analysis|forecasting|adaptive_system",
    )

    try:
        response = chat(
            system_prompt=GOALS_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2048,
            temperature=0.7,
            response_format={"type": "json_object"},
        )
        return _parse_goals_response(response.content, idea, features)
    except (LLMUnavailableError, LLMClientError) as e:
        logger.warning("LLM intelligence goals generation failed: %s. Using fallback.", e)
        return _fallback_goals(idea, features)
    except Exception as e:
        logger.warning("Unexpected error generating intelligence goals: %s. Using fallback.", e)
        return _fallback_goals(idea, features)


def _parse_goals_response(
    raw_json: str,
    idea: str,
    features: list[dict],
) -> list[dict]:
    """Parse and validate LLM JSON response for goals.

    Args:
        raw_json: Raw JSON string from LLM.
        idea: Fallback idea text.
        features: Fallback features.

    Returns:
        List of validated goal dicts with canonical field names.
    """
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse intelligence goals JSON, using fallback")
        return _fallback_goals(idea, features)

    goals = data.get("goals", [])
    if not isinstance(goals, list) or len(goals) < 4:
        logger.warning("Intelligence goals JSON has < 4 goals, using fallback")
        return _fallback_goals(idea, features)

    validated = []
    for i, goal in enumerate(goals[:8]):
        if not isinstance(goal, dict):
            continue
        # Accept both old and new field names from LLM response
        goal_type = goal.get("goal_type") or goal.get("type", "recommendation")
        if goal_type not in GOAL_TYPES:
            goal_type = "recommendation"
        validated.append({
            "id": goal.get("id", f"goal_{i + 1}"),
            "user_facing_label": (goal.get("user_facing_label") or goal.get("label", f"Intelligence Goal {i + 1}"))[:100],
            "description": goal.get("description", "")[:500],
            "goal_type": goal_type,
            "confidence_required": None,
            "impact_level": None,
        })

    if len(validated) < 4:
        return _fallback_goals(idea, features)

    return validated


def _fallback_goals(idea: str, features: list[dict]) -> list[dict]:
    """Generate deterministic fallback goals based on keyword matching.

    Always returns at least 4 goals.

    Args:
        idea: The project idea text.
        features: List of selected feature dicts.

    Returns:
        List of goal dicts with canonical field names.
    """
    idea_lower = (idea or "").lower()
    feature_text = " ".join(
        f"{f.get('name', '')} {f.get('description', '')}" for f in features
    ).lower()
    combined = f"{idea_lower} {feature_text}"

    goals = []

    # Prediction goals
    if re.search(r"\bpredict|\bforecast|\btrend", combined):
        goals.append({
            "id": "goal_predict",
            "user_facing_label": "Predictive analytics for key metrics",
            "description": "Use historical data to forecast trends and support proactive decision-making.",
            "goal_type": "prediction",
            "confidence_required": None,
            "impact_level": None,
        })

    # Recommendation goals
    if re.search(r"\brecommend|\bpersonaliz|\bsuggest", combined):
        goals.append({
            "id": "goal_recommend",
            "user_facing_label": "Personalized recommendations engine",
            "description": "Generate context-aware suggestions based on user behavior and preferences.",
            "goal_type": "recommendation",
            "confidence_required": None,
            "impact_level": None,
        })

    # Classification goals
    if re.search(r"\bclassif|\bcategoriz|\bsort|\blabel", combined):
        goals.append({
            "id": "goal_classify",
            "user_facing_label": "Automated content classification",
            "description": "Categorize incoming data automatically with configurable confidence thresholds.",
            "goal_type": "classification",
            "confidence_required": None,
            "impact_level": None,
        })

    # Anomaly detection goals
    if re.search(r"\banomaly|\bdetect|\bmonitor|\balert|\bfraud", combined):
        goals.append({
            "id": "goal_anomaly",
            "user_facing_label": "Anomaly detection and alerting",
            "description": "Identify unusual patterns in real-time and trigger appropriate alerts.",
            "goal_type": "anomaly_detection",
            "confidence_required": None,
            "impact_level": None,
        })

    # NLP goals
    if re.search(r"\bnlp|\bsentiment|\btext|\bchat|\bconversat|\blanguage", combined):
        goals.append({
            "id": "goal_nlp",
            "user_facing_label": "Natural language understanding",
            "description": "Process and understand user text input for extraction, sentiment, or conversation.",
            "goal_type": "nlp_analysis",
            "confidence_required": None,
            "impact_level": None,
        })

    # Optimization goals
    if re.search(r"\boptimiz|\befficien|\bschedul|\broute|\balloc", combined):
        goals.append({
            "id": "goal_optimize",
            "user_facing_label": "Resource optimization engine",
            "description": "Optimize allocation and scheduling to maximize efficiency and reduce cost.",
            "goal_type": "optimization",
            "confidence_required": None,
            "impact_level": None,
        })

    # Adaptive system goals
    if re.search(r"\badapt|\blearn|\bimprov|\bevolv", combined):
        goals.append({
            "id": "goal_adaptive",
            "user_facing_label": "Adaptive behavior learning",
            "description": "System learns from user interactions to improve over time automatically.",
            "goal_type": "adaptive_system",
            "confidence_required": None,
            "impact_level": None,
        })

    # Ensure at least 4 goals — pad with generic ones if needed
    generic_goals = [
        {
            "id": "goal_data_analysis",
            "user_facing_label": "Intelligent data analysis",
            "description": "Analyze project data to surface actionable insights and patterns.",
            "goal_type": "recommendation",
            "confidence_required": None,
            "impact_level": None,
        },
        {
            "id": "goal_quality_scoring",
            "user_facing_label": "Automated quality scoring",
            "description": "Score and rank content or outputs using configurable quality criteria.",
            "goal_type": "classification",
            "confidence_required": None,
            "impact_level": None,
        },
        {
            "id": "goal_usage_patterns",
            "user_facing_label": "Usage pattern recognition",
            "description": "Identify user behavior patterns to inform product and feature decisions.",
            "goal_type": "anomaly_detection",
            "confidence_required": None,
            "impact_level": None,
        },
        {
            "id": "goal_process_optimization",
            "user_facing_label": "Process workflow optimization",
            "description": "Continuously improve workflow efficiency based on throughput metrics.",
            "goal_type": "optimization",
            "confidence_required": None,
            "impact_level": None,
        },
    ]

    existing_ids = {g["id"] for g in goals}
    for generic in generic_goals:
        if len(goals) >= 4:
            break
        if generic["id"] not in existing_ids:
            goals.append(generic)

    return goals


def check_intelligence_goals_alignment(
    goals: list[dict],
    features: list[dict],
) -> dict:
    """Check if intelligence goals have supporting AI features selected.

    Advisory check — warns if goals are set but no AI-related features
    are selected. Does not block progression.

    Args:
        goals: List of intelligence goal dicts.
        features: List of selected feature dicts.

    Returns:
        Dict with 'passed' bool and 'warnings' list.
    """
    if not goals:
        return {"passed": True, "warnings": []}

    # Check for AI features
    ai_patterns = [
        r"\bai\b", r"\bml\b", r"\bmachine learning\b", r"\bneural\b",
        r"\bnlp\b", r"\bnatural language\b", r"\brecommendation\b",
        r"\badaptive\b", r"\bintelligent\b", r"\bpredictive\b",
        r"\bautonomous\b",
    ]

    ai_feature_count = 0
    for feat in features:
        text = f"{feat.get('name', '')} {feat.get('description', '')}".lower()
        if any(re.search(pat, text) for pat in ai_patterns):
            ai_feature_count += 1

    warnings = []
    if ai_feature_count == 0:
        warnings.append(
            "This project includes intelligent behavior goals but no AI "
            "capabilities are selected. Consider enabling relevant AI features."
        )

    return {
        "passed": len(warnings) == 0,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Prompt Section Builder — generates chapter-specific prompt text
# ---------------------------------------------------------------------------

HIGH_CONFIDENCE_VALUES = {"high_confidence", "critical_accuracy"}


def build_intelligence_goals_prompt_section(
    goals: list[dict],
    chapter_title: str,
    expansion_depth: str = "detailed",
) -> str:
    """Build chapter-specific prompt text from intelligence goals.

    For Chapter 4 (Functional Requirements): injects behavioral
    requirement guidance per goal type.

    For Chapter 5 (AI & Intelligence Architecture): injects architecture
    component lists per goal type, with additional depth for high-confidence
    goals.

    For all other chapters: outputs a brief summary list.

    The expansion_depth parameter controls how much detail is included:
    - "brief": Goal labels only (Light mode)
    - "standard": Labels + descriptions (Standard mode)
    - "detailed": Full current behavior with rules (Professional mode)
    - "comprehensive": Full rules with extra elaboration (Enterprise mode)

    Args:
        goals: List of intelligence goal dicts with canonical field names.
        chapter_title: The outline section title for this chapter.
        expansion_depth: Controls verbosity of prompt output.

    Returns:
        Formatted prompt text, or empty string if no goals.
    """
    if not goals:
        return ""

    title_lower = chapter_title.lower()

    if "functional requirement" in title_lower:
        return _build_functional_section(goals, expansion_depth)

    if "ai" in title_lower or "intelligence" in title_lower:
        return _build_architecture_section(goals, expansion_depth)

    # Default: brief summary for other chapters
    labels = [g.get("user_facing_label", g.get("label", "")) for g in goals]
    if expansion_depth == "brief":
        return "Intelligence Goals: " + ", ".join(labels[:3])
    return "Intelligence Goals: " + ", ".join(labels)


def _build_functional_section(goals: list[dict], expansion_depth: str = "detailed") -> str:
    """Build functional requirements prompt section for intelligence goals."""
    lines = []
    for goal in goals:
        label = goal.get("user_facing_label", goal.get("label", ""))
        goal_type = goal.get("goal_type", goal.get("type", "recommendation"))
        desc = goal.get("description", "")
        rules = GOAL_TYPE_ARCHITECTURE_RULES.get(goal_type, {})
        func_req = rules.get("functional_requirements", "")

        if expansion_depth == "brief":
            lines.append(f"- **{label}**")
        elif expansion_depth == "standard":
            lines.append(f"- **{label}**: {desc}")
        else:
            # detailed or comprehensive
            lines.append(f"- **{label}**: {desc}")
            if func_req:
                lines.append(f"  Behavioral requirement: {func_req}")

    if expansion_depth in ("detailed", "comprehensive"):
        lines.append("")
        lines.append(
            "Include detailed behavioral requirements for each intelligence goal "
            "listed above. Specify inputs, outputs, success criteria, and edge cases."
        )

    return "\n".join(lines)


def _build_architecture_section(goals: list[dict], expansion_depth: str = "detailed") -> str:
    """Build AI architecture prompt section for intelligence goals."""
    lines = []
    for goal in goals:
        label = goal.get("user_facing_label", goal.get("label", ""))
        goal_type = goal.get("goal_type", goal.get("type", "recommendation"))
        confidence = goal.get("confidence_required", goal.get("confidence_level"))
        rules = GOAL_TYPE_ARCHITECTURE_RULES.get(goal_type, {})

        if expansion_depth == "brief":
            lines.append(f"- **{label}** (type: {goal_type})")
        elif expansion_depth == "standard":
            lines.append(f"- **{label}** (type: {goal_type})")
            arch_sections = rules.get("architecture_sections", [])
            if arch_sections:
                lines.append(f"  Required architecture components: {', '.join(arch_sections)}")
        else:
            # detailed or comprehensive
            arch_sections = rules.get("architecture_sections", [])
            lines.append(f"- **{label}** (type: {goal_type})")
            if arch_sections:
                lines.append(f"  Required architecture components: {', '.join(arch_sections)}")

            # Add high-confidence additions if applicable
            if confidence in HIGH_CONFIDENCE_VALUES:
                additions = rules.get("high_confidence_additions", [])
                if additions:
                    lines.append(f"  High-confidence additions: {', '.join(additions)}")

    if expansion_depth in ("detailed", "comprehensive"):
        lines.append("")
        lines.append(
            "Infer the required AI architecture depth from these intelligence goals. "
            "Be specific about each component listed above. Include implementation "
            "details, data flow, and integration points."
        )

    return "\n".join(lines)
