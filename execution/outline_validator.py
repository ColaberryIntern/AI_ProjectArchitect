"""Outline-specific validation checks.

Validates outlines against the required structure: 7 or 10 mandatory sections
in correct order, proper naming conventions, and no placeholder content.

Auto-detects section count and applies appropriate validation rules.
"""

import re

# The 7 required section categories in strict order (legacy).
REQUIRED_SECTION_ORDER = [
    {"keywords": ["purpose", "context", "why"], "label": "Purpose & Context"},
    {"keywords": ["user", "role", "who"], "label": "Users & Roles"},
    {"keywords": ["capabilit", "feature", "what"], "label": "Core Capabilities"},
    {"keywords": ["non-goal", "exclusion", "not"], "label": "Non-Goals & Exclusions"},
    {"keywords": ["architecture", "flow", "how"], "label": "Architecture / Flow"},
    {"keywords": ["phase", "module", "execution", "build"], "label": "Execution Phases"},
    {"keywords": ["risk", "constraint", "assumption"], "label": "Risks & Constraints"},
]

# The 10 required section categories for enhanced outlines.
ENHANCED_SECTION_ORDER = [
    {"keywords": ["executive", "summary", "overview"], "label": "Executive Summary"},
    {"keywords": ["problem", "market", "context"], "label": "Problem & Market"},
    {"keywords": ["persona", "user", "use case"], "label": "User Personas"},
    {"keywords": ["functional", "requirement", "capabilit"], "label": "Functional Requirements"},
    {"keywords": ["ai", "intelligence", "ml"], "label": "AI Architecture"},
    {"keywords": ["non-functional", "performance", "scalab"], "label": "Non-Functional"},
    {"keywords": ["technical", "data model"], "label": "Technical Architecture"},
    {"keywords": ["security", "compliance", "privacy"], "label": "Security"},
    {"keywords": ["metric", "kpi", "success"], "label": "Success Metrics"},
    {"keywords": ["roadmap", "phase", "delivery"], "label": "Roadmap"},
]

# Patterns that indicate placeholder content
PLACEHOLDER_PATTERNS = [
    r"\bTBD\b",
    r"\bTBA\b",
    r"\bTBC\b",
    r"to be determined",
    r"to be decided",
    r"to be confirmed",
    r"we'll decide later",
    r"we'll figure out",
    r"figure out later",
    r"placeholder",
    r"TODO",
    r"FIXME",
    r"\bN/A\b",
    r"\.\.\.",
]

# Marketing/unprofessional naming patterns
BAD_NAMING_PATTERNS = [
    r"magic",
    r"secret sauce",
    r"stuff we",
    r"awesome",
    r"amazing",
    r"revolutionary",
    r"game.?changer",
    r"silver bullet",
    r"killer feature",
]


def _get_section_order(sections: list[dict]) -> list[dict]:
    """Auto-detect whether to use 7-section or 10-section validation."""
    if len(sections) >= 10:
        return ENHANCED_SECTION_ORDER
    return REQUIRED_SECTION_ORDER


def _matches_category(title: str, keywords: list[str]) -> bool:
    """Check if a section title matches a required category."""
    title_lower = title.lower()
    return any(kw in title_lower for kw in keywords)


def check_required_sections(sections: list[dict]) -> dict:
    """Verify all required sections are present (7 or 10 depending on count).

    Args:
        sections: List of outline section dicts with 'title' field.

    Returns:
        Dict with 'passed' bool, 'missing' list of missing categories,
        and 'matched' list of matched categories.
    """
    section_order = _get_section_order(sections)
    titles = [s["title"] for s in sections]
    matched = []
    missing = []

    for category in section_order:
        found = any(_matches_category(t, category["keywords"]) for t in titles)
        if found:
            matched.append(category["label"])
        else:
            missing.append(category["label"])

    return {
        "passed": len(missing) == 0,
        "missing": missing,
        "matched": matched,
    }


def check_section_order(sections: list[dict]) -> dict:
    """Verify sections follow the required conceptual order.

    Args:
        sections: List of outline section dicts with 'title' field.

    Returns:
        Dict with 'passed' bool and 'details' string.
    """
    section_order = _get_section_order(sections)
    titles = [s["title"] for s in sections]

    # Find the index of each required category in the actual section list
    category_positions = []
    for category in section_order:
        for i, title in enumerate(titles):
            if _matches_category(title, category["keywords"]):
                category_positions.append((i, category["label"]))
                break

    # Check that positions are in ascending order
    for i in range(len(category_positions) - 1):
        pos_a, label_a = category_positions[i]
        pos_b, label_b = category_positions[i + 1]
        if pos_a >= pos_b:
            return {
                "passed": False,
                "details": (
                    f"'{label_a}' (position {pos_a + 1}) must come before "
                    f"'{label_b}' (position {pos_b + 1})"
                ),
            }

    return {"passed": True, "details": "All sections are in correct order"}


def check_naming_conventions(sections: list[dict]) -> dict:
    """Check that section titles follow naming conventions.

    Titles must be clear, descriptive, action/purpose-oriented.
    No marketing language or internal shorthand.

    Args:
        sections: List of outline section dicts with 'title' field.

    Returns:
        Dict with 'passed' bool and 'violations' list.
    """
    violations = []
    for section in sections:
        title = section["title"]
        for pattern in BAD_NAMING_PATTERNS:
            if re.search(pattern, title, re.IGNORECASE):
                violations.append(
                    f"Section '{title}' uses unprofessional language matching '{pattern}'"
                )
        if len(title) < 3:
            violations.append(f"Section title '{title}' is too short")

    return {"passed": len(violations) == 0, "violations": violations}


def check_no_placeholders(sections: list[dict]) -> dict:
    """Check that no section contains placeholder language.

    Args:
        sections: List of outline section dicts with 'title' and 'summary' fields.

    Returns:
        Dict with 'passed' bool and 'found' list of placeholder matches.
    """
    found = []
    for section in sections:
        for field in ["title", "summary"]:
            text = section.get(field, "")
            for pattern in PLACEHOLDER_PATTERNS:
                if re.search(pattern, text, re.IGNORECASE):
                    found.append(
                        f"Section '{section['title']}' {field} contains placeholder: '{pattern}'"
                    )

    return {"passed": len(found) == 0, "found": found}


def check_section_overlap(sections: list[dict]) -> dict:
    """Flag sections with significantly overlapping summaries.

    Uses a simple word overlap heuristic.

    Args:
        sections: List of outline section dicts.

    Returns:
        Dict with 'passed' bool and 'warnings' list.
    """
    warnings = []
    for i, sec_a in enumerate(sections):
        words_a = set(sec_a.get("summary", "").lower().split())
        for j, sec_b in enumerate(sections):
            if j <= i:
                continue
            words_b = set(sec_b.get("summary", "").lower().split())
            if not words_a or not words_b:
                continue
            overlap = words_a & words_b
            # Exclude common words
            common = {"the", "a", "an", "and", "or", "is", "are", "of", "to", "in", "for", "with", "that", "this", "it", "what", "how", "who", "why"}
            meaningful_overlap = overlap - common
            min_size = min(len(words_a - common), len(words_b - common))
            if min_size > 0 and len(meaningful_overlap) / min_size > 0.6:
                warnings.append(
                    f"'{sec_a['title']}' and '{sec_b['title']}' may overlap significantly"
                )

    return {"passed": len(warnings) == 0, "warnings": warnings}


def run_all_checks(sections: list[dict]) -> dict:
    """Run all outline validation checks and return a structured report.

    Args:
        sections: List of outline section dicts.

    Returns:
        Dict with 'all_passed' bool and individual check results.
    """
    results = {
        "required_sections": check_required_sections(sections),
        "section_order": check_section_order(sections),
        "naming_conventions": check_naming_conventions(sections),
        "no_placeholders": check_no_placeholders(sections),
        "section_overlap": check_section_overlap(sections),
    }

    results["all_passed"] = all(r["passed"] for r in results.values())
    return results
