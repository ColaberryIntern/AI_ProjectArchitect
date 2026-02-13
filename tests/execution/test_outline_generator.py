"""Tests for the outline generator."""

import json
from unittest.mock import patch

from execution.outline_generator import (
    DEFAULT_SECTIONS,
    ENHANCED_SECTIONS,
    LIGHT_SECTIONS,
    STANDARD_SECTIONS,
    _parse_enhanced_outline_response,
    _parse_outline_response,
    generate_outline,
    generate_outline_from_profile,
    get_sections_for_depth,
)


class TestDefaultSections:
    """Validate the default sections structure."""

    def test_has_exactly_7_sections(self):
        assert len(DEFAULT_SECTIONS) == 7

    def test_all_sections_have_required_fields(self):
        for sec in DEFAULT_SECTIONS:
            assert "index" in sec
            assert "title" in sec
            assert "type" in sec
            assert "summary" in sec

    def test_indices_are_sequential(self):
        indices = [s["index"] for s in DEFAULT_SECTIONS]
        assert indices == list(range(1, 8))


class TestParseOutlineResponse:
    """Test parsing LLM JSON output into sections list."""

    def test_valid_json_parsed(self):
        data = {
            "sections": [
                {"index": i, "title": f"Title {i}", "type": "required", "summary": f"Summary {i}"}
                for i in range(1, 8)
            ]
        }
        result = _parse_outline_response(json.dumps(data))
        assert len(result) == 7
        assert result[0]["title"] == "Title 1"
        assert result[6]["summary"] == "Summary 7"

    def test_invalid_json_returns_defaults(self):
        result = _parse_outline_response("not json")
        assert len(result) == 7
        assert result[0]["title"] == DEFAULT_SECTIONS[0]["title"]
        assert result[0]["summary"] == ""

    def test_missing_sections_key_returns_defaults(self):
        result = _parse_outline_response(json.dumps({"data": []}))
        assert len(result) == 7
        assert result[0]["summary"] == ""

    def test_too_few_sections_returns_defaults(self):
        data = {
            "sections": [
                {"index": 1, "title": "Only One", "summary": "Just one section"}
            ]
        }
        result = _parse_outline_response(json.dumps(data))
        assert len(result) == 7
        assert result[0]["summary"] == ""

    def test_extra_sections_truncated_to_7(self):
        data = {
            "sections": [
                {"index": i, "title": f"Title {i}", "summary": f"Summary {i}"}
                for i in range(1, 10)
            ]
        }
        result = _parse_outline_response(json.dumps(data))
        assert len(result) == 7
        assert result[6]["title"] == "Title 7"

    def test_missing_title_uses_default(self):
        data = {
            "sections": [
                {"index": i, "summary": f"Summary {i}"}
                for i in range(1, 8)
            ]
        }
        result = _parse_outline_response(json.dumps(data))
        assert result[0]["title"] == DEFAULT_SECTIONS[0]["title"]

    def test_missing_summary_uses_empty(self):
        data = {
            "sections": [
                {"index": i, "title": f"Title {i}"}
                for i in range(1, 8)
            ]
        }
        result = _parse_outline_response(json.dumps(data))
        assert result[0]["summary"] == ""

    def test_none_input_returns_defaults(self):
        result = _parse_outline_response(None)
        assert len(result) == 7


class TestGenerateOutline:
    """Test the main generate_outline function."""

    def test_empty_idea_returns_defaults(self):
        result = generate_outline("", [])
        assert len(result) == 7
        assert result[0]["summary"] == ""

    def test_whitespace_idea_returns_defaults(self):
        result = generate_outline("   ", [])
        assert len(result) == 7
        assert result[0]["summary"] == ""

    @patch("execution.outline_generator.is_available", return_value=False)
    def test_llm_unavailable_returns_defaults(self, mock_avail):
        result = generate_outline("Build an AI app", [])
        assert len(result) == 7
        assert result[0]["summary"] == ""

    @patch("execution.outline_generator.chat")
    @patch("execution.outline_generator.is_available", return_value=True)
    def test_llm_success_returns_parsed_outline(self, mock_avail, mock_chat):
        """Valid LLM response returns project-specific sections."""
        sections = [
            {"index": i, "title": f"Title {i}", "type": "required", "summary": f"Summary for {i}"}
            for i in range(1, 8)
        ]

        from execution.llm_client import LLMResponse
        mock_chat.return_value = LLMResponse(
            content=json.dumps({"sections": sections}),
            model="gpt-4o-mini",
            usage={"prompt_tokens": 100, "completion_tokens": 200},
            stop_reason="stop",
        )

        features = [{"name": "Dashboard", "description": "Main dashboard"}]
        result = generate_outline("Build an AI learning platform", features)
        assert len(result) == 7
        assert result[0]["title"] == "Title 1"
        assert result[0]["summary"] == "Summary for 1"

    @patch("execution.outline_generator.chat")
    @patch("execution.outline_generator.is_available", return_value=True)
    def test_llm_error_returns_defaults(self, mock_avail, mock_chat):
        from execution.llm_client import LLMClientError
        mock_chat.side_effect = LLMClientError("API error")

        result = generate_outline("Build an AI app", [])
        assert len(result) == 7
        assert result[0]["summary"] == ""

    def test_returns_deep_copies(self):
        """Each call returns independent copies, not shared references."""
        result1 = generate_outline("", [])
        result2 = generate_outline("", [])
        result1[0]["title"] = "Modified"
        assert result2[0]["title"] == DEFAULT_SECTIONS[0]["title"]


class TestEnhancedSections:
    """Validate the enhanced 10-section structure."""

    def test_has_exactly_10_sections(self):
        assert len(ENHANCED_SECTIONS) == 10

    def test_indices_are_sequential(self):
        indices = [s["index"] for s in ENHANCED_SECTIONS]
        assert indices == list(range(1, 11))


class TestParseEnhancedOutlineResponse:
    """Test parsing 10-section LLM output."""

    def test_valid_json_parsed(self):
        data = {
            "sections": [
                {"index": i, "title": f"Title {i}", "type": "required", "summary": f"Summary {i}"}
                for i in range(1, 11)
            ]
        }
        result = _parse_enhanced_outline_response(json.dumps(data))
        assert len(result) == 10
        assert result[0]["title"] == "Title 1"
        assert result[9]["summary"] == "Summary 10"

    def test_invalid_json_returns_defaults(self):
        result = _parse_enhanced_outline_response("not json")
        assert len(result) == 10
        assert result[0]["title"] == ENHANCED_SECTIONS[0]["title"]

    def test_too_few_sections_returns_defaults(self):
        data = {"sections": [{"index": 1, "title": "Only One", "summary": "short"}]}
        result = _parse_enhanced_outline_response(json.dumps(data))
        assert len(result) == 10

    def test_extra_sections_truncated_to_10(self):
        data = {
            "sections": [
                {"index": i, "title": f"Title {i}", "summary": f"Summary {i}"}
                for i in range(1, 15)
            ]
        }
        result = _parse_enhanced_outline_response(json.dumps(data))
        assert len(result) == 10
        assert result[9]["title"] == "Title 10"


class TestGenerateOutlineFromProfile:
    """Test the profile-driven 10-section outline generator."""

    def _make_profile(self):
        from execution.state_manager import PROFILE_REQUIRED_FIELDS
        profile = {}
        for field in PROFILE_REQUIRED_FIELDS:
            profile[field] = {"selected": f"value_{field}", "confidence": 0.85, "confirmed": True, "options": []}
        profile["technical_constraints"] = ["REST API"]
        profile["non_functional_requirements"] = ["99.9% uptime"]
        profile["success_metrics"] = ["50% faster"]
        profile["risk_assessment"] = ["LLM dependency"]
        profile["core_use_cases"] = ["Submit idea"]
        return profile

    def test_empty_profile_returns_enhanced_defaults(self):
        result = generate_outline_from_profile({}, [])
        assert len(result) == 10
        assert result[0]["summary"] == ""

    @patch("execution.outline_generator.is_available", return_value=False)
    def test_llm_unavailable_returns_enhanced_defaults(self, mock_avail):
        profile = self._make_profile()
        result = generate_outline_from_profile(profile, [])
        assert len(result) == 10
        assert result[0]["title"] == ENHANCED_SECTIONS[0]["title"]

    @patch("execution.outline_generator.chat")
    @patch("execution.outline_generator.is_available", return_value=True)
    def test_llm_success_returns_10_sections(self, mock_avail, mock_chat):
        sections = [
            {"index": i, "title": f"Section {i}", "type": "required", "summary": f"Summary for section {i}"}
            for i in range(1, 11)
        ]
        from execution.llm_client import LLMResponse
        mock_chat.return_value = LLMResponse(
            content=json.dumps({"sections": sections}),
            model="gpt-4o-mini",
            usage={"prompt_tokens": 300, "completion_tokens": 600},
            stop_reason="stop",
        )

        profile = self._make_profile()
        features = [{"name": "Dashboard", "description": "Main dashboard"}]
        result = generate_outline_from_profile(profile, features)
        assert len(result) == 10
        assert result[0]["title"] == "Section 1"

    @patch("execution.outline_generator.chat")
    @patch("execution.outline_generator.is_available", return_value=True)
    def test_llm_error_returns_enhanced_defaults(self, mock_avail, mock_chat):
        from execution.llm_client import LLMClientError
        mock_chat.side_effect = LLMClientError("API error")

        profile = self._make_profile()
        result = generate_outline_from_profile(profile, [])
        assert len(result) == 10
        assert result[0]["summary"] == ""

    def test_light_mode_generates_5_sections(self):
        result = generate_outline_from_profile({}, [], depth_mode="light")
        assert len(result) == 5

    def test_standard_mode_generates_8_sections(self):
        result = generate_outline_from_profile({}, [], depth_mode="standard")
        assert len(result) == 8

    def test_professional_generates_10_sections(self):
        result = generate_outline_from_profile({}, [], depth_mode="professional")
        assert len(result) == 10

    def test_enterprise_generates_10_sections(self):
        result = generate_outline_from_profile({}, [], depth_mode="enterprise")
        assert len(result) == 10


# ---------------------------------------------------------------------------
# Depth-aware section templates
# ---------------------------------------------------------------------------

class TestDepthAwareSections:
    """Validate depth-aware section templates."""

    def test_light_sections_has_5(self):
        assert len(LIGHT_SECTIONS) == 5

    def test_standard_sections_has_8(self):
        assert len(STANDARD_SECTIONS) == 8

    def test_enhanced_sections_has_10(self):
        assert len(ENHANCED_SECTIONS) == 10

    def test_light_preserves_functional_requirements(self):
        """Light mode must keep Functional Requirements for intelligence goals."""
        titles = [s["title"] for s in LIGHT_SECTIONS]
        assert "Functional Requirements" in titles

    def test_standard_preserves_ai_architecture(self):
        """Standard mode must keep AI & Intelligence Architecture."""
        titles = [s["title"] for s in STANDARD_SECTIONS]
        assert "AI & Intelligence Architecture" in titles
        assert "Functional Requirements" in titles

    def test_get_sections_for_depth_light(self):
        sections = get_sections_for_depth("light")
        assert len(sections) == 5

    def test_get_sections_for_depth_standard(self):
        sections = get_sections_for_depth("standard")
        assert len(sections) == 8

    def test_get_sections_for_depth_professional(self):
        sections = get_sections_for_depth("professional")
        assert len(sections) == 10

    def test_get_sections_for_depth_enterprise(self):
        sections = get_sections_for_depth("enterprise")
        assert len(sections) == 10

    def test_get_sections_returns_copies(self):
        sections = get_sections_for_depth("light")
        sections[0]["title"] = "MUTATED"
        assert LIGHT_SECTIONS[0]["title"] != "MUTATED"


class TestParseEnhancedWithFallback:
    """Test _parse_enhanced_outline_response with custom fallback_sections."""

    def test_fallback_to_light_sections(self):
        result = _parse_enhanced_outline_response("not json", fallback_sections=LIGHT_SECTIONS)
        assert len(result) == 5

    def test_fallback_to_standard_sections(self):
        result = _parse_enhanced_outline_response("not json", fallback_sections=STANDARD_SECTIONS)
        assert len(result) == 8

    def test_too_few_sections_for_standard_falls_back(self):
        import json
        data = {"sections": [{"index": 1, "title": "Only One", "summary": "short"}]}
        result = _parse_enhanced_outline_response(json.dumps(data), fallback_sections=STANDARD_SECTIONS)
        assert len(result) == 8
