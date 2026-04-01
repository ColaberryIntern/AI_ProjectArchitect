"""AI maturity scoring engine for advisory sessions.

Fully deterministic — no LLM. Scores businesses across 5 dimensions
using keyword/pattern matching on question answers.

Each dimension is scored 1-5:
  1 = Nascent (no readiness)
  2 = Emerging (early awareness)
  3 = Developing (some capability)
  4 = Advanced (strong capability)
  5 = Leading (best-in-class)
"""

import re
from datetime import datetime, timezone


DIMENSIONS = [
    "data_readiness",
    "process_maturity",
    "tech_infrastructure",
    "team_capability",
    "strategic_alignment",
]

# Dimension weights for overall score
DIMENSION_WEIGHTS = {
    "data_readiness": 0.25,
    "process_maturity": 0.20,
    "tech_infrastructure": 0.25,
    "team_capability": 0.15,
    "strategic_alignment": 0.15,
}

# Keywords that increase score for each dimension
_SCORING_RULES = {
    "data_readiness": {
        "question_ids": ["q6_current_tools", "q7_data_systems"],
        "high_indicators": [
            "data warehouse", "analytics", "dashboard", "bi tool", "tableau",
            "power bi", "looker", "bigquery", "snowflake", "data lake",
            "real-time", "machine learning", "ml", "predictive",
        ],
        "medium_indicators": [
            "spreadsheet", "excel", "google sheets", "reports", "kpi",
            "metrics", "tracking", "database", "sql", "crm data",
        ],
        "low_indicators": [
            "manual", "no data", "gut feel", "intuition", "paper",
            "don't track", "no metrics", "informal",
        ],
    },
    "process_maturity": {
        "question_ids": ["q4_bottlenecks", "q8_manual_processes"],
        "high_indicators": [
            "automated", "workflow engine", "standardized", "sop",
            "process documentation", "lean", "six sigma", "optimized",
            "integrated", "api", "pipeline",
        ],
        "medium_indicators": [
            "some automation", "partially automated", "templates",
            "defined process", "checklists", "project management",
        ],
        "low_indicators": [
            "ad hoc", "manual", "no process", "chaotic", "inconsistent",
            "different every time", "depends on person", "tribal knowledge",
        ],
    },
    "tech_infrastructure": {
        "question_ids": ["q6_current_tools"],
        "high_indicators": [
            "cloud", "aws", "azure", "gcp", "kubernetes", "docker",
            "microservices", "api", "ci/cd", "devops", "terraform",
            "serverless", "saas", "modern stack",
        ],
        "medium_indicators": [
            "hybrid", "some cloud", "virtual machines", "hosted",
            "web application", "python", "javascript", "node",
            "react", "sql server", "postgresql",
        ],
        "low_indicators": [
            "on-premise", "legacy", "mainframe", "no cloud",
            "desktop only", "standalone", "paper-based", "none",
        ],
    },
    "team_capability": {
        "question_ids": ["q10_success_vision", "q4_bottlenecks"],
        "high_indicators": [
            "data team", "ai team", "ml engineers", "data scientists",
            "technical team", "innovation lab", "r&d", "pilot project",
            "experimented", "proof of concept",
        ],
        "medium_indicators": [
            "interested", "learning", "training", "hired consultant",
            "evaluating", "some experience", "vendor support",
        ],
        "low_indicators": [
            "no expertise", "no experience", "don't understand",
            "fear", "resistance", "no budget", "trust issues",
            "skeptical", "never tried", "don't know where to start",
        ],
    },
    "strategic_alignment": {
        "question_ids": ["q10_success_vision", "q9_budget_timeline"],
        "high_indicators": [
            "ai strategy", "digital transformation", "competitive advantage",
            "market leader", "innovation", "disrupt", "scale",
            "10x", "automate everything", "ai-first",
        ],
        "medium_indicators": [
            "efficiency", "cost reduction", "improve", "better",
            "competitors using ai", "keeping up", "modernize",
        ],
        "low_indicators": [
            "not sure", "no plan", "unclear", "don't know",
            "survival", "just exploring", "maybe",
        ],
    },
}


def score_maturity(answers: list[dict], capability_map: dict | None = None) -> dict:
    """Score AI maturity across 5 dimensions.

    Args:
        answers: List of answer dicts from the advisory session.
        capability_map: Optional capability map (currently unused but available
                       for future scoring enhancements).

    Returns:
        Dict with overall score, per-dimension scores, and timestamp.
    """
    answer_map = {a["question_id"]: a.get("answer_text", "") for a in answers}

    dimensions = {}
    for dim in DIMENSIONS:
        dimensions[dim] = _score_dimension(dim, answer_map)

    overall = sum(
        dimensions[dim] * DIMENSION_WEIGHTS[dim]
        for dim in DIMENSIONS
    )

    return {
        "overall": round(overall, 1),
        "dimensions": dimensions,
        "scored_at": datetime.now(timezone.utc).isoformat(),
    }


def _score_dimension(dimension: str, answer_map: dict[str, str]) -> int:
    """Score a single dimension (1-5) based on keyword matching.

    Looks at answers to relevant questions and counts indicator matches.
    """
    rules = _SCORING_RULES[dimension]
    relevant_text = " ".join(
        answer_map.get(qid, "") for qid in rules["question_ids"]
    ).lower()

    if not relevant_text.strip():
        return 1

    high_count = _count_matches(relevant_text, rules["high_indicators"])
    medium_count = _count_matches(relevant_text, rules["medium_indicators"])
    low_count = _count_matches(relevant_text, rules["low_indicators"])

    # Scoring logic: high indicators boost score, low indicators reduce it
    if high_count >= 3:
        return 5
    elif high_count >= 1 and medium_count >= 1:
        return 4
    elif medium_count >= 2:
        return 3
    elif medium_count >= 1 or high_count >= 1:
        return 3 if low_count == 0 else 2
    elif low_count >= 2:
        return 1
    else:
        return 2  # Default: limited info suggests emerging stage


def _count_matches(text: str, indicators: list[str]) -> int:
    """Count how many indicator phrases appear in the text."""
    count = 0
    for indicator in indicators:
        if indicator in text:
            count += 1
    return count


def get_maturity_label(score: float) -> str:
    """Convert a numeric score to a human-readable label."""
    if score >= 4.5:
        return "Leading"
    elif score >= 3.5:
        return "Advanced"
    elif score >= 2.5:
        return "Developing"
    elif score >= 1.5:
        return "Emerging"
    else:
        return "Nascent"


def get_dimension_label(dimension: str) -> str:
    """Return a human-friendly label for a dimension ID."""
    labels = {
        "data_readiness": "Data Readiness",
        "process_maturity": "Process Maturity",
        "tech_infrastructure": "Technology Infrastructure",
        "team_capability": "Team AI Capability",
        "strategic_alignment": "Strategic Alignment",
    }
    return labels.get(dimension, dimension.replace("_", " ").title())
