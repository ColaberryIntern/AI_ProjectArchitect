"""Tests for the profile generator."""

import json
from unittest.mock import patch

from execution.profile_generator import (
    FALLBACK_OPTIONS,
    _fallback_profile,
    _parse_profile_response,
    generate_profile,
)


def _make_valid_llm_response():
    """Build a valid LLM response dict for testing."""
    fields = {}
    for field_name in FALLBACK_OPTIONS:
        fields[field_name] = {
            "options": [
                {"value": f"{field_name}_opt1", "label": "Option 1", "description": "Desc 1"},
                {"value": f"{field_name}_opt2", "label": "Option 2", "description": "Desc 2"},
                {"value": f"{field_name}_opt3", "label": "Option 3", "description": "Desc 3"},
            ],
            "recommended": f"{field_name}_opt1",
            "confidence": 0.85,
        }
    return {
        "fields": fields,
        "derived": {
            "technical_constraints": ["REST API required", "Must support SSO"],
            "non_functional_requirements": ["99.9% uptime", "Sub-2s latency"],
            "success_metrics": ["50% reduction in planning time"],
            "risk_assessment": ["LLM availability", "Data privacy concerns"],
            "core_use_cases": ["Submit idea", "Review profile"],
        },
    }


class TestFallbackOptions:
    """Validate the fallback options structure."""

    def test_has_all_7_fields(self):
        assert len(FALLBACK_OPTIONS) == 7

    def test_each_field_has_at_least_3_options(self):
        for field, options in FALLBACK_OPTIONS.items():
            assert len(options) >= 3, f"{field} has only {len(options)} options"

    def test_all_options_have_required_keys(self):
        for field, options in FALLBACK_OPTIONS.items():
            for opt in options:
                assert "value" in opt, f"{field} option missing 'value'"
                assert "label" in opt, f"{field} option missing 'label'"
                assert "description" in opt, f"{field} option missing 'description'"

    def test_all_option_values_unique_within_field(self):
        for field, options in FALLBACK_OPTIONS.items():
            values = [o["value"] for o in options]
            assert len(values) == len(set(values)), f"{field} has duplicate values"


class TestFallbackProfile:
    """Test the fallback profile structure."""

    def test_has_fields_and_derived(self):
        result = _fallback_profile()
        assert "fields" in result
        assert "derived" in result

    def test_all_7_fields_present(self):
        result = _fallback_profile()
        for field_name in FALLBACK_OPTIONS:
            assert field_name in result["fields"]

    def test_each_field_has_options_and_recommended(self):
        result = _fallback_profile()
        for field_name, field_data in result["fields"].items():
            assert "options" in field_data
            assert "recommended" in field_data
            assert "confidence" in field_data
            assert field_data["confidence"] == 0.0

    def test_recommended_matches_first_option(self):
        result = _fallback_profile()
        for field_name, field_data in result["fields"].items():
            assert field_data["recommended"] == field_data["options"][0]["value"]

    def test_derived_lists_empty(self):
        result = _fallback_profile()
        for key in ["technical_constraints", "non_functional_requirements",
                     "success_metrics", "risk_assessment", "core_use_cases"]:
            assert result["derived"][key] == []


class TestParseProfileResponse:
    """Test parsing LLM JSON output into profile dict."""

    def test_valid_json_parsed(self):
        data = _make_valid_llm_response()
        result = _parse_profile_response(json.dumps(data))
        assert "fields" in result
        assert "derived" in result
        for field_name in FALLBACK_OPTIONS:
            assert field_name in result["fields"]
            assert len(result["fields"][field_name]["options"]) == 3

    def test_derived_lists_parsed(self):
        data = _make_valid_llm_response()
        result = _parse_profile_response(json.dumps(data))
        assert result["derived"]["technical_constraints"] == ["REST API required", "Must support SSO"]
        assert len(result["derived"]["success_metrics"]) == 1

    def test_invalid_json_returns_fallback(self):
        result = _parse_profile_response("not json")
        assert result == _fallback_profile()

    def test_none_input_returns_fallback(self):
        result = _parse_profile_response(None)
        assert result == _fallback_profile()

    def test_missing_fields_key_returns_fallback(self):
        result = _parse_profile_response(json.dumps({"data": {}}))
        assert result == _fallback_profile()

    def test_missing_required_field_returns_fallback(self):
        data = _make_valid_llm_response()
        del data["fields"]["ai_depth"]
        result = _parse_profile_response(json.dumps(data))
        assert result == _fallback_profile()

    def test_field_with_too_few_options_returns_fallback(self):
        data = _make_valid_llm_response()
        data["fields"]["target_user"]["options"] = [
            {"value": "only_one", "label": "Only One", "description": "Single option"}
        ]
        result = _parse_profile_response(json.dumps(data))
        assert result == _fallback_profile()

    def test_option_missing_value_returns_fallback(self):
        data = _make_valid_llm_response()
        data["fields"]["deployment_type"]["options"][0] = {"label": "No value key"}
        result = _parse_profile_response(json.dumps(data))
        assert result == _fallback_profile()

    def test_bad_recommended_defaults_to_first(self):
        data = _make_valid_llm_response()
        data["fields"]["ai_depth"]["recommended"] = "nonexistent_value"
        result = _parse_profile_response(json.dumps(data))
        assert result["fields"]["ai_depth"]["recommended"] == data["fields"]["ai_depth"]["options"][0]["value"]

    def test_confidence_clamped_to_range(self):
        data = _make_valid_llm_response()
        data["fields"]["mvp_scope"]["confidence"] = 1.5
        result = _parse_profile_response(json.dumps(data))
        assert result["fields"]["mvp_scope"]["confidence"] == 1.0

    def test_negative_confidence_clamped(self):
        data = _make_valid_llm_response()
        data["fields"]["mvp_scope"]["confidence"] = -0.5
        result = _parse_profile_response(json.dumps(data))
        assert result["fields"]["mvp_scope"]["confidence"] == 0.0

    def test_non_numeric_confidence_defaults_to_zero(self):
        data = _make_valid_llm_response()
        data["fields"]["mvp_scope"]["confidence"] = "high"
        result = _parse_profile_response(json.dumps(data))
        assert result["fields"]["mvp_scope"]["confidence"] == 0.0

    def test_missing_derived_uses_empty_lists(self):
        data = _make_valid_llm_response()
        del data["derived"]
        result = _parse_profile_response(json.dumps(data))
        for key in ["technical_constraints", "non_functional_requirements",
                     "success_metrics", "risk_assessment", "core_use_cases"]:
            assert result["derived"][key] == []

    def test_option_missing_label_uses_value(self):
        data = _make_valid_llm_response()
        del data["fields"]["target_user"]["options"][0]["label"]
        result = _parse_profile_response(json.dumps(data))
        opt = result["fields"]["target_user"]["options"][0]
        assert opt["label"] == opt["value"]

    def test_option_missing_description_uses_empty(self):
        data = _make_valid_llm_response()
        del data["fields"]["target_user"]["options"][0]["description"]
        result = _parse_profile_response(json.dumps(data))
        assert result["fields"]["target_user"]["options"][0]["description"] == ""


class TestGenerateProfile:
    """Test the main generate_profile function."""

    def test_empty_idea_returns_fallback(self):
        result = generate_profile("")
        assert result == _fallback_profile()

    def test_whitespace_idea_returns_fallback(self):
        result = generate_profile("   ")
        assert result == _fallback_profile()

    @patch("execution.profile_generator.is_available", return_value=False)
    def test_llm_unavailable_returns_fallback(self, mock_avail):
        result = generate_profile("Build an AI app")
        assert result == _fallback_profile()

    @patch("execution.profile_generator.chat")
    @patch("execution.profile_generator.is_available", return_value=True)
    def test_llm_success_returns_parsed_profile(self, mock_avail, mock_chat):
        """Valid LLM response returns project-specific profile."""
        data = _make_valid_llm_response()

        from execution.llm_client import LLMResponse
        mock_chat.return_value = LLMResponse(
            content=json.dumps(data),
            model="gpt-4o-mini",
            usage={"prompt_tokens": 200, "completion_tokens": 400},
            stop_reason="stop",
        )

        result = generate_profile("Build an AI-powered learning platform")
        assert "fields" in result
        assert "derived" in result
        for field_name in FALLBACK_OPTIONS:
            assert field_name in result["fields"]
            assert len(result["fields"][field_name]["options"]) == 3

    @patch("execution.profile_generator.chat")
    @patch("execution.profile_generator.is_available", return_value=True)
    def test_llm_error_returns_fallback(self, mock_avail, mock_chat):
        from execution.llm_client import LLMClientError
        mock_chat.side_effect = LLMClientError("API error")

        result = generate_profile("Build an AI app")
        assert result == _fallback_profile()

    @patch("execution.profile_generator.chat")
    @patch("execution.profile_generator.is_available", return_value=True)
    def test_llm_unexpected_error_returns_fallback(self, mock_avail, mock_chat):
        mock_chat.side_effect = RuntimeError("unexpected")

        result = generate_profile("Build an AI app")
        assert result == _fallback_profile()

    def test_returns_independent_copies(self):
        """Each call returns independent copies, not shared references."""
        result1 = generate_profile("")
        result2 = generate_profile("")
        result1["fields"]["ai_depth"]["confidence"] = 999
        assert result2["fields"]["ai_depth"]["confidence"] == 0.0
