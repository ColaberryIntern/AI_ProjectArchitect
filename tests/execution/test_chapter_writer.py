"""Tests for execution/chapter_writer.py."""

import json
from unittest.mock import patch

import pytest

from execution.chapter_writer import (
    CHAPTER_SYSTEM_PROMPT,
    CHAPTER_TEMPERATURE,
    CHAPTER_USER_PROMPT,
    ENTERPRISE_SYSTEM_PROMPT,
    _build_enterprise_prompt,
    _build_prompt,
    _build_quality_gate_section,
    _convert_legacy_to_markdown,
    _fallback_chapter,
    _fallback_chapter_enterprise,
    _parse_chapter_response,
    _parse_enterprise_response,
    generate_chapter,
    generate_chapter_enterprise,
    generate_chapter_enterprise_with_retry,
    generate_chapter_with_retry,
)
from execution.quality_gate_runner import (
    check_anti_vagueness,
    check_build_readiness,
    check_clarity,
    check_completeness,
    run_chapter_gates,
)
from execution.template_renderer import render_chapter


@pytest.fixture
def sample_profile():
    """A minimal project profile for testing."""
    return {
        "problem_definition": {"selected": "Manual planning is slow", "confirmed": True},
        "target_user": {"selected": "Non-technical PMs", "confirmed": True},
        "value_proposition": {"selected": "Automate requirements", "confirmed": True},
        "deployment_type": {"selected": "SaaS multi-tenant", "confirmed": True},
        "ai_depth": {"selected": "AI-assisted", "confirmed": True},
        "monetization_model": {"selected": "Freemium SaaS", "confirmed": True},
        "mvp_scope": {"selected": "Core features only", "confirmed": True},
        "technical_constraints": ["Python 3.11+", "PostgreSQL"],
        "non_functional_requirements": ["Sub-2s response", "99.9% uptime"],
        "core_use_cases": ["Create project", "Generate requirements"],
    }


@pytest.fixture
def sample_features():
    """Sample feature list for testing."""
    return [
        {"name": "AI Requirements Extractor", "description": "Extract requirements from text"},
        {"name": "Project Dashboard", "description": "Central hub for project status"},
    ]


def _make_valid_llm_response():
    """Create a valid LLM JSON response for chapter content."""
    return json.dumps({
        "purpose": (
            "This chapter defines the executive summary of the system. "
            "The purpose of this section is to provide a high-level overview "
            "of the entire project and its goals. This chapter exists because "
            "stakeholders need a concise understanding of what the system does "
            "before diving into technical details. The system targets non-technical "
            "project managers who need automated requirements planning. "
            "This chapter establishes the foundation for all subsequent chapters "
            "by defining the core vision and value proposition."
        ),
        "design_intent": (
            "This approach was chosen to provide clarity to all stakeholders "
            "from the very first chapter. The tradeoff was between a detailed "
            "technical overview versus a business-focused summary. The decision "
            "was to lead with business value because the target users are "
            "non-technical PMs who need to understand the system's purpose "
            "before reviewing technical architecture. Alternative approaches "
            "included starting with the technical stack, but this was rejected "
            "because it would alienate the primary audience."
        ),
        "implementation_guidance": (
            "First, review the project profile to understand the core problem "
            "and target user. The input is the confirmed project profile from "
            "the idea intake phase.\n\n"
            "Then, open VS Code and use Claude Code to create a summary document "
            "that captures the key points from the profile.\n\n"
            "Next, validate that the summary accurately reflects the selected "
            "features and deployment model.\n\n"
            "Step 1: Extract the problem definition and value proposition.\n"
            "Step 2: Map features to user needs.\n"
            "Step 3: Define the success criteria.\n\n"
            "The output is a clear executive summary that can be shared with "
            "stakeholders. This step depends on the profile being confirmed."
        ),
    })


class TestGenerateChapter:
    """Tests for generate_chapter()."""

    @patch("execution.chapter_writer.is_available", return_value=False)
    def test_fallback_when_llm_unavailable(self, mock_avail, sample_profile, sample_features):
        result = generate_chapter(
            sample_profile, sample_features, "Executive Summary", "Overview of project",
            1, 10,
        )
        assert "purpose" in result
        assert "design_intent" in result
        assert "implementation_guidance" in result

    @patch("execution.chapter_writer.is_available", return_value=True)
    @patch("execution.chapter_writer.chat")
    def test_returns_all_three_fields(self, mock_chat, mock_avail, sample_profile, sample_features):
        mock_chat.return_value.content = _make_valid_llm_response()
        result = generate_chapter(
            sample_profile, sample_features, "Executive Summary", "Overview",
            1, 10,
        )
        assert "purpose" in result
        assert "design_intent" in result
        assert "implementation_guidance" in result
        assert len(result["purpose"]) > 50
        assert len(result["design_intent"]) > 50
        assert len(result["implementation_guidance"]) > 50

    @patch("execution.chapter_writer.is_available", return_value=True)
    @patch("execution.chapter_writer.chat", side_effect=Exception("API down"))
    def test_exception_returns_fallback(self, mock_chat, mock_avail, sample_profile, sample_features):
        result = generate_chapter(
            sample_profile, sample_features, "Executive Summary", "Overview",
            1, 10,
        )
        assert "purpose" in result
        assert "design_intent" in result
        assert "implementation_guidance" in result

    @patch("execution.chapter_writer.is_available", return_value=True)
    @patch("execution.chapter_writer.chat")
    def test_previous_summaries_included(self, mock_chat, mock_avail, sample_profile, sample_features):
        mock_chat.return_value.content = _make_valid_llm_response()
        generate_chapter(
            sample_profile, sample_features, "Architecture", "Tech stack",
            3, 10, previous_summaries=["Exec summary overview", "Problem context"],
        )
        call_args = mock_chat.call_args
        prompt_text = call_args[1]["messages"][0]["content"]
        assert "Chapter 1:" in prompt_text
        assert "Chapter 2:" in prompt_text

    def test_empty_profile_handled(self, sample_features):
        result = generate_chapter(
            {}, sample_features, "Executive Summary", "Overview", 1, 10,
        )
        assert "purpose" in result


class TestGenerateChapterWithRetry:
    """Tests for generate_chapter_with_retry()."""

    @patch("execution.chapter_writer.is_available", return_value=True)
    @patch("execution.chapter_writer.chat")
    def test_retry_includes_gate_failures_in_prompt(self, mock_chat, mock_avail, sample_profile, sample_features):
        mock_chat.return_value.content = _make_valid_llm_response()
        generate_chapter_with_retry(
            sample_profile, sample_features, "Executive Summary", "Overview",
            1, 10, gate_failures=["Missing required element: 'purpose'", "Too short"],
        )
        call_args = mock_chat.call_args
        messages = call_args[1]["messages"]
        # Should have 3 messages: original prompt, placeholder, retry prompt
        assert len(messages) == 3
        retry_text = messages[2]["content"]
        assert "Missing required element" in retry_text
        assert "Too short" in retry_text

    @patch("execution.chapter_writer.is_available", return_value=False)
    def test_fallback_when_llm_unavailable(self, mock_avail, sample_profile, sample_features):
        result = generate_chapter_with_retry(
            sample_profile, sample_features, "Architecture", "Tech stack",
            1, 10, gate_failures=["Some issue"],
        )
        assert "purpose" in result

    @patch("execution.chapter_writer.is_available", return_value=True)
    @patch("execution.chapter_writer.chat")
    def test_retry_without_failures_still_works(self, mock_chat, mock_avail, sample_profile, sample_features):
        mock_chat.return_value.content = _make_valid_llm_response()
        result = generate_chapter_with_retry(
            sample_profile, sample_features, "Executive Summary", "Overview",
            1, 10, gate_failures=None,
        )
        assert "purpose" in result


class TestFallbackChapter:
    """Tests for _fallback_chapter()."""

    def test_has_purpose_field(self):
        result = _fallback_chapter("Executive Summary", "Overview", 1)
        assert "purpose" in result
        assert len(result["purpose"]) > 100

    def test_has_design_intent_field(self):
        result = _fallback_chapter("Architecture", "Tech stack", 2)
        assert "design_intent" in result
        assert len(result["design_intent"]) > 100

    def test_has_implementation_guidance_field(self):
        result = _fallback_chapter("Functional Requirements", "Features", 3)
        assert "implementation_guidance" in result
        assert len(result["implementation_guidance"]) > 100

    def test_references_section_title(self):
        result = _fallback_chapter("Security & Compliance", "Auth and privacy", 8)
        assert "security & compliance" in result["purpose"].lower()

    def test_references_vs_code(self):
        result = _fallback_chapter("Architecture", "Tech stack", 2)
        combined = " ".join(result.values()).lower()
        assert "vs code" in combined or "claude code" in combined

    def test_content_passes_completeness_gate(self):
        result = _fallback_chapter("Executive Summary", "Overview", 1)
        rendered = render_chapter(1, "Executive Summary", result["purpose"],
                                  result["design_intent"], result["implementation_guidance"])
        gate = check_completeness(rendered, "Executive Summary")
        assert gate["passed"], f"Completeness failed: {gate['issues']}"

    def test_content_passes_clarity_gate(self):
        result = _fallback_chapter("Architecture", "Tech stack", 2)
        rendered = render_chapter(2, "Architecture", result["purpose"],
                                  result["design_intent"], result["implementation_guidance"])
        gate = check_clarity(rendered)
        assert gate["passed"], f"Clarity failed: {gate['issues']}"

    def test_content_passes_build_readiness_gate(self):
        result = _fallback_chapter("Functional Requirements", "Features", 3)
        rendered = render_chapter(3, "Functional Requirements", result["purpose"],
                                  result["design_intent"], result["implementation_guidance"])
        gate = check_build_readiness(rendered)
        assert gate["passed"], f"Build readiness failed: {gate['issues']}"

    def test_content_passes_anti_vagueness_gate(self):
        result = _fallback_chapter("Architecture", "Tech stack", 2)
        rendered = render_chapter(2, "Architecture", result["purpose"],
                                  result["design_intent"], result["implementation_guidance"])
        gate = check_anti_vagueness(rendered)
        assert gate["passed"], f"Anti-vagueness failed: {gate['flagged_phrases']}"

    def test_content_passes_all_chapter_gates(self):
        result = _fallback_chapter("Executive Summary", "Overview", 1)
        rendered = render_chapter(1, "Executive Summary", result["purpose"],
                                  result["design_intent"], result["implementation_guidance"])
        gates = run_chapter_gates(rendered, "Executive Summary")
        assert gates["all_passed"], f"Gates failed: {gates}"


class TestParseChapterResponse:
    """Tests for _parse_chapter_response()."""

    def test_parse_valid_json(self):
        raw = _make_valid_llm_response()
        result = _parse_chapter_response(raw, "Executive Summary")
        assert "purpose" in result
        assert "design_intent" in result
        assert "implementation_guidance" in result

    def test_parse_invalid_json_returns_fallback(self):
        result = _parse_chapter_response("not json at all", "Architecture")
        assert "purpose" in result
        assert "design_intent" in result

    def test_parse_empty_string_returns_fallback(self):
        result = _parse_chapter_response("", "Architecture")
        assert "purpose" in result

    def test_parse_missing_field_returns_fallback(self):
        raw = json.dumps({"purpose": "x" * 60, "design_intent": "y" * 60})
        result = _parse_chapter_response(raw, "Architecture")
        # Should fallback because implementation_guidance is missing
        assert "implementation_guidance" in result

    def test_parse_short_field_returns_fallback(self):
        raw = json.dumps({
            "purpose": "Too short",
            "design_intent": "Also short",
            "implementation_guidance": "Nope",
        })
        result = _parse_chapter_response(raw, "Architecture")
        # Should fallback because fields are < 50 chars
        assert len(result["purpose"]) > 50

    def test_parse_non_dict_returns_fallback(self):
        raw = json.dumps(["not", "a", "dict"])
        result = _parse_chapter_response(raw, "Architecture")
        assert "purpose" in result


class TestBuildPrompt:
    """Tests for _build_prompt()."""

    def test_includes_profile_fields(self, sample_profile, sample_features):
        prompt = _build_prompt(
            sample_profile, sample_features, "Executive Summary", "Overview",
            1, 10,
        )
        assert "Manual planning is slow" in prompt
        assert "Non-technical PMs" in prompt
        assert "SaaS multi-tenant" in prompt

    def test_includes_features(self, sample_profile, sample_features):
        prompt = _build_prompt(
            sample_profile, sample_features, "Features", "Feature details",
            4, 10,
        )
        assert "AI Requirements Extractor" in prompt
        assert "Project Dashboard" in prompt

    def test_includes_section_summary(self, sample_profile, sample_features):
        prompt = _build_prompt(
            sample_profile, sample_features, "Architecture", "Full tech stack overview",
            7, 10,
        )
        assert "Full tech stack overview" in prompt

    def test_includes_previous_summaries(self, sample_profile, sample_features):
        prompt = _build_prompt(
            sample_profile, sample_features, "Architecture", "Tech",
            3, 10, previous_summaries=["First chapter purpose", "Second chapter"],
        )
        assert "Chapter 1: First chapter purpose" in prompt
        assert "Chapter 2: Second chapter" in prompt

    def test_first_chapter_has_no_previous(self, sample_profile, sample_features):
        prompt = _build_prompt(
            sample_profile, sample_features, "Executive Summary", "Overview",
            1, 10,
        )
        assert "This is the first chapter" in prompt

    def test_vs_code_claude_code_in_system_prompt(self):
        assert "VS Code" in CHAPTER_SYSTEM_PROMPT
        assert "Claude Code" in CHAPTER_SYSTEM_PROMPT

    def test_vs_code_claude_code_in_user_prompt(self):
        assert "VS Code with Claude Code" in CHAPTER_USER_PROMPT

    def test_empty_profile_uses_defaults(self, sample_features):
        prompt = _build_prompt(
            {}, sample_features, "Executive Summary", "Overview",
            1, 10,
        )
        assert "Not specified" in prompt

    def test_includes_quality_gate_requirements(self, sample_profile, sample_features):
        prompt = _build_prompt(
            sample_profile, sample_features, "Executive Summary", "Overview",
            1, 10,
        )
        assert "QUALITY GATE REQUIREMENTS" in prompt
        assert "Anti-Vagueness" in prompt
        assert "handle edge cases" in prompt
        assert "Completeness Gate" in prompt
        assert "Build Readiness Gate" in prompt


class TestChapterTemperature:
    """Tests for CHAPTER_TEMPERATURE constant."""

    def test_temperature_is_low(self):
        assert CHAPTER_TEMPERATURE == 0.2

    @patch("execution.chapter_writer.is_available", return_value=True)
    @patch("execution.chapter_writer.chat")
    def test_generate_chapter_uses_low_temperature(self, mock_chat, mock_avail, sample_profile, sample_features):
        mock_chat.return_value.content = _make_valid_llm_response()
        generate_chapter(
            sample_profile, sample_features, "Executive Summary", "Overview", 1, 10,
        )
        call_args = mock_chat.call_args
        assert call_args[1]["temperature"] == 0.2

    @patch("execution.chapter_writer.is_available", return_value=True)
    @patch("execution.chapter_writer.chat")
    def test_generate_chapter_enterprise_uses_low_temperature(self, mock_chat, mock_avail, sample_profile, sample_features):
        mock_chat.return_value.content = _make_valid_enterprise_response()
        generate_chapter_enterprise(
            sample_profile, sample_features, "Executive Summary", "Overview", 1, 10,
        )
        call_args = mock_chat.call_args
        assert call_args[1]["temperature"] == 0.2


class TestQualityGateSection:
    """Tests for _build_quality_gate_section()."""

    def test_includes_completeness_gate(self):
        section = _build_quality_gate_section()
        assert "Completeness Gate" in section
        assert "placeholder" in section.lower()

    def test_includes_scoring_dimensions(self):
        section = _build_quality_gate_section()
        assert "SCORING DIMENSIONS" in section
        assert "Word Count" in section
        assert "Technical Density" in section
        assert "Implementation Specificity" in section
        assert "Subsection Coverage" in section

    def test_includes_anti_vagueness_gate(self):
        section = _build_quality_gate_section()
        assert "Anti-Vagueness" in section
        assert "handle edge cases" in section
        assert "use best practices" in section

    def test_includes_build_readiness_gate(self):
        section = _build_quality_gate_section()
        assert "Build Readiness" in section
        assert "first" in section
        assert "then" in section

    def test_includes_clarity_gate(self):
        section = _build_quality_gate_section()
        assert "Clarity Gate" in section
        assert "this chapter" in section

    def test_dynamically_reads_forbidden_phrases(self):
        from execution.ambiguity_detector import FORBIDDEN_PHRASES
        section = _build_quality_gate_section()
        for pattern in FORBIDDEN_PHRASES:
            # Check that each forbidden phrase appears in the section
            clean = pattern.replace(r"\.", ".").replace(r"\b", "")
            assert clean in section, f"Missing forbidden phrase: {clean}"


# ---------------------------------------------------------------------------
# Enterprise Chapter Generation
# ---------------------------------------------------------------------------


def _make_valid_enterprise_response(subsections=None):
    """Create a valid enterprise LLM JSON response."""
    subs = subsections or [
        "Vision & Strategy", "Business Model", "Competitive Landscape",
        "Market Size Context", "Risk Summary", "Technical High-Level Architecture",
        "Deployment Model", "Assumptions & Constraints",
    ]
    parts = []
    for sub in subs:
        parts.append(
            f"## {sub}\n\n"
            f"This section covers {sub.lower()} in detail with specific implementation "
            f"guidance for the project. The approach uses VS Code with Claude Code "
            f"to implement the required components.\n\n"
            f"First, create the necessary configuration files. Then, implement the "
            f"core logic in the `src/` directory. Next, add unit tests to verify "
            f"the behavior. The output should include complete file structures, "
            f"environment variables like `DATABASE_URL`, and CLI commands such as "
            f"`npm install` and `python manage.py migrate`.\n\n"
            f"Key considerations include error handling for network failures, "
            f"input validation for user-submitted data, and monitoring via "
            f"structured logging. Each component depends on the base configuration "
            f"being in place before implementation begins."
        )
    content = "\n\n".join(parts)
    return json.dumps({"content": content})


class TestGenerateChapterEnterprise:
    """Tests for generate_chapter_enterprise()."""

    @patch("execution.chapter_writer.is_available", return_value=False)
    def test_fallback_when_llm_unavailable(self, mock_avail, sample_profile, sample_features):
        result = generate_chapter_enterprise(
            sample_profile, sample_features, "Executive Summary", "Overview",
            1, 10,
        )
        assert "content" in result
        assert len(result["content"]) > 100

    @patch("execution.chapter_writer.is_available", return_value=True)
    @patch("execution.chapter_writer.chat")
    def test_returns_content_field(self, mock_chat, mock_avail, sample_profile, sample_features):
        mock_chat.return_value.content = _make_valid_enterprise_response()
        result = generate_chapter_enterprise(
            sample_profile, sample_features, "Executive Summary", "Overview",
            1, 10,
        )
        assert "content" in result
        assert "Vision & Strategy" in result["content"]

    @patch("execution.chapter_writer.is_available", return_value=True)
    @patch("execution.chapter_writer.chat")
    def test_uses_depth_mode_max_tokens(self, mock_chat, mock_avail, sample_profile, sample_features):
        mock_chat.return_value.content = _make_valid_enterprise_response()
        generate_chapter_enterprise(
            sample_profile, sample_features, "Executive Summary", "Overview",
            1, 10, depth_mode="architect",
        )
        call_args = mock_chat.call_args
        assert call_args[1]["max_tokens"] == 16384

    @patch("execution.chapter_writer.is_available", return_value=True)
    @patch("execution.chapter_writer.chat")
    def test_lite_mode_uses_4096_tokens(self, mock_chat, mock_avail, sample_profile, sample_features):
        mock_chat.return_value.content = _make_valid_enterprise_response(["Vision & Strategy", "Business Model"])
        generate_chapter_enterprise(
            sample_profile, sample_features, "Executive Summary", "Overview",
            1, 10, depth_mode="lite",
        )
        call_args = mock_chat.call_args
        assert call_args[1]["max_tokens"] == 4096

    @patch("execution.chapter_writer.is_available", return_value=True)
    @patch("execution.chapter_writer.chat", side_effect=Exception("API down"))
    def test_exception_returns_fallback(self, mock_chat, mock_avail, sample_profile, sample_features):
        result = generate_chapter_enterprise(
            sample_profile, sample_features, "Executive Summary", "Overview",
            1, 10,
        )
        assert "content" in result
        assert len(result["content"]) > 100

    @patch("execution.chapter_writer.is_available", return_value=True)
    @patch("execution.chapter_writer.chat")
    def test_enterprise_system_prompt_used(self, mock_chat, mock_avail, sample_profile, sample_features):
        mock_chat.return_value.content = _make_valid_enterprise_response()
        generate_chapter_enterprise(
            sample_profile, sample_features, "Executive Summary", "Overview",
            1, 10,
        )
        call_args = mock_chat.call_args
        assert call_args[1]["system_prompt"] == ENTERPRISE_SYSTEM_PROMPT


class TestGenerateChapterEnterpriseWithRetry:
    """Tests for generate_chapter_enterprise_with_retry()."""

    @patch("execution.chapter_writer.is_available", return_value=True)
    @patch("execution.chapter_writer.chat")
    def test_retry_includes_score_feedback(self, mock_chat, mock_avail, sample_profile, sample_features):
        mock_chat.return_value.content = _make_valid_enterprise_response()
        score_result = {
            "total_score": 55,
            "word_count": 1200,
            "subsections_missing": ["Risk Summary", "Deployment Model"],
            "technical_density_score": 10,
            "implementation_specificity_score": 12,
        }
        generate_chapter_enterprise_with_retry(
            sample_profile, sample_features, "Executive Summary", "Overview",
            1, 10, score_result=score_result,
        )
        call_args = mock_chat.call_args
        messages = call_args[1]["messages"]
        assert len(messages) == 3
        retry_text = messages[2]["content"]
        assert "55/100" in retry_text
        assert "Risk Summary" in retry_text
        assert "1200" in retry_text

    @patch("execution.chapter_writer.is_available", return_value=False)
    def test_fallback_when_llm_unavailable(self, mock_avail, sample_profile, sample_features):
        result = generate_chapter_enterprise_with_retry(
            sample_profile, sample_features, "Architecture", "Tech",
            1, 10, score_result={"total_score": 30},
        )
        assert "content" in result

    @patch("execution.chapter_writer.is_available", return_value=True)
    @patch("execution.chapter_writer.chat")
    def test_retry_without_score_still_works(self, mock_chat, mock_avail, sample_profile, sample_features):
        mock_chat.return_value.content = _make_valid_enterprise_response()
        result = generate_chapter_enterprise_with_retry(
            sample_profile, sample_features, "Executive Summary", "Overview",
            1, 10, score_result=None,
        )
        assert "content" in result


class TestBuildEnterprisePrompt:
    """Tests for _build_enterprise_prompt()."""

    def test_includes_profile_fields(self, sample_profile, sample_features):
        prompt = _build_enterprise_prompt(
            sample_profile, sample_features, "Executive Summary", "Overview",
            1, 10,
        )
        assert "Manual planning is slow" in prompt
        assert "Non-technical PMs" in prompt

    def test_includes_required_subsections(self, sample_profile, sample_features):
        prompt = _build_enterprise_prompt(
            sample_profile, sample_features, "Executive Summary", "Overview",
            1, 10, depth_mode="enterprise",
        )
        assert "Vision & Strategy" in prompt
        assert "Business Model" in prompt
        assert "Competitive Landscape" in prompt

    def test_includes_min_words(self, sample_profile, sample_features):
        prompt = _build_enterprise_prompt(
            sample_profile, sample_features, "Executive Summary", "Overview",
            1, 10, depth_mode="enterprise",
        )
        assert "7000" in prompt  # enterprise min_words

    def test_lite_mode_has_fewer_subsections(self, sample_profile, sample_features):
        enterprise = _build_enterprise_prompt(
            sample_profile, sample_features, "Executive Summary", "Overview",
            1, 10, depth_mode="enterprise",
        )
        lite = _build_enterprise_prompt(
            sample_profile, sample_features, "Executive Summary", "Overview",
            1, 10, depth_mode="lite",
        )
        assert enterprise.count("## ") > lite.count("## ")

    def test_includes_success_metrics(self, sample_profile, sample_features):
        sample_profile["success_metrics"] = ["50% faster planning"]
        prompt = _build_enterprise_prompt(
            sample_profile, sample_features, "Executive Summary", "Overview",
            1, 10,
        )
        assert "50% faster planning" in prompt

    def test_includes_risks(self, sample_profile, sample_features):
        sample_profile["risk_assessment"] = ["LLM dependency"]
        prompt = _build_enterprise_prompt(
            sample_profile, sample_features, "Executive Summary", "Overview",
            1, 10,
        )
        assert "LLM dependency" in prompt

    def test_vs_code_claude_code_in_enterprise_system_prompt(self):
        assert "VS Code" in ENTERPRISE_SYSTEM_PROMPT
        assert "Claude Code" in ENTERPRISE_SYSTEM_PROMPT

    def test_includes_quality_gate_requirements(self, sample_profile, sample_features):
        prompt = _build_enterprise_prompt(
            sample_profile, sample_features, "Executive Summary", "Overview",
            1, 10,
        )
        assert "QUALITY GATE REQUIREMENTS" in prompt
        assert "Anti-Vagueness" in prompt
        assert "handle edge cases" in prompt


class TestParseEnterpriseResponse:
    """Tests for _parse_enterprise_response()."""

    def test_parse_valid_content_format(self):
        raw = _make_valid_enterprise_response()
        result = _parse_enterprise_response(raw, "Executive Summary", "enterprise")
        assert "content" in result
        assert "Vision & Strategy" in result["content"]

    def test_parse_legacy_format_converts(self):
        raw = _make_valid_llm_response()
        result = _parse_enterprise_response(raw, "Executive Summary", "enterprise")
        assert "content" in result
        assert "Purpose" in result["content"]
        assert "Design Intent" in result["content"]
        assert "Implementation Guidance" in result["content"]

    def test_parse_invalid_json_returns_fallback(self):
        result = _parse_enterprise_response("not json", "Architecture", "enterprise")
        assert "content" in result
        assert len(result["content"]) > 100

    def test_parse_short_content_returns_fallback(self):
        raw = json.dumps({"content": "too short"})
        result = _parse_enterprise_response(raw, "Architecture", "enterprise")
        assert "content" in result
        assert len(result["content"]) > 100

    def test_parse_non_dict_returns_fallback(self):
        raw = json.dumps(["not", "a", "dict"])
        result = _parse_enterprise_response(raw, "Architecture", "enterprise")
        assert "content" in result


class TestConvertLegacyToMarkdown:
    """Tests for _convert_legacy_to_markdown()."""

    def test_converts_all_three_fields(self):
        data = {
            "purpose": "The purpose of this chapter.",
            "design_intent": "The design approach.",
            "implementation_guidance": "Steps to implement.",
        }
        result = _convert_legacy_to_markdown(data)
        assert "## Purpose" in result
        assert "## Design Intent" in result
        assert "## Implementation Guidance" in result

    def test_handles_missing_fields(self):
        result = _convert_legacy_to_markdown({"purpose": "Only purpose."})
        assert "## Purpose" in result
        assert "## Design Intent" not in result


class TestFallbackChapterEnterprise:
    """Tests for _fallback_chapter_enterprise()."""

    def test_returns_content_field(self):
        result = _fallback_chapter_enterprise("Executive Summary", "Overview", 1, "enterprise")
        assert "content" in result
        assert len(result["content"]) > 100

    def test_includes_required_subsections(self):
        result = _fallback_chapter_enterprise("Executive Summary", "Overview", 1, "enterprise")
        content = result["content"]
        assert "## Vision & Strategy" in content
        assert "## Business Model" in content

    def test_lite_has_fewer_subsections(self):
        enterprise = _fallback_chapter_enterprise("Executive Summary", "Overview", 1, "enterprise")
        lite = _fallback_chapter_enterprise("Executive Summary", "Overview", 1, "lite")
        assert enterprise["content"].count("## ") > lite["content"].count("## ")

    def test_references_vs_code(self):
        result = _fallback_chapter_enterprise("Architecture", "Tech", 2, "enterprise")
        assert "VS Code" in result["content"] or "Claude Code" in result["content"]


class TestWithUsageFunctions:
    """Tests for _with_usage wrapper functions that return (content, usage) tuples."""

    @patch("execution.chapter_writer.is_available", return_value=False)
    def test_generate_chapter_with_usage_fallback(self, mock_avail, sample_profile, sample_features):
        from execution.chapter_writer import generate_chapter_with_usage
        content, usage = generate_chapter_with_usage(
            sample_profile, sample_features, "Architecture", "System design", 1, 7,
        )
        assert "purpose" in content
        assert usage == {}

    @patch("execution.chapter_writer.is_available", return_value=False)
    def test_generate_chapter_enterprise_with_usage_fallback(self, mock_avail, sample_profile, sample_features):
        from execution.chapter_writer import generate_chapter_enterprise_with_usage
        content, usage = generate_chapter_enterprise_with_usage(
            sample_profile, sample_features, "Architecture", "Tech", 1, 10, depth_mode="enterprise",
        )
        assert "content" in content
        assert usage == {}

    @patch("execution.chapter_writer.is_available", return_value=False)
    def test_generate_chapter_with_retry_and_usage_fallback(self, mock_avail, sample_profile, sample_features):
        from execution.chapter_writer import generate_chapter_with_retry_and_usage
        content, usage = generate_chapter_with_retry_and_usage(
            sample_profile, sample_features, "Architecture", "Tech", 1, 7,
            gate_failures=["Too short"],
        )
        assert "purpose" in content
        assert usage == {}

    @patch("execution.chapter_writer.is_available", return_value=False)
    def test_generate_chapter_enterprise_with_retry_and_usage_fallback(self, mock_avail, sample_profile, sample_features):
        from execution.chapter_writer import generate_chapter_enterprise_with_retry_and_usage
        content, usage = generate_chapter_enterprise_with_retry_and_usage(
            sample_profile, sample_features, "Architecture", "Tech", 1, 10,
            depth_mode="enterprise", score_result={"total_score": 50, "word_count": 100},
        )
        assert "content" in content
        assert usage == {}

    @patch("execution.chapter_writer.chat")
    @patch("execution.chapter_writer.is_available", return_value=True)
    def test_generate_chapter_with_usage_returns_usage(self, mock_avail, mock_chat, sample_profile, sample_features):
        from execution.chapter_writer import generate_chapter_with_usage
        from execution.llm_client import LLMResponse
        import json
        mock_chat.return_value = LLMResponse(
            content=json.dumps({
                "purpose": "x" * 200,
                "design_intent": "y" * 200,
                "implementation_guidance": "z" * 200,
            }),
            model="gpt-4o-mini",
            usage={"prompt_tokens": 500, "completion_tokens": 300},
            stop_reason="stop",
        )
        content, usage = generate_chapter_with_usage(
            sample_profile, sample_features, "Architecture", "Tech", 1, 7,
        )
        assert "purpose" in content
        assert usage["prompt_tokens"] == 500
        assert usage["completion_tokens"] == 300
