"""Build depth configuration for chapter generation.

Defines depth modes (Light, Standard, Professional, Enterprise),
build profiles, per-chapter subsection requirements, scoring thresholds,
and token scaling.

This is purely data-driven configuration — no orchestration logic.
"""

# ---------------------------------------------------------------------------
# Depth Mode Definitions
# ---------------------------------------------------------------------------

DEPTH_MODES = {
    "light": {
        "label": "Light",
        "target_pages": "20-40",
        "max_tokens": 4096,
        "min_words": 800,
        "min_subsections": 3,
    },
    "standard": {
        "label": "Standard",
        "target_pages": "40-80",
        "max_tokens": 6144,
        "min_words": 1500,
        "min_subsections": 4,
    },
    "professional": {
        "label": "Professional",
        "target_pages": "80-120",
        "max_tokens": 8192,
        "min_words": 2500,
        "min_subsections": 6,
    },
    "enterprise": {
        "label": "Enterprise",
        "target_pages": "120-150+",
        "max_tokens": 12288,
        "min_words": 3500,
        "min_subsections": 8,
    },
}

DEFAULT_DEPTH_MODE = "professional"

# Backward compatibility aliases for existing projects stored with old names
DEPTH_MODE_ALIASES = {
    "lite": "light",
    "architect": "enterprise",
}

# ---------------------------------------------------------------------------
# Build Profiles — deterministic scaling config per depth mode
# ---------------------------------------------------------------------------

BUILD_PROFILES = {
    "light": {
        "section_count": 5,
        "subsections_range": (2, 3),
        "word_target_per_chapter": 800,
        "total_page_range": (20, 40),
        "intelligence_expansion_depth": "brief",
        "architecture_expansion_depth": "minimal",
    },
    "standard": {
        "section_count": 8,
        "subsections_range": (3, 5),
        "word_target_per_chapter": 1500,
        "total_page_range": (40, 80),
        "intelligence_expansion_depth": "standard",
        "architecture_expansion_depth": "standard",
    },
    "professional": {
        "section_count": 10,
        "subsections_range": (5, 7),
        "word_target_per_chapter": 2500,
        "total_page_range": (80, 120),
        "intelligence_expansion_depth": "detailed",
        "architecture_expansion_depth": "detailed",
    },
    "enterprise": {
        "section_count": 10,
        "subsections_range": (7, 10),
        "word_target_per_chapter": 3500,
        "total_page_range": (120, 150),
        "intelligence_expansion_depth": "comprehensive",
        "architecture_expansion_depth": "comprehensive",
    },
}

# ---------------------------------------------------------------------------
# Per-Chapter Subsection Requirements (10-section Enhanced Outline)
# ---------------------------------------------------------------------------
# Keys are the section titles from outline_generator.ENHANCED_SECTIONS.
# Each maps depth mode → list of required subsection headings.

CHAPTER_REQUIREMENTS = {
    "Executive Summary": {
        "light": [
            "Vision & Strategy",
            "Business Model",
        ],
        "standard": [
            "Vision & Strategy",
            "Business Model",
            "Risk Summary",
            "Deployment Model",
        ],
        "professional": [
            "Vision & Strategy",
            "Business Model",
            "Competitive Landscape",
            "Market Size Context",
            "Risk Summary",
            "Technical High-Level Architecture",
            "Deployment Model",
            "Assumptions & Constraints",
        ],
        "enterprise": [
            "Vision & Strategy",
            "Business Model",
            "Competitive Landscape",
            "Market Size Context",
            "Risk Summary",
            "Technical High-Level Architecture",
            "Deployment Model",
            "Assumptions & Constraints",
            "Stakeholder Map",
            "Investment & Funding Context",
        ],
    },
    "Problem & Market Context": {
        "light": [
            "Detailed Problem Breakdown",
            "Existing Alternatives",
        ],
        "standard": [
            "Detailed Problem Breakdown",
            "Market Segmentation",
            "Existing Alternatives",
            "Value Differentiation Matrix",
        ],
        "professional": [
            "Detailed Problem Breakdown",
            "Market Segmentation",
            "Existing Alternatives",
            "Competitive Gap Analysis",
            "Value Differentiation Matrix",
            "Market Timing & Trends",
        ],
        "enterprise": [
            "Detailed Problem Breakdown",
            "Market Segmentation",
            "Existing Alternatives",
            "Competitive Gap Analysis",
            "Value Differentiation Matrix",
            "Market Timing & Trends",
            "Regulatory Landscape",
            "Total Addressable Market Analysis",
        ],
    },
    "User Personas & Core Use Cases": {
        "light": [
            "Primary User Personas",
            "Core Use Cases",
        ],
        "standard": [
            "Primary User Personas",
            "Core Use Cases",
            "User Journey Maps",
            "Access Control Model",
        ],
        "professional": [
            "Primary User Personas",
            "Secondary User Personas",
            "Core Use Cases",
            "User Journey Maps",
            "Access Control Model",
            "Onboarding & Activation Flow",
        ],
        "enterprise": [
            "Primary User Personas",
            "Secondary User Personas",
            "Core Use Cases",
            "Edge-Case Use Cases",
            "User Journey Maps",
            "Access Control Model",
            "Onboarding & Activation Flow",
            "Internationalization & Localization",
        ],
    },
    "Functional Requirements": {
        "light": [
            "Feature Specifications",
            "Input/Output Definitions",
        ],
        "standard": [
            "Feature Specifications",
            "Input/Output Definitions",
            "Workflow Diagrams",
            "Acceptance Criteria",
        ],
        "professional": [
            "Feature Specifications",
            "Input/Output Definitions",
            "Workflow Diagrams",
            "Acceptance Criteria",
            "API Endpoint Definitions",
            "Error Handling & Edge Cases",
            "Feature Dependency Map",
        ],
        "enterprise": [
            "Feature Specifications",
            "Input/Output Definitions",
            "Workflow Diagrams",
            "Acceptance Criteria",
            "API Endpoint Definitions",
            "Error Handling & Edge Cases",
            "Feature Dependency Map",
            "Integration Contracts",
            "Feature Flag Strategy",
        ],
    },
    "AI & Intelligence Architecture": {
        "light": [
            "AI Capabilities Overview",
            "Model Selection",
        ],
        "standard": [
            "AI Capabilities Overview",
            "Model Selection",
            "Prompt Engineering Strategy",
            "Inference Pipeline",
        ],
        "professional": [
            "AI Capabilities Overview",
            "Model Selection & Comparison",
            "Prompt Engineering Strategy",
            "Inference Pipeline",
            "Training & Fine-Tuning Plan",
            "AI Safety & Guardrails",
            "Cost Estimation & Optimization",
        ],
        "enterprise": [
            "AI Capabilities Overview",
            "Model Selection & Comparison",
            "Prompt Engineering Strategy",
            "Inference Pipeline",
            "Training & Fine-Tuning Plan",
            "AI Safety & Guardrails",
            "Cost Estimation & Optimization",
            "Evaluation & Benchmarking",
            "Model Versioning & Rollback",
            "Responsible AI Framework",
        ],
    },
    "Non-Functional Requirements": {
        "light": [
            "Performance Requirements",
            "Scalability Approach",
        ],
        "standard": [
            "Performance Requirements",
            "Scalability Approach",
            "Availability & Reliability",
            "Monitoring & Alerting",
        ],
        "professional": [
            "Performance Requirements",
            "Scalability Approach",
            "Availability & Reliability",
            "Monitoring & Alerting",
            "Disaster Recovery",
            "Accessibility Standards",
        ],
        "enterprise": [
            "Performance Requirements",
            "Scalability Approach",
            "Availability & Reliability",
            "Monitoring & Alerting",
            "Disaster Recovery",
            "Accessibility Standards",
            "Capacity Planning",
            "SLA Definitions",
        ],
    },
    "Technical Architecture & Data Model": {
        "light": [
            "Service Architecture",
            "Data Model Overview",
        ],
        "standard": [
            "Service Architecture",
            "Data Model Overview",
            "API Design",
            "Technology Stack",
        ],
        "professional": [
            "Service Architecture",
            "Database Schema",
            "API Design",
            "Technology Stack",
            "Infrastructure & Deployment",
            "CI/CD Pipeline",
            "Environment Configuration",
        ],
        "enterprise": [
            "Service Architecture",
            "Database Schema",
            "API Design",
            "Technology Stack",
            "Infrastructure & Deployment",
            "CI/CD Pipeline",
            "Environment Configuration",
            "Data Migration Strategy",
            "Caching Architecture",
            "Event-Driven Patterns",
        ],
    },
    "Security & Compliance": {
        "light": [
            "Authentication & Authorization",
            "Data Privacy",
        ],
        "standard": [
            "Authentication & Authorization",
            "Data Privacy",
            "Security Architecture",
            "Compliance Requirements",
        ],
        "professional": [
            "Authentication & Authorization",
            "Data Privacy & Encryption",
            "Security Architecture",
            "Compliance Requirements",
            "Threat Model",
            "Audit Logging",
        ],
        "enterprise": [
            "Authentication & Authorization",
            "Data Privacy & Encryption",
            "Security Architecture",
            "Compliance Requirements",
            "Threat Model",
            "Audit Logging",
            "Penetration Testing Plan",
            "Incident Response Playbook",
        ],
    },
    "Success Metrics & KPIs": {
        "light": [
            "Key Metrics",
            "Measurement Plan",
        ],
        "standard": [
            "Key Metrics",
            "Measurement Plan",
            "Analytics Architecture",
            "Reporting Dashboard",
        ],
        "professional": [
            "Key Metrics",
            "Measurement Plan",
            "Analytics Architecture",
            "Reporting Dashboard",
            "A/B Testing Framework",
            "Business Impact Tracking",
        ],
        "enterprise": [
            "Key Metrics",
            "Measurement Plan",
            "Analytics Architecture",
            "Reporting Dashboard",
            "A/B Testing Framework",
            "Business Impact Tracking",
            "Data Warehouse Design",
            "Cohort Analysis Plan",
        ],
    },
    "Roadmap & Phased Delivery": {
        "light": [
            "MVP Scope",
            "Phase Plan",
        ],
        "standard": [
            "MVP Scope",
            "Phase Plan",
            "Milestone Definitions",
            "Resource Requirements",
        ],
        "professional": [
            "MVP Scope",
            "Phase Plan",
            "Milestone Definitions",
            "Resource Requirements",
            "Risk Mitigation Timeline",
            "Go-To-Market Strategy",
        ],
        "enterprise": [
            "MVP Scope",
            "Phase Plan",
            "Milestone Definitions",
            "Resource Requirements",
            "Risk Mitigation Timeline",
            "Go-To-Market Strategy",
            "Team Structure & Hiring Plan",
            "Technical Debt Budget",
        ],
    },
}

# ---------------------------------------------------------------------------
# Fallback Requirements for 7-Section Default Outlines
# ---------------------------------------------------------------------------

CHAPTER_REQUIREMENTS_DEFAULT = {
    "System Purpose & Context": {
        "light": ["Purpose", "Context"],
        "standard": ["Purpose", "Context", "Scope", "Stakeholders"],
        "professional": [
            "Purpose", "Context", "Scope", "Stakeholders",
            "Business Model", "Competitive Landscape",
        ],
        "enterprise": [
            "Purpose", "Context", "Scope", "Stakeholders",
            "Business Model", "Competitive Landscape",
            "Market Timing", "Investment Context",
        ],
    },
    "Target Users & Roles": {
        "light": ["User Personas", "Roles"],
        "standard": ["User Personas", "Roles", "Access Control", "User Journeys"],
        "professional": [
            "User Personas", "Roles", "Access Control",
            "User Journeys", "Onboarding Flow", "Edge Cases",
        ],
        "enterprise": [
            "User Personas", "Roles", "Access Control",
            "User Journeys", "Onboarding Flow", "Edge Cases",
            "Internationalization", "Accessibility",
        ],
    },
    "Core Capabilities": {
        "light": ["Features", "Integration Points"],
        "standard": ["Features", "Integration Points", "API Design", "Workflows"],
        "professional": [
            "Features", "Integration Points", "API Design",
            "Workflows", "Acceptance Criteria", "Error Handling",
        ],
        "enterprise": [
            "Features", "Integration Points", "API Design",
            "Workflows", "Acceptance Criteria", "Error Handling",
            "Feature Dependencies", "Feature Flags",
        ],
    },
    "Non-Goals & Explicit Exclusions": {
        "light": ["Non-Goals", "Exclusions"],
        "standard": ["Non-Goals", "Exclusions", "Future Considerations", "Scope Boundaries"],
        "professional": [
            "Non-Goals", "Exclusions", "Future Considerations",
            "Scope Boundaries", "Anti-Patterns", "Decision Rationale",
        ],
        "enterprise": [
            "Non-Goals", "Exclusions", "Future Considerations",
            "Scope Boundaries", "Anti-Patterns", "Decision Rationale",
            "Deferred Features", "Technical Debt Boundaries",
        ],
    },
    "High-Level Architecture": {
        "light": ["Architecture Overview", "Technology Stack"],
        "standard": [
            "Architecture Overview", "Technology Stack",
            "Data Model", "Infrastructure",
        ],
        "professional": [
            "Architecture Overview", "Technology Stack",
            "Data Model", "Infrastructure",
            "CI/CD Pipeline", "Security Architecture",
        ],
        "enterprise": [
            "Architecture Overview", "Technology Stack",
            "Data Model", "Infrastructure",
            "CI/CD Pipeline", "Security Architecture",
            "Caching Strategy", "Event Architecture",
        ],
    },
    "Execution Phases": {
        "light": ["MVP Scope", "Phase Plan"],
        "standard": ["MVP Scope", "Phase Plan", "Milestones", "Resources"],
        "professional": [
            "MVP Scope", "Phase Plan", "Milestones",
            "Resources", "Risk Mitigation", "Go-To-Market",
        ],
        "enterprise": [
            "MVP Scope", "Phase Plan", "Milestones",
            "Resources", "Risk Mitigation", "Go-To-Market",
            "Team Structure", "Technical Debt Budget",
        ],
    },
    "Risks, Constraints, and Assumptions": {
        "light": ["Risks", "Constraints"],
        "standard": ["Risks", "Constraints", "Assumptions", "Mitigation Plans"],
        "professional": [
            "Risks", "Constraints", "Assumptions",
            "Mitigation Plans", "Compliance Requirements", "Monitoring",
        ],
        "enterprise": [
            "Risks", "Constraints", "Assumptions",
            "Mitigation Plans", "Compliance Requirements", "Monitoring",
            "Incident Response", "Disaster Recovery",
        ],
    },
}

# ---------------------------------------------------------------------------
# Score Thresholds (per depth mode)
# ---------------------------------------------------------------------------

SCORE_THRESHOLDS = {
    "light": {"incomplete": 35, "needs_expansion": 55, "complete": 55},
    "standard": {"incomplete": 38, "needs_expansion": 65, "complete": 65},
    "professional": {"incomplete": 40, "needs_expansion": 70, "complete": 70},
    "enterprise": {"incomplete": 40, "needs_expansion": 75, "complete": 75},
}

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve_depth_mode(mode: str) -> str:
    """Resolve a depth mode key, applying backward compatibility aliases.

    Args:
        mode: A depth mode key (may be old or new name).

    Returns:
        The resolved canonical depth mode key.

    Raises:
        ValueError: If mode is not valid even after alias resolution.
    """
    resolved = DEPTH_MODE_ALIASES.get(mode, mode)
    if resolved not in DEPTH_MODES:
        raise ValueError(
            f"Invalid depth mode: {mode}. Must be one of {list(DEPTH_MODES.keys())}"
        )
    return resolved


def get_depth_config(mode: str) -> dict:
    """Return the full configuration dict for a depth mode.

    Args:
        mode: One of 'light', 'standard', 'professional', 'enterprise'
              (or legacy aliases 'lite', 'architect').

    Returns:
        Dict with label, target_pages, max_tokens, min_words, min_subsections.

    Raises:
        ValueError: If mode is not valid.
    """
    resolved = resolve_depth_mode(mode)
    return dict(DEPTH_MODES[resolved])


def get_build_profile(mode: str) -> dict:
    """Return the build profile for a depth mode.

    Args:
        mode: One of the valid depth mode keys.

    Returns:
        Dict with section_count, subsections_range, word_target_per_chapter,
        total_page_range, intelligence_expansion_depth, architecture_expansion_depth.

    Raises:
        ValueError: If mode is not valid.
    """
    resolved = resolve_depth_mode(mode)
    return dict(BUILD_PROFILES[resolved])


def get_chapter_subsections(section_title: str, mode: str) -> list[str]:
    """Return the required subsection headings for a chapter at a given depth.

    Checks the 10-section enhanced requirements first, then falls back to
    7-section default requirements. If the title is unknown, returns a
    generic minimal list based on the depth mode's min_subsections.

    Args:
        section_title: The outline section title (e.g. "Executive Summary").
        mode: One of 'light', 'standard', 'professional', 'enterprise'
              (or legacy aliases).

    Returns:
        List of subsection heading strings.

    Raises:
        ValueError: If mode is not valid.
    """
    resolved = resolve_depth_mode(mode)

    # Try enhanced 10-section requirements
    if section_title in CHAPTER_REQUIREMENTS:
        return list(CHAPTER_REQUIREMENTS[section_title].get(resolved, []))

    # Try default 7-section requirements
    if section_title in CHAPTER_REQUIREMENTS_DEFAULT:
        return list(CHAPTER_REQUIREMENTS_DEFAULT[section_title].get(resolved, []))

    # Unknown title — return generic subsections
    min_subs = DEPTH_MODES[resolved]["min_subsections"]
    generic = ["Overview", "Details", "Implementation", "Considerations",
               "Dependencies", "Testing Strategy", "Deployment Notes",
               "Monitoring & Operations"]
    return generic[:min_subs]


def get_scoring_thresholds(mode: str) -> dict:
    """Return scoring configuration for a depth mode.

    Args:
        mode: One of 'light', 'standard', 'professional', 'enterprise'
              (or legacy aliases).

    Returns:
        Dict with min_words, min_subsections, incomplete_threshold,
        complete_threshold.

    Raises:
        ValueError: If mode is not valid.
    """
    resolved = resolve_depth_mode(mode)
    config = DEPTH_MODES[resolved]
    thresholds = SCORE_THRESHOLDS[resolved]
    return {
        "min_words": config["min_words"],
        "min_subsections": config["min_subsections"],
        "incomplete_threshold": thresholds["incomplete"],
        "complete_threshold": thresholds["complete"],
    }


def estimate_pages(word_count: int) -> int:
    """Estimate page count from word count (approximately 500 words per page).

    Args:
        word_count: Total number of words.

    Returns:
        Estimated page count (minimum 1).
    """
    if word_count <= 0:
        return 0
    return max(1, word_count // 500)


def get_all_depth_modes() -> dict:
    """Return all depth mode configurations (for UI dropdowns).

    Returns:
        Dict mapping mode key to config dict.
    """
    return {k: dict(v) for k, v in DEPTH_MODES.items()}
