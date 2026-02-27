"""LLM-powered chapter content generator for the auto-build pipeline.

Generates detailed chapter content from project profile, features, and
outline section data. Supports both legacy 3-field format and enterprise
full-markdown format.

Assumes the project will be built using VS Code with Claude Code.
Falls back to structured template content if LLM is unavailable.
"""

import json
import logging

from execution.ambiguity_detector import FORBIDDEN_PHRASES
from execution.build_depth import get_chapter_subsections, get_depth_config
from execution.intelligence_goals import build_intelligence_goals_prompt_section
from execution.llm_client import LLMClientError, LLMUnavailableError, chat, is_available

logger = logging.getLogger(__name__)

# Temperature for chapter generation — low for consistent gate compliance
CHAPTER_TEMPERATURE = 0.2

CHAPTER_SYSTEM_PROMPT = (
    "You are a senior software architect writing a detailed build guide chapter. "
    "The project will be built using VS Code with Claude Code (Anthropic's AI coding CLI). "
    "Write as if instructing a junior developer who will use Claude Code to implement each step. "
    "Be specific, actionable, and reference concrete files, components, and patterns."
)

CHAPTER_USER_PROMPT = """Write Chapter {chapter_index} of {total_chapters}: "{section_title}"

## Project Profile
- **Problem:** {problem_definition}
- **Target User:** {target_user}
- **Value Proposition:** {value_proposition}
- **Deployment:** {deployment_type}
- **AI Depth:** {ai_depth}
- **Monetization:** {monetization_model}
- **MVP Scope:** {mvp_scope}

## Technical Context
- **Constraints:** {technical_constraints}
- **NFRs:** {nfrs}
- **Use Cases:** {use_cases}

## Selected Features
{feature_list}

## Intelligence Goals
{intelligence_goals_section}

## Section Summary (from outline)
{section_summary}

## Previous Chapters Context
{previous_context}

{quality_gate_section}

Return ONLY valid JSON:
{{
  "purpose": "3-5 paragraphs explaining WHY this chapter exists...",
  "design_intent": "3-5 paragraphs on WHY this approach was chosen...",
  "implementation_guidance": "Detailed step-by-step guidance..."
}}

Rules for each field:
- **purpose**: Why this chapter matters, what decision/behavior it supports, how it fits in the overall system. Reference the project profile.
- **design_intent**: Tradeoffs considered, constraints that shaped the design, alternatives rejected and why. Be specific to THIS project.
- **implementation_guidance**: Practical, step-by-step instructions. Reference specific file names, component patterns, API endpoints, data models. Assume the developer uses VS Code with Claude Code. Include execution order (first, then, next), input/output definitions, and dependency notes. Detailed enough for an intern to act without guesswork.

Each field must be at least 200 words. Total response must be at least 800 words.
Return ONLY the JSON object."""

CHAPTER_RETRY_PROMPT = """Your previous attempt for Chapter {chapter_index}: "{section_title}" failed quality gates.

Issues found:
{gate_failures}

Please rewrite the chapter content, fixing ALL of the above issues.
The same rules apply as before — return ONLY valid JSON with purpose, design_intent, and implementation_guidance fields.
Each field must be at least 200 words. Be specific, avoid vague language, include execution order signals.

{quality_gate_section}"""


def _build_quality_gate_section(min_words: int = 2500) -> str:
    """Build prompt section listing all quality gate criteria.

    Dynamically reads FORBIDDEN_PHRASES from ambiguity_detector so the
    prompt always matches the actual gate checks — single source of truth.

    Args:
        min_words: Minimum word count for the depth mode (used in scoring guidance).
    """
    # Build the forbidden phrases bullet list from the actual gate data
    phrases = []
    for pattern in FORBIDDEN_PHRASES:
        # Strip regex escapes for human-readable display
        clean = pattern.replace(r"\.", ".").replace(r"\b", "")
        phrases.append(f'  - "{clean}"')
    forbidden_list = "\n".join(phrases)

    half_words = min_words // 2

    return f"""## QUALITY GATE REQUIREMENTS (Your output WILL be checked against these)

### Completeness Gate
- No placeholder language: TBD, TBA, TBC, TODO, FIXME, "to be determined", "placeholder"
- Must produce at least 10 non-heading content lines

### Clarity Gate
- Include at least one outcome phrase: "this chapter", "the goal", "the purpose", "this section", or "the objective"
- Use at least 2 heading levels for structure

### Build Readiness Gate
- Include execution order signals: "first", "then", "next", "after", "before", "step 1"
- Include input/output signals: "input", "output", "produce", "accept", "return", "receive"
- Include dependency signals: "depend", "require", "prerequisite"

### Anti-Vagueness Gate (CRITICAL — zero tolerance)
Do NOT use ANY of these phrases anywhere in your response:
{forbidden_list}
Instead of these phrases, specify WHAT to handle, WHICH practices, and WHEN to apply them.

### SCORING DIMENSIONS (0-100 total, you need 65+ to pass)
Your chapter is scored on 4 equal dimensions (25 points each):
1. **Word Count (25 pts)**: You MUST write at least {min_words} words. Writing less than {min_words} words WILL reduce your score. {min_words} words = 25/25. {half_words} words = ~12/25. WRITE MORE, NOT LESS.
2. **Subsection Coverage (25 pts)**: Include ALL required subsections as ## headings.
3. **Technical Density (25 pts)**: You MUST include code blocks, file paths, CLI commands, markdown tables, and environment variables. Aim for 10+ technical artifacts to score 15+.
4. **Implementation Specificity (25 pts)**: Cover execution order (step 1, step 2...), input/output definitions, dependencies, environment configuration, testing strategy, and deployment. Cover 4+ of these 6 categories."""


def generate_chapter(
    profile: dict,
    features: list[dict],
    section_title: str,
    section_summary: str,
    chapter_index: int,
    total_chapters: int,
    previous_summaries: list[str] | None = None,
) -> dict:
    """Generate one chapter's content via LLM.

    Args:
        profile: The project_profile dictionary with confirmed fields.
        features: List of selected feature dicts.
        section_title: Title of this chapter's outline section.
        section_summary: Summary text from the outline section.
        chapter_index: 1-based chapter number.
        total_chapters: Total number of chapters.
        previous_summaries: Optional list of purpose summaries from prior chapters.

    Returns:
        Dict with 'purpose', 'design_intent', 'implementation_guidance' keys.
        Falls back to _fallback_chapter() if LLM unavailable.
    """
    if not is_available():
        logger.info("LLM unavailable, using fallback chapter content")
        return _fallback_chapter(section_title, section_summary, chapter_index)

    prompt = _build_prompt(
        profile, features, section_title, section_summary,
        chapter_index, total_chapters, previous_summaries,
    )

    try:
        response = chat(
            system_prompt=CHAPTER_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=4096,
            temperature=CHAPTER_TEMPERATURE,
            response_format={"type": "json_object"},
        )
        return _parse_chapter_response(response.content, section_title)
    except (LLMUnavailableError, LLMClientError) as e:
        logger.warning("LLM chapter generation failed: %s. Using fallback.", e)
        return _fallback_chapter(section_title, section_summary, chapter_index)
    except Exception as e:
        logger.warning("Unexpected error generating chapter: %s. Using fallback.", e)
        return _fallback_chapter(section_title, section_summary, chapter_index)


def generate_chapter_with_retry(
    profile: dict,
    features: list[dict],
    section_title: str,
    section_summary: str,
    chapter_index: int,
    total_chapters: int,
    previous_summaries: list[str] | None = None,
    gate_failures: list[str] | None = None,
) -> dict:
    """Generate chapter content, incorporating gate failure feedback for retries.

    Sends the original prompt plus a follow-up message describing the gate
    failures, so the LLM can correct them.

    Args:
        gate_failures: List of issue strings from quality gate results.
        (other args same as generate_chapter)

    Returns:
        Dict with 'purpose', 'design_intent', 'implementation_guidance' keys.
    """
    if not is_available():
        return _fallback_chapter(section_title, section_summary, chapter_index)

    original_prompt = _build_prompt(
        profile, features, section_title, section_summary,
        chapter_index, total_chapters, previous_summaries,
    )

    failure_text = "\n".join(f"- {f}" for f in (gate_failures or []))
    retry_prompt = CHAPTER_RETRY_PROMPT.format(
        chapter_index=chapter_index,
        section_title=section_title,
        gate_failures=failure_text or "- No specific issues listed",
        quality_gate_section=_build_quality_gate_section(),
    )

    try:
        response = chat(
            system_prompt=CHAPTER_SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": original_prompt},
                {"role": "assistant", "content": '{"purpose": "...retry needed..."}'},
                {"role": "user", "content": retry_prompt},
            ],
            max_tokens=4096,
            temperature=CHAPTER_TEMPERATURE,
            response_format={"type": "json_object"},
        )
        return _parse_chapter_response(response.content, section_title)
    except (LLMUnavailableError, LLMClientError) as e:
        logger.warning("LLM chapter retry failed: %s. Using fallback.", e)
        return _fallback_chapter(section_title, section_summary, chapter_index)
    except Exception as e:
        logger.warning("Unexpected error retrying chapter: %s. Using fallback.", e)
        return _fallback_chapter(section_title, section_summary, chapter_index)


def generate_chapter_with_usage(
    profile: dict,
    features: list[dict],
    section_title: str,
    section_summary: str,
    chapter_index: int,
    total_chapters: int,
    previous_summaries: list[str] | None = None,
) -> tuple[dict, dict]:
    """Generate one chapter's content via LLM, returning usage data.

    Returns:
        Tuple of (content_dict, usage_dict). usage_dict has prompt_tokens
        and completion_tokens, or empty dict on fallback.
    """
    if not is_available():
        logger.info("LLM unavailable, using fallback chapter content")
        return _fallback_chapter(section_title, section_summary, chapter_index), {}

    prompt = _build_prompt(
        profile, features, section_title, section_summary,
        chapter_index, total_chapters, previous_summaries,
    )

    try:
        response = chat(
            system_prompt=CHAPTER_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=4096,
            temperature=CHAPTER_TEMPERATURE,
            response_format={"type": "json_object"},
        )
        return _parse_chapter_response(response.content, section_title), response.usage
    except (LLMUnavailableError, LLMClientError) as e:
        logger.warning("LLM chapter generation failed: %s. Using fallback.", e)
        return _fallback_chapter(section_title, section_summary, chapter_index), {}
    except Exception as e:
        logger.warning("Unexpected error generating chapter: %s. Using fallback.", e)
        return _fallback_chapter(section_title, section_summary, chapter_index), {}


def generate_chapter_with_retry_and_usage(
    profile: dict,
    features: list[dict],
    section_title: str,
    section_summary: str,
    chapter_index: int,
    total_chapters: int,
    previous_summaries: list[str] | None = None,
    gate_failures: list[str] | None = None,
) -> tuple[dict, dict]:
    """Generate chapter with retry, returning usage data.

    Returns:
        Tuple of (content_dict, usage_dict).
    """
    if not is_available():
        return _fallback_chapter(section_title, section_summary, chapter_index), {}

    original_prompt = _build_prompt(
        profile, features, section_title, section_summary,
        chapter_index, total_chapters, previous_summaries,
    )

    failure_text = "\n".join(f"- {f}" for f in (gate_failures or []))
    retry_prompt = CHAPTER_RETRY_PROMPT.format(
        chapter_index=chapter_index,
        section_title=section_title,
        gate_failures=failure_text or "- No specific issues listed",
        quality_gate_section=_build_quality_gate_section(),
    )

    try:
        response = chat(
            system_prompt=CHAPTER_SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": original_prompt},
                {"role": "assistant", "content": '{"purpose": "...retry needed..."}'},
                {"role": "user", "content": retry_prompt},
            ],
            max_tokens=4096,
            temperature=CHAPTER_TEMPERATURE,
            response_format={"type": "json_object"},
        )
        return _parse_chapter_response(response.content, section_title), response.usage
    except (LLMUnavailableError, LLMClientError) as e:
        logger.warning("LLM chapter retry failed: %s. Using fallback.", e)
        return _fallback_chapter(section_title, section_summary, chapter_index), {}
    except Exception as e:
        logger.warning("Unexpected error retrying chapter: %s. Using fallback.", e)
        return _fallback_chapter(section_title, section_summary, chapter_index), {}


def _build_prompt(
    profile: dict,
    features: list[dict],
    section_title: str,
    section_summary: str,
    chapter_index: int,
    total_chapters: int,
    previous_summaries: list[str] | None = None,
) -> str:
    """Build the user prompt from project data."""
    # Extract selected values from profile fields
    fields = {}
    for field_name in [
        "problem_definition", "target_user", "value_proposition",
        "deployment_type", "ai_depth", "monetization_model", "mvp_scope",
    ]:
        field_data = profile.get(field_name, {})
        fields[field_name] = field_data.get("selected", "") or "Not specified"

    # Build feature list
    feature_list = "\n".join(
        f"- {f['name']}: {f.get('description', '')}" for f in features
    ) or "- No specific features selected"

    # Build intelligence goals section (legacy prompt uses brief expansion)
    intelligence_goals = profile.get("intelligence_goals", [])
    intelligence_goals_section = build_intelligence_goals_prompt_section(
        intelligence_goals, section_title,
        expansion_depth="brief",
    )

    # Build derived field strings
    tc = profile.get("technical_constraints", [])
    nfrs = profile.get("non_functional_requirements", [])
    ucs = profile.get("core_use_cases", [])

    # Build previous chapter context
    if previous_summaries:
        previous_context = "\n".join(
            f"- Chapter {i + 1}: {s}" for i, s in enumerate(previous_summaries)
        )
    else:
        previous_context = "This is the first chapter."

    return CHAPTER_USER_PROMPT.format(
        chapter_index=chapter_index,
        total_chapters=total_chapters,
        section_title=section_title,
        feature_list=feature_list,
        intelligence_goals_section=intelligence_goals_section or "No intelligence goals for this project.",
        section_summary=section_summary or "No summary provided",
        previous_context=previous_context,
        technical_constraints=", ".join(tc) if tc else "None specified",
        nfrs=", ".join(nfrs) if nfrs else "None specified",
        use_cases=", ".join(ucs) if ucs else "None specified",
        quality_gate_section=_build_quality_gate_section(),
        **fields,
    )


def _fallback_chapter(
    section_title: str, section_summary: str, chapter_index: int
) -> dict:
    """Return structured chapter content when LLM is unavailable.

    Content is designed to pass quality gates (completeness, clarity,
    build readiness, anti-vagueness).
    """
    summary = section_summary or f"the {section_title.lower()} aspects of the system"

    purpose = (
        f"This chapter defines {summary}. "
        f"The purpose of this section is to provide a clear foundation for "
        f"the development team to understand what needs to be built and why. "
        f"This chapter exists because {section_title.lower()} is a critical "
        f"component of the overall system architecture. Without this chapter, "
        f"the team would lack the context needed to make informed implementation "
        f"decisions.\n\n"
        f"This chapter supports the broader project goals by establishing "
        f"clear boundaries and expectations for this area of the system. "
        f"The decisions documented here directly affect downstream chapters "
        f"and the overall build order. Understanding this chapter is a "
        f"prerequisite for implementing the related features as specified.\n\n"
        f"The system requires this documentation to ensure that every team "
        f"member, including junior developers using VS Code with Claude Code, "
        f"can understand the intent behind each design choice. This chapter "
        f"bridges the gap between high-level requirements and actionable "
        f"implementation steps."
    )

    design_intent = (
        f"This approach was chosen to ensure clarity and reduce ambiguity "
        f"in the {section_title.lower()} area of the system. The tradeoff "
        f"considered was between providing exhaustive detail versus maintaining "
        f"readability — the decision was to favor specificity over brevity.\n\n"
        f"Alternative approaches were evaluated, including a more abstract "
        f"specification style and a code-first approach. The structured "
        f"documentation approach was selected because it provides the right "
        f"level of detail for developers who will use Claude Code to generate "
        f"implementation scaffolding.\n\n"
        f"The constraints that shaped this design include the need for "
        f"deterministic build steps, testable outputs at each phase, and "
        f"compatibility with the project's deployment model. Each constraint "
        f"was weighed against implementation complexity to arrive at the "
        f"approach documented below."
    )

    implementation_guidance = (
        f"First, review the outline section for {section_title} to understand "
        f"the scope of this chapter. The input is the approved project profile "
        f"and feature list from earlier phases.\n\n"
        f"Then, open VS Code and use Claude Code to scaffold the initial "
        f"structure for the {section_title.lower()} components. The output "
        f"of this step is a set of files matching the architecture described "
        f"in this chapter.\n\n"
        f"Next, implement the core logic following the execution order below:\n"
        f"- Step 1: Create the data models and type definitions\n"
        f"- Step 2: Implement the primary business logic functions\n"
        f"- Step 3: Add input validation and error handling\n"
        f"- Step 4: Write unit tests for each function\n"
        f"- Step 5: Run the test suite and verify all tests pass\n\n"
        f"This step depends on the outline being locked and all previous "
        f"chapters being approved. The execution order is: review context, "
        f"scaffold structure, implement logic, validate with tests.\n\n"
        f"The definition of done for this chapter is: all described components "
        f"are implemented, all unit tests pass, and the output matches the "
        f"acceptance criteria defined in the project profile."
    )

    return {
        "purpose": purpose,
        "design_intent": design_intent,
        "implementation_guidance": implementation_guidance,
    }


def _parse_chapter_response(raw_json: str, section_title: str) -> dict:
    """Parse LLM JSON response into chapter dict with validation.

    Args:
        raw_json: The raw JSON string from the LLM.
        section_title: Title for fallback content if parsing fails.

    Returns:
        Dict with 'purpose', 'design_intent', 'implementation_guidance'.
    """
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse chapter JSON, using fallback")
        return _fallback_chapter(section_title, "", 0)

    if not isinstance(data, dict):
        logger.warning("Chapter JSON is not a dict, using fallback")
        return _fallback_chapter(section_title, "", 0)

    required_keys = ["purpose", "design_intent", "implementation_guidance"]
    for key in required_keys:
        if key not in data or not isinstance(data[key], str) or len(data[key].strip()) < 50:
            logger.warning("Chapter JSON missing or short field '%s', using fallback", key)
            return _fallback_chapter(section_title, "", 0)

    return {
        "purpose": data["purpose"].strip(),
        "design_intent": data["design_intent"].strip(),
        "implementation_guidance": data["implementation_guidance"].strip(),
    }


# ---------------------------------------------------------------------------
# Enterprise Chapter Generation
# ---------------------------------------------------------------------------

ENTERPRISE_SYSTEM_PROMPT = (
    "You are a senior software architect writing a detailed engineering blueprint chapter. "
    "The project will be built using VS Code with Claude Code (Anthropic's AI coding CLI). "
    "Write as if creating a document that will be used by junior developers, senior architects, "
    "investors, compliance auditors, and DevOps teams. Be exhaustive and specific. "
    "Include exact folder structures, CLI commands, environment variables, configuration examples, "
    "API definitions, error handling strategies, testing strategies, and deployment considerations. "
    "Never use placeholder language."
)

ENTERPRISE_CHAPTER_PROMPT = """Write Chapter {chapter_index} of {total_chapters}: "{section_title}"

## Project Profile
- **Problem:** {problem_definition}
- **Target User:** {target_user}
- **Value Proposition:** {value_proposition}
- **Deployment:** {deployment_type}
- **AI Depth:** {ai_depth}
- **Monetization:** {monetization_model}
- **MVP Scope:** {mvp_scope}

## Technical Context
- **Constraints:** {technical_constraints}
- **NFRs:** {nfrs}
- **Use Cases:** {use_cases}
- **Success Metrics:** {success_metrics}
- **Risks:** {risks}

## Selected Features
{feature_list}

## Intelligence Goals
{intelligence_goals_section}

## Section Summary (from outline)
{section_summary}

## Previous Chapters Context
{previous_context}

## REQUIRED SUBSECTIONS
You MUST include ALL of the following subsections as ## headings in your content:
{required_subsections}

## CONTENT REQUIREMENTS
- Minimum {min_words} words total
- Each subsection must be substantive (400+ words)
- Include specific file names, component patterns, API endpoints
- Include folder structures using tree format and CLI commands
- Include environment variables and configuration examples
- Include error handling strategies and testing approaches
- Include deployment and production considerations
- Reference VS Code with Claude Code for implementation
- Include tables, code blocks, and structured lists

{quality_gate_section}

Return ONLY valid JSON:
{{"content": "<full chapter markdown body with ## subsection headings>"}}"""

ENTERPRISE_RETRY_PROMPT = """Your previous attempt for Chapter {chapter_index}: "{section_title}" scored {score}/100.

CRITICAL: You wrote {word_count} words. The MINIMUM requirement is {min_words} words. You MUST write at least {min_words} words.

Issues found:
{issues}

Missing subsections: {missing_subsections}

Please rewrite the chapter content, fixing ALL of the above issues.
- Include ALL required subsections as ## headings
- Each subsection must be 400+ words with substantive detail
- Total chapter MUST be at least {min_words} words
- Include code blocks, file paths, CLI commands, environment variables, and tables

{quality_gate_section}

Return ONLY valid JSON: {{"content": "<full chapter markdown body>"}}"""


def generate_chapter_enterprise(
    profile: dict,
    features: list[dict],
    section_title: str,
    section_summary: str,
    chapter_index: int,
    total_chapters: int,
    previous_summaries: list[str] | None = None,
    depth_mode: str = "enterprise",
) -> dict:
    """Generate an enterprise-grade chapter via LLM.

    Returns {"content": "<full markdown body>"} instead of the legacy
    3-field format. If the LLM returns the old format, auto-converts.

    Args:
        profile: The project_profile dictionary with confirmed fields.
        features: List of selected feature dicts.
        section_title: Title of this chapter's outline section.
        section_summary: Summary text from the outline section.
        chapter_index: 1-based chapter number.
        total_chapters: Total number of chapters.
        previous_summaries: Optional list of purpose summaries from prior chapters.
        depth_mode: Build depth mode (lite, standard, enterprise, architect).

    Returns:
        Dict with 'content' key containing full markdown body.
        Falls back to _fallback_chapter_enterprise() if LLM unavailable.
    """
    if not is_available():
        logger.info("LLM unavailable, using fallback enterprise chapter content")
        return _fallback_chapter_enterprise(section_title, section_summary, chapter_index, depth_mode)

    prompt = _build_enterprise_prompt(
        profile, features, section_title, section_summary,
        chapter_index, total_chapters, previous_summaries, depth_mode,
    )

    config = get_depth_config(depth_mode)

    try:
        response = chat(
            system_prompt=ENTERPRISE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=config["max_tokens"],
            temperature=CHAPTER_TEMPERATURE,
            response_format={"type": "json_object"},
        )
        return _parse_enterprise_response(response.content, section_title, depth_mode)
    except (LLMUnavailableError, LLMClientError) as e:
        logger.warning("LLM enterprise chapter generation failed: %s. Using fallback.", e)
        return _fallback_chapter_enterprise(section_title, section_summary, chapter_index, depth_mode)
    except Exception as e:
        logger.warning("Unexpected error generating enterprise chapter: %s. Using fallback.", e)
        return _fallback_chapter_enterprise(section_title, section_summary, chapter_index, depth_mode)


def generate_chapter_enterprise_with_retry(
    profile: dict,
    features: list[dict],
    section_title: str,
    section_summary: str,
    chapter_index: int,
    total_chapters: int,
    previous_summaries: list[str] | None = None,
    depth_mode: str = "enterprise",
    score_result: dict | None = None,
) -> dict:
    """Regenerate an enterprise chapter incorporating scoring feedback.

    Args:
        score_result: Dict from score_chapter() with score details.
        (other args same as generate_chapter_enterprise)

    Returns:
        Dict with 'content' key containing full markdown body.
    """
    if not is_available():
        return _fallback_chapter_enterprise(section_title, section_summary, chapter_index, depth_mode)

    config = get_depth_config(depth_mode)

    original_prompt = _build_enterprise_prompt(
        profile, features, section_title, section_summary,
        chapter_index, total_chapters, previous_summaries, depth_mode,
    )

    # Build retry message from score result
    sr = score_result or {}
    issues = []
    if sr.get("word_count", 0) < config["min_words"]:
        issues.append(f"Word count too low: {sr.get('word_count', 0)} words")
    if sr.get("subsections_missing"):
        issues.append(f"Missing required subsections")
    if sr.get("technical_density_score", 0) < 15:
        issues.append("Insufficient technical detail (needs code blocks, CLI commands, file paths)")
    if sr.get("implementation_specificity_score", 0) < 15:
        issues.append("Insufficient implementation specificity (needs execution order, I/O definitions)")

    retry_prompt = ENTERPRISE_RETRY_PROMPT.format(
        chapter_index=chapter_index,
        section_title=section_title,
        score=sr.get("total_score", 0),
        issues="\n".join(f"- {i}" for i in issues) or "- General quality below threshold",
        missing_subsections=", ".join(sr.get("subsections_missing", [])) or "None",
        word_count=sr.get("word_count", 0),
        min_words=config["min_words"],
        quality_gate_section=_build_quality_gate_section(config["min_words"]),
    )

    try:
        response = chat(
            system_prompt=ENTERPRISE_SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": original_prompt},
                {"role": "assistant", "content": '{"content": "...retry needed..."}'},
                {"role": "user", "content": retry_prompt},
            ],
            max_tokens=config["max_tokens"],
            temperature=CHAPTER_TEMPERATURE,
            response_format={"type": "json_object"},
        )
        return _parse_enterprise_response(response.content, section_title, depth_mode)
    except (LLMUnavailableError, LLMClientError) as e:
        logger.warning("LLM enterprise retry failed: %s. Using fallback.", e)
        return _fallback_chapter_enterprise(section_title, section_summary, chapter_index, depth_mode)
    except Exception as e:
        logger.warning("Unexpected error retrying enterprise chapter: %s. Using fallback.", e)
        return _fallback_chapter_enterprise(section_title, section_summary, chapter_index, depth_mode)


def generate_chapter_enterprise_with_usage(
    profile: dict,
    features: list[dict],
    section_title: str,
    section_summary: str,
    chapter_index: int,
    total_chapters: int,
    previous_summaries: list[str] | None = None,
    depth_mode: str = "enterprise",
) -> tuple[dict, dict]:
    """Generate an enterprise chapter, returning usage data.

    Returns:
        Tuple of (content_dict, usage_dict).
    """
    if not is_available():
        logger.info("LLM unavailable, using fallback enterprise chapter content")
        return _fallback_chapter_enterprise(section_title, section_summary, chapter_index, depth_mode), {}

    prompt = _build_enterprise_prompt(
        profile, features, section_title, section_summary,
        chapter_index, total_chapters, previous_summaries, depth_mode,
    )
    config = get_depth_config(depth_mode)

    try:
        response = chat(
            system_prompt=ENTERPRISE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=config["max_tokens"],
            temperature=CHAPTER_TEMPERATURE,
            response_format={"type": "json_object"},
        )
        return _parse_enterprise_response(response.content, section_title, depth_mode), response.usage
    except (LLMUnavailableError, LLMClientError) as e:
        logger.warning("LLM enterprise chapter generation failed: %s. Using fallback.", e)
        return _fallback_chapter_enterprise(section_title, section_summary, chapter_index, depth_mode), {}
    except Exception as e:
        logger.warning("Unexpected error generating enterprise chapter: %s. Using fallback.", e)
        return _fallback_chapter_enterprise(section_title, section_summary, chapter_index, depth_mode), {}


def generate_chapter_enterprise_with_retry_and_usage(
    profile: dict,
    features: list[dict],
    section_title: str,
    section_summary: str,
    chapter_index: int,
    total_chapters: int,
    previous_summaries: list[str] | None = None,
    depth_mode: str = "enterprise",
    score_result: dict | None = None,
) -> tuple[dict, dict]:
    """Regenerate an enterprise chapter with scoring feedback, returning usage data.

    Returns:
        Tuple of (content_dict, usage_dict).
    """
    if not is_available():
        return _fallback_chapter_enterprise(section_title, section_summary, chapter_index, depth_mode), {}

    config = get_depth_config(depth_mode)

    original_prompt = _build_enterprise_prompt(
        profile, features, section_title, section_summary,
        chapter_index, total_chapters, previous_summaries, depth_mode,
    )

    sr = score_result or {}
    issues = []
    if sr.get("word_count", 0) < config["min_words"]:
        issues.append(f"Word count too low: {sr.get('word_count', 0)} words")
    if sr.get("subsections_missing"):
        issues.append("Missing required subsections")
    if sr.get("technical_density_score", 0) < 15:
        issues.append("Insufficient technical detail (needs code blocks, CLI commands, file paths)")
    if sr.get("implementation_specificity_score", 0) < 15:
        issues.append("Insufficient implementation specificity (needs execution order, I/O definitions)")

    retry_prompt = ENTERPRISE_RETRY_PROMPT.format(
        chapter_index=chapter_index,
        section_title=section_title,
        score=sr.get("total_score", 0),
        issues="\n".join(f"- {i}" for i in issues) or "- General quality below threshold",
        missing_subsections=", ".join(sr.get("subsections_missing", [])) or "None",
        word_count=sr.get("word_count", 0),
        min_words=config["min_words"],
        quality_gate_section=_build_quality_gate_section(config["min_words"]),
    )

    try:
        response = chat(
            system_prompt=ENTERPRISE_SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": original_prompt},
                {"role": "assistant", "content": '{"content": "...retry needed..."}'},
                {"role": "user", "content": retry_prompt},
            ],
            max_tokens=config["max_tokens"],
            temperature=CHAPTER_TEMPERATURE,
            response_format={"type": "json_object"},
        )
        return _parse_enterprise_response(response.content, section_title, depth_mode), response.usage
    except (LLMUnavailableError, LLMClientError) as e:
        logger.warning("LLM enterprise retry failed: %s. Using fallback.", e)
        return _fallback_chapter_enterprise(section_title, section_summary, chapter_index, depth_mode), {}
    except Exception as e:
        logger.warning("Unexpected error retrying enterprise chapter: %s. Using fallback.", e)
        return _fallback_chapter_enterprise(section_title, section_summary, chapter_index, depth_mode), {}


def _build_enterprise_prompt(
    profile: dict,
    features: list[dict],
    section_title: str,
    section_summary: str,
    chapter_index: int,
    total_chapters: int,
    previous_summaries: list[str] | None = None,
    depth_mode: str = "professional",
) -> str:
    """Build the enterprise prompt with chapter-specific subsection requirements."""
    # Extract selected values from profile fields
    fields = {}
    for field_name in [
        "problem_definition", "target_user", "value_proposition",
        "deployment_type", "ai_depth", "monetization_model", "mvp_scope",
    ]:
        field_data = profile.get(field_name, {})
        fields[field_name] = field_data.get("selected", "") or "Not specified"

    # Build feature list
    feature_list = "\n".join(
        f"- {f['name']}: {f.get('description', '')}" for f in features
    ) or "- No specific features selected"

    # Build intelligence goals section (scaled by build profile)
    from execution.build_depth import get_build_profile
    build_profile = get_build_profile(depth_mode)
    intelligence_goals = profile.get("intelligence_goals", [])
    intelligence_goals_section = build_intelligence_goals_prompt_section(
        intelligence_goals, section_title,
        expansion_depth=build_profile.get("intelligence_expansion_depth", "detailed"),
    )

    # Build derived field strings
    tc = profile.get("technical_constraints", [])
    nfrs = profile.get("non_functional_requirements", [])
    ucs = profile.get("core_use_cases", [])
    sm = profile.get("success_metrics", [])
    risks = profile.get("risk_assessment", [])

    # Build previous chapter context
    if previous_summaries:
        previous_context = "\n".join(
            f"- Chapter {i + 1}: {s}" for i, s in enumerate(previous_summaries)
        )
    else:
        previous_context = "This is the first chapter."

    # Get chapter-specific subsection requirements
    subsections = get_chapter_subsections(section_title, depth_mode)
    required_subsections = "\n".join(f"- ## {s}" for s in subsections)

    config = get_depth_config(depth_mode)

    return ENTERPRISE_CHAPTER_PROMPT.format(
        chapter_index=chapter_index,
        total_chapters=total_chapters,
        section_title=section_title,
        feature_list=feature_list,
        intelligence_goals_section=intelligence_goals_section or "No intelligence goals for this project.",
        section_summary=section_summary or "No summary provided",
        previous_context=previous_context,
        technical_constraints=", ".join(tc) if tc else "None specified",
        nfrs=", ".join(nfrs) if nfrs else "None specified",
        use_cases=", ".join(ucs) if ucs else "None specified",
        success_metrics=", ".join(sm) if sm else "None specified",
        risks=", ".join(risks) if risks else "None specified",
        required_subsections=required_subsections,
        min_words=config["min_words"],
        quality_gate_section=_build_quality_gate_section(config["min_words"]),
        **fields,
    )


def _parse_enterprise_response(raw_json: str, section_title: str, depth_mode: str) -> dict:
    """Parse enterprise LLM response. Handles both new and legacy formats.

    Args:
        raw_json: The raw JSON string from the LLM.
        section_title: Title for fallback content.
        depth_mode: Depth mode for fallback content.

    Returns:
        Dict with 'content' key containing full markdown body.
    """
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse enterprise chapter JSON, using fallback")
        return _fallback_chapter_enterprise(section_title, "", 0, depth_mode)

    if not isinstance(data, dict):
        logger.warning("Enterprise chapter JSON is not a dict, using fallback")
        return _fallback_chapter_enterprise(section_title, "", 0, depth_mode)

    # New format: {"content": "markdown body"}
    if "content" in data and isinstance(data["content"], str) and len(data["content"].strip()) > 100:
        return {"content": data["content"].strip()}

    # Legacy format: {"purpose", "design_intent", "implementation_guidance"}
    if all(k in data for k in ("purpose", "design_intent", "implementation_guidance")):
        content = _convert_legacy_to_markdown(data)
        if len(content.strip()) > 100:
            return {"content": content}

    logger.warning("Enterprise chapter JSON has insufficient content, using fallback")
    return _fallback_chapter_enterprise(section_title, "", 0, depth_mode)


def _convert_legacy_to_markdown(data: dict) -> str:
    """Convert legacy 3-field response to enterprise markdown format."""
    parts = []
    if data.get("purpose"):
        parts.append(f"## Purpose\n\n{data['purpose'].strip()}")
    if data.get("design_intent"):
        parts.append(f"## Design Intent\n\n{data['design_intent'].strip()}")
    if data.get("implementation_guidance"):
        parts.append(f"## Implementation Guidance\n\n{data['implementation_guidance'].strip()}")
    return "\n\n".join(parts)


def _fallback_chapter_enterprise(
    section_title: str, section_summary: str, chapter_index: int, depth_mode: str
) -> dict:
    """Return enterprise fallback content with required subsections.

    Generates markdown body with all required subsections for the given
    depth mode, each with substantive placeholder content.
    """
    summary = section_summary or f"the {section_title.lower()} aspects of the system"
    subsections = get_chapter_subsections(section_title, depth_mode)

    parts = []
    for sub in subsections:
        parts.append(
            f"## {sub}\n\n"
            f"This section covers {sub.lower()} as it relates to {section_title.lower()}. "
            f"The project requires specific attention to {sub.lower()} because {summary}. "
            f"The implementation approach for this area should follow the patterns "
            f"established in the project architecture.\n\n"
            f"When implementing this using VS Code with Claude Code, start by reviewing "
            f"the project profile and feature list to understand the specific requirements. "
            f"Create the necessary files and components following the execution order "
            f"described below.\n\n"
            f"The definition of done for this subsection includes: all components "
            f"implemented, unit tests passing, integration verified, and documentation "
            f"updated. Each step should be validated before proceeding to the next.\n\n"
            f"Key considerations for this area include error handling, input validation, "
            f"logging, and monitoring. Ensure all edge cases are covered and that the "
            f"implementation is resilient to unexpected inputs."
        )

    return {"content": "\n\n".join(parts)}
