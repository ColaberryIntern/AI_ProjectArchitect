"""Ambiguity detection for project documentation.

Detects vague language patterns including vague nouns, undefined users,
missing criteria, overloaded goals, and forbidden vagueness phrases.
All detection is regex-based and deterministic.
"""

import re


# Vague nouns that need specificity
VAGUE_NOUNS = [
    r"\bplatform\b",
    r"\btool\b",
    r"\bsolution\b",
    r"\bsystem\b",
    r"\bframework\b",
    r"\binfrastructure\b",
    r"\bservice\b",
    r"\bmodule\b",
    r"\bcomponent\b",
    r"\blayer\b",
]

# Undefined user references
UNDEFINED_USERS = [
    r"\bbusinesses\b",
    r"\bpeople\b",
    r"\bteams\b",
    r"\bstakeholders\b",
    r"\beveryone\b",
    r"\busers\b",
    r"\bcustomers\b",
    r"\bclients\b",
]

# Overloaded goal signals
OVERLOADED_GOALS = [
    r"\bend.to.end\b",
    r"\bdo everything\b",
    r"\bfull.?stack\b",
    r"\ball.in.one\b",
    r"\bcomprehensive\b",
    r"\bcomplete solution\b",
    r"\bone.stop\b",
]

# Forbidden vagueness phrases (from Quality Gates spec)
FORBIDDEN_PHRASES = [
    r"handle edge cases",
    r"optimize later",
    r"make it scalable",
    r"ensure good ux",
    r"use best practices",
    r"as needed",
    r"where applicable",
    r"and so on",
    r"et cetera",
    r"etc\.",
    r"and more",
    r"various",
    r"appropriate",
    r"suitable",
    r"adequate",
    r"sufficient",
    r"properly",
    r"correctly",
    r"efficiently",
]


def detect_vague_nouns(text: str) -> list[dict]:
    """Find vague nouns used without context.

    Args:
        text: The text to scan.

    Returns:
        List of dicts with 'term', 'position', and 'suggestion'.
    """
    findings = []
    for pattern in VAGUE_NOUNS:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            findings.append(
                {
                    "term": match.group(),
                    "position": match.start(),
                    "suggestion": f"Specify what '{match.group()}' refers to concretely",
                }
            )
    return findings


def detect_undefined_users(text: str) -> list[dict]:
    """Find generic user references without specificity.

    Args:
        text: The text to scan.

    Returns:
        List of dicts with 'term', 'position', and 'suggestion'.
    """
    findings = []
    for pattern in UNDEFINED_USERS:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            findings.append(
                {
                    "term": match.group(),
                    "position": match.start(),
                    "suggestion": f"Define who '{match.group()}' are specifically (role, context, skill level)",
                }
            )
    return findings


def detect_overloaded_goals(text: str) -> list[dict]:
    """Flag phrases that indicate overly broad scope.

    Args:
        text: The text to scan.

    Returns:
        List of dicts with 'term', 'position', and 'suggestion'.
    """
    findings = []
    for pattern in OVERLOADED_GOALS:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            findings.append(
                {
                    "term": match.group(),
                    "position": match.start(),
                    "suggestion": f"Break '{match.group()}' into specific, bounded goals",
                }
            )
    return findings


def detect_forbidden_phrases(text: str) -> list[dict]:
    """Detect forbidden vagueness phrases that must be replaced.

    Args:
        text: The text to scan.

    Returns:
        List of dicts with 'phrase', 'position', and 'required_replacement'.
    """
    findings = []
    for pattern in FORBIDDEN_PHRASES:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            findings.append(
                {
                    "phrase": match.group(),
                    "position": match.start(),
                    "required_replacement": (
                        "Replace with specific behaviors, explicit constraints, "
                        "or measurable outcomes"
                    ),
                }
            )
    return findings


def detect_missing_criteria(text: str) -> dict:
    """Check for presence of success criteria indicators.

    Args:
        text: The text to scan.

    Returns:
        Dict with 'has_criteria' bool and 'suggestion'.
    """
    criteria_signals = [
        r"success\s+(is|means|criteria|metric)",
        r"done\s+when",
        r"complet(e|ed|ion)\s+when",
        r"measur(e|ed|able)",
        r"criteria",
        r"metric",
        r"pass(es)?\s+when",
    ]
    for pattern in criteria_signals:
        if re.search(pattern, text, re.IGNORECASE):
            return {"has_criteria": True, "suggestion": None}

    return {
        "has_criteria": False,
        "suggestion": "Add explicit success criteria or completion conditions",
    }


def run_all_detectors(text: str) -> dict:
    """Run all ambiguity detectors and return a structured report.

    Args:
        text: The text to scan.

    Returns:
        Dict with per-detector results and overall 'total_findings' count.
    """
    vague_nouns = detect_vague_nouns(text)
    undefined_users = detect_undefined_users(text)
    overloaded_goals = detect_overloaded_goals(text)
    forbidden_phrases = detect_forbidden_phrases(text)
    missing_criteria = detect_missing_criteria(text)

    total = (
        len(vague_nouns)
        + len(undefined_users)
        + len(overloaded_goals)
        + len(forbidden_phrases)
        + (0 if missing_criteria["has_criteria"] else 1)
    )

    return {
        "vague_nouns": vague_nouns,
        "undefined_users": undefined_users,
        "overloaded_goals": overloaded_goals,
        "forbidden_phrases": forbidden_phrases,
        "missing_criteria": missing_criteria,
        "total_findings": total,
        "has_issues": total > 0,
    }
