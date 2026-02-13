"""Tests for the feature advisor module."""

import json

import pytest

from execution.feature_advisor import (
    FeatureAdvisorResponse,
    _dict_to_feature_response,
    _ensure_options,
    _parse_feature_response,
    build_feature_messages,
    extract_features_from_conversation,
    get_feature_fallback_response,
    get_feature_response,
)
from execution.llm_client import LLMClientError, LLMResponse, LLMUnavailableError


# ---------------------------------------------------------------------------
# JSON parsing tests
# ---------------------------------------------------------------------------

class TestParseFeatureResponse:
    def test_valid_json(self):
        raw = json.dumps({
            "bot_message": "Here are some features!",
            "options": ["Auth", "Dashboard"],
            "options_mode": "multi",
            "is_complete": False,
            "features_extracted": [{"name": "Auth", "description": "Login system"}],
        })
        result = _parse_feature_response(raw)
        assert result is not None
        assert result["bot_message"] == "Here are some features!"

    def test_json_with_code_fences(self):
        raw = '```json\n{"bot_message": "Features!", "options": []}\n```'
        result = _parse_feature_response(raw)
        assert result is not None
        assert result["bot_message"] == "Features!"

    def test_json_with_plain_fences(self):
        raw = '```\n{"bot_message": "Hi there"}\n```'
        result = _parse_feature_response(raw)
        assert result is not None

    def test_invalid_json_returns_none(self):
        assert _parse_feature_response("not json at all") is None

    def test_missing_bot_message_returns_none(self):
        raw = json.dumps({"options": ["A"]})
        assert _parse_feature_response(raw) is None

    def test_non_dict_returns_none(self):
        assert _parse_feature_response("[1, 2, 3]") is None

    def test_whitespace_handling(self):
        raw = '  \n  {"bot_message": "spaced out"}  \n  '
        result = _parse_feature_response(raw)
        assert result is not None
        assert result["bot_message"] == "spaced out"


# ---------------------------------------------------------------------------
# Dict-to-response conversion tests
# ---------------------------------------------------------------------------

class TestDictToFeatureResponse:
    def test_full_response(self):
        data = {
            "bot_message": "Great choices!",
            "options": ["Feature A", "Feature B", "Feature C"],
            "options_mode": "multi",
            "is_complete": False,
            "features_extracted": [
                {"name": "User auth", "description": "Login system"},
                {"name": "Dashboard", "description": "Overview page"},
            ],
        }
        resp = _dict_to_feature_response(data)
        assert isinstance(resp, FeatureAdvisorResponse)
        assert resp.bot_message == "Great choices!"
        assert len(resp.options) == 3
        assert resp.options_mode == "multi"
        assert resp.fallback_used is False
        assert len(resp.features_extracted) == 2
        assert resp.features_extracted[0]["name"] == "User auth"

    def test_minimal_response(self):
        data = {"bot_message": "Hi"}
        resp = _dict_to_feature_response(data)
        assert resp.bot_message == "Hi"
        assert resp.options == []
        assert resp.options_mode == "multi"
        assert resp.is_complete is False
        assert resp.features_extracted == []

    def test_complete_response(self):
        data = {
            "bot_message": "All done!",
            "is_complete": True,
            "features_extracted": [
                {"name": "Feature 1", "description": "Desc 1"},
            ],
        }
        resp = _dict_to_feature_response(data)
        assert resp.is_complete is True
        assert len(resp.features_extracted) == 1

    def test_filters_invalid_features(self):
        data = {
            "bot_message": "Test",
            "features_extracted": [
                {"name": "Valid", "description": "Good"},
                {"description": "Missing name"},  # no name
                "not a dict",  # wrong type
                {"name": "", "description": "Empty name"},  # empty name
            ],
        }
        resp = _dict_to_feature_response(data)
        assert len(resp.features_extracted) == 1
        assert resp.features_extracted[0]["name"] == "Valid"

    def test_handles_missing_features_extracted(self):
        data = {"bot_message": "Hi"}
        resp = _dict_to_feature_response(data)
        assert resp.features_extracted == []

    def test_handles_non_list_features(self):
        data = {"bot_message": "Hi", "features_extracted": "not a list"}
        resp = _dict_to_feature_response(data)
        assert resp.features_extracted == []


# ---------------------------------------------------------------------------
# Message building tests
# ---------------------------------------------------------------------------

class TestBuildFeatureMessages:
    def test_empty_history(self):
        msgs = build_feature_messages(
            "Build an AI tool",
            "Summary of ideation",
            [],
        )
        assert len(msgs) >= 1
        assert msgs[0]["role"] == "user"
        assert "Build an AI tool" in msgs[0]["content"]
        assert "Summary of ideation" in msgs[0]["content"]

    def test_includes_idea_and_summary(self):
        msgs = build_feature_messages(
            "AI training builder",
            "For corporate teams, solves manual training",
            [],
        )
        content = msgs[0]["content"]
        assert "AI training builder" in content
        assert "For corporate teams" in content

    def test_with_conversation_history(self):
        history = [
            {"role": "user", "text": "I want auth and dashboard"},
            {"role": "bot", "text": "Great choices! What about AI features?"},
        ]
        msgs = build_feature_messages(
            "Build an AI tool",
            "Summary",
            history,
        )
        assert len(msgs) >= 2

    def test_includes_extracted_features(self):
        features = [
            {"name": "User auth", "description": "Login system"},
            {"name": "Dashboard", "description": "Overview page"},
        ]
        msgs = build_feature_messages(
            "Build an AI tool",
            "Summary",
            [],
            extracted_features=features,
        )
        content = msgs[0]["content"]
        assert "PREVIOUSLY EXTRACTED FEATURES" in content
        assert "User auth" in content
        assert "Dashboard" in content

    def test_no_features_section_when_empty(self):
        msgs = build_feature_messages(
            "Build an AI tool",
            "Summary",
            [],
            extracted_features=[],
        )
        content = msgs[0]["content"]
        assert "PREVIOUSLY EXTRACTED FEATURES" not in content

    def test_no_features_section_when_none(self):
        msgs = build_feature_messages(
            "Build an AI tool",
            "Summary",
            [],
            extracted_features=None,
        )
        content = msgs[0]["content"]
        assert "PREVIOUSLY EXTRACTED FEATURES" not in content

    def test_turn_number_included(self):
        history = [
            {"role": "user", "text": "Feature A"},
            {"role": "bot", "text": "Got it"},
        ]
        msgs = build_feature_messages("Test", "Summary", history)
        content = msgs[0]["content"]
        assert "Turn number:" in content


# ---------------------------------------------------------------------------
# Options safety net tests
# ---------------------------------------------------------------------------

class TestEnsureFeatureOptions:
    def test_preserves_good_options(self):
        resp = FeatureAdvisorResponse(
            bot_message="Question?",
            options=["A", "B", "C"],
        )
        result = _ensure_options(resp)
        assert result.options == ["A", "B", "C"]

    def test_strips_other_from_llm_response(self):
        resp = FeatureAdvisorResponse(
            bot_message="Question?",
            options=["A", "B", "C", "Other (type your own)"],
        )
        result = _ensure_options(resp)
        assert "Other (type your own)" not in result.options
        assert result.options == ["A", "B", "C"]

    def test_generates_fallback_when_empty(self):
        resp = FeatureAdvisorResponse(
            bot_message="Question?",
            options=[],
        )
        result = _ensure_options(resp)
        assert len(result.options) >= 3
        assert result.options_mode == "multi"

    def test_generates_fallback_when_single_option(self):
        resp = FeatureAdvisorResponse(
            bot_message="Question?",
            options=["Just one"],
        )
        result = _ensure_options(resp)
        assert len(result.options) >= 3

    def test_skips_when_complete(self):
        resp = FeatureAdvisorResponse(
            bot_message="Done!",
            options=[],
            is_complete=True,
        )
        result = _ensure_options(resp)
        assert result.options == []


# ---------------------------------------------------------------------------
# Fallback tests
# ---------------------------------------------------------------------------

class TestFeatureFallbackResponse:
    def test_first_fallback(self):
        resp = get_feature_fallback_response(0)
        assert resp.fallback_used is True
        assert len(resp.options) > 0
        assert resp.options_mode == "multi"
        assert resp.is_complete is False

    def test_second_fallback(self):
        resp = get_feature_fallback_response(1)
        assert resp.fallback_used is True
        assert len(resp.options) > 0

    def test_third_fallback(self):
        resp = get_feature_fallback_response(2)
        assert resp.fallback_used is True
        assert len(resp.options) > 0

    def test_fourth_fallback(self):
        resp = get_feature_fallback_response(3)
        assert resp.fallback_used is True
        assert len(resp.options) > 0

    def test_beyond_last_fallback_signals_complete(self):
        resp = get_feature_fallback_response(10)
        assert resp.fallback_used is True
        assert resp.is_complete is True


# ---------------------------------------------------------------------------
# Main entry point tests
# ---------------------------------------------------------------------------

class TestGetFeatureResponse:
    def test_uses_fallback_when_llm_disabled(self, monkeypatch):
        monkeypatch.setattr("execution.feature_advisor.LLM_ENABLED", False)
        resp = get_feature_response("Build something", "Summary", [])
        assert resp.fallback_used is True

    def test_uses_fallback_when_no_api_key(self, monkeypatch):
        monkeypatch.setattr("execution.feature_advisor.LLM_ENABLED", True)
        monkeypatch.setattr("execution.feature_advisor.llm_client.is_available", lambda: False)
        resp = get_feature_response("Build something", "Summary", [])
        assert resp.fallback_used is True

    def test_successful_llm_call(self, monkeypatch):
        monkeypatch.setattr("execution.feature_advisor.LLM_ENABLED", True)
        monkeypatch.setattr("execution.feature_advisor.llm_client.is_available", lambda: True)

        llm_json = json.dumps({
            "bot_message": "Here are some core features!",
            "options": ["User auth", "Dashboard", "AI search"],
            "options_mode": "multi",
            "is_complete": False,
            "features_extracted": [
                {"name": "User auth", "description": "Login and registration"},
            ],
        })
        mock_llm_response = LLMResponse(
            content=llm_json, model="test", usage={}, stop_reason="end_turn",
        )
        monkeypatch.setattr(
            "execution.feature_advisor.llm_client.chat",
            lambda **kwargs: mock_llm_response,
        )

        resp = get_feature_response("AI scheduler", "Summary of ideation", [])
        assert resp.fallback_used is False
        assert resp.bot_message == "Here are some core features!"
        assert "User auth" in resp.options
        assert len(resp.features_extracted) == 1

    def test_llm_parse_failure_falls_back(self, monkeypatch):
        monkeypatch.setattr("execution.feature_advisor.LLM_ENABLED", True)
        monkeypatch.setattr("execution.feature_advisor.llm_client.is_available", lambda: True)

        mock_llm_response = LLMResponse(
            content="I'm confused...",
            model="test", usage={}, stop_reason="end_turn",
        )
        monkeypatch.setattr(
            "execution.feature_advisor.llm_client.chat",
            lambda **kwargs: mock_llm_response,
        )

        resp = get_feature_response("Build something", "Summary", [])
        assert resp.fallback_used is True

    def test_llm_unavailable_error_falls_back(self, monkeypatch):
        monkeypatch.setattr("execution.feature_advisor.LLM_ENABLED", True)
        monkeypatch.setattr("execution.feature_advisor.llm_client.is_available", lambda: True)

        def raise_unavailable(**kwargs):
            raise LLMUnavailableError("no key")

        monkeypatch.setattr(
            "execution.feature_advisor.llm_client.chat", raise_unavailable,
        )

        resp = get_feature_response("Build something", "Summary", [])
        assert resp.fallback_used is True

    def test_llm_client_error_falls_back(self, monkeypatch):
        monkeypatch.setattr("execution.feature_advisor.LLM_ENABLED", True)
        monkeypatch.setattr("execution.feature_advisor.llm_client.is_available", lambda: True)

        def raise_client_error(**kwargs):
            raise LLMClientError("API error")

        monkeypatch.setattr(
            "execution.feature_advisor.llm_client.chat", raise_client_error,
        )

        resp = get_feature_response("Build something", "Summary", [])
        assert resp.fallback_used is True

    def test_passes_response_format_to_llm(self, monkeypatch):
        monkeypatch.setattr("execution.feature_advisor.LLM_ENABLED", True)
        monkeypatch.setattr("execution.feature_advisor.llm_client.is_available", lambda: True)

        captured_kwargs = {}

        def mock_chat(**kwargs):
            captured_kwargs.update(kwargs)
            llm_json = json.dumps({
                "bot_message": "Hi!",
                "options": ["A", "B", "C"],
                "options_mode": "multi",
                "is_complete": False,
                "features_extracted": [],
            })
            return LLMResponse(
                content=llm_json, model="test", usage={}, stop_reason="end_turn",
            )

        monkeypatch.setattr("execution.feature_advisor.llm_client.chat", mock_chat)

        get_feature_response("Build something", "Summary", [])
        assert captured_kwargs.get("response_format") == {"type": "json_object"}

    def test_ensure_options_applied(self, monkeypatch):
        """Verify _ensure_options fills in missing options from LLM."""
        monkeypatch.setattr("execution.feature_advisor.LLM_ENABLED", True)
        monkeypatch.setattr("execution.feature_advisor.llm_client.is_available", lambda: True)

        llm_json = json.dumps({
            "bot_message": "What features do you need?",
            "options": [],
            "options_mode": "multi",
            "is_complete": False,
            "features_extracted": [],
        })
        mock_llm_response = LLMResponse(
            content=llm_json, model="test", usage={}, stop_reason="end_turn",
        )
        monkeypatch.setattr(
            "execution.feature_advisor.llm_client.chat",
            lambda **kwargs: mock_llm_response,
        )

        resp = get_feature_response("Build something", "Summary", [])
        assert resp.fallback_used is False
        assert len(resp.options) >= 3

    def test_passes_existing_features(self, monkeypatch):
        """Verify extracted features are passed through to message building."""
        monkeypatch.setattr("execution.feature_advisor.LLM_ENABLED", True)
        monkeypatch.setattr("execution.feature_advisor.llm_client.is_available", lambda: True)

        captured_kwargs = {}

        def mock_chat(**kwargs):
            captured_kwargs.update(kwargs)
            llm_json = json.dumps({
                "bot_message": "More features?",
                "options": ["Feature D", "Feature E"],
                "options_mode": "multi",
                "is_complete": False,
                "features_extracted": [],
            })
            return LLMResponse(
                content=llm_json, model="test", usage={}, stop_reason="end_turn",
            )

        monkeypatch.setattr("execution.feature_advisor.llm_client.chat", mock_chat)

        existing = [{"name": "Auth", "description": "Login"}]
        get_feature_response("Build something", "Summary", [], extracted_features=existing)

        # The system prompt should include previously extracted features
        messages = captured_kwargs.get("messages", [])
        assert any("Auth" in msg["content"] for msg in messages)


# ---------------------------------------------------------------------------
# Retroactive feature extraction tests
# ---------------------------------------------------------------------------

class TestExtractFeaturesFromConversation:
    def test_returns_empty_when_llm_disabled(self, monkeypatch):
        monkeypatch.setattr("execution.feature_advisor.LLM_ENABLED", False)
        result = extract_features_from_conversation("An idea", [
            {"role": "user", "text": "Build a scheduler"},
        ])
        assert result == []

    def test_returns_empty_when_no_history(self, monkeypatch):
        monkeypatch.setattr("execution.feature_advisor.LLM_ENABLED", True)
        monkeypatch.setattr("execution.feature_advisor.llm_client.is_available", lambda: True)
        result = extract_features_from_conversation("An idea", [])
        assert result == []

    def test_extracts_features_from_conversation(self, monkeypatch):
        monkeypatch.setattr("execution.feature_advisor.LLM_ENABLED", True)
        monkeypatch.setattr("execution.feature_advisor.llm_client.is_available", lambda: True)

        llm_json = json.dumps({
            "features": [
                {"name": "Resume parser", "description": "Extracts skills from resumes"},
                {"name": "Gap analysis", "description": "Identifies skill gaps"},
            ],
        })
        mock_response = LLMResponse(
            content=llm_json, model="test", usage={}, stop_reason="end_turn",
        )
        monkeypatch.setattr(
            "execution.feature_advisor.llm_client.chat",
            lambda **kwargs: mock_response,
        )

        history = [
            {"role": "user", "text": "Build an AI training builder"},
            {"role": "bot", "text": "That sounds great! Have you considered resume parsing?"},
        ]
        result = extract_features_from_conversation("AI training builder", history)
        assert len(result) == 2
        assert result[0]["name"] == "Resume parser"
        assert result[1]["name"] == "Gap analysis"

    def test_handles_llm_error(self, monkeypatch):
        monkeypatch.setattr("execution.feature_advisor.LLM_ENABLED", True)
        monkeypatch.setattr("execution.feature_advisor.llm_client.is_available", lambda: True)

        def raise_error(**kwargs):
            raise LLMClientError("API error")

        monkeypatch.setattr("execution.feature_advisor.llm_client.chat", raise_error)

        result = extract_features_from_conversation("An idea", [
            {"role": "user", "text": "Hello"},
        ])
        assert result == []

    def test_handles_bad_json_response(self, monkeypatch):
        monkeypatch.setattr("execution.feature_advisor.LLM_ENABLED", True)
        monkeypatch.setattr("execution.feature_advisor.llm_client.is_available", lambda: True)

        mock_response = LLMResponse(
            content="not json", model="test", usage={}, stop_reason="end_turn",
        )
        monkeypatch.setattr(
            "execution.feature_advisor.llm_client.chat",
            lambda **kwargs: mock_response,
        )

        result = extract_features_from_conversation("An idea", [
            {"role": "user", "text": "Hello"},
        ])
        assert result == []

    def test_filters_invalid_features(self, monkeypatch):
        monkeypatch.setattr("execution.feature_advisor.LLM_ENABLED", True)
        monkeypatch.setattr("execution.feature_advisor.llm_client.is_available", lambda: True)

        llm_json = json.dumps({
            "features": [
                {"name": "Valid feature", "description": "Works"},
                {"description": "No name"},  # invalid
                "not a dict",  # invalid
            ],
        })
        mock_response = LLMResponse(
            content=llm_json, model="test", usage={}, stop_reason="end_turn",
        )
        monkeypatch.setattr(
            "execution.feature_advisor.llm_client.chat",
            lambda **kwargs: mock_response,
        )

        result = extract_features_from_conversation("An idea", [
            {"role": "user", "text": "Hello"},
        ])
        assert len(result) == 1
        assert result[0]["name"] == "Valid feature"
