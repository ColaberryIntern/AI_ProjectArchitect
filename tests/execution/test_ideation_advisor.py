"""Tests for the ideation advisor module."""

import json
from unittest.mock import patch

import pytest

from execution.ideation_advisor import (
    AdvisorResponse,
    _dict_to_advisor_response,
    _ensure_alternating,
    _ensure_options,
    _parse_llm_response,
    build_advisor_messages,
    get_fallback_response,
    get_ideation_response,
)
from execution.llm_client import LLMClientError, LLMResponse, LLMUnavailableError


# ---------------------------------------------------------------------------
# Sample dimension states for testing
# ---------------------------------------------------------------------------

def _all_open():
    return {
        "business_model": {"status": "open", "responses": [], "summary": None},
        "user_problem": {"status": "open", "responses": [], "summary": None},
        "ai_leverage": {"status": "open", "responses": [], "summary": None},
        "differentiation": {"status": "open", "responses": [], "summary": None},
    }


def _partially_done():
    return {
        "business_model": {"status": "answered", "responses": [], "summary": "Small businesses"},
        "user_problem": {"status": "answered", "responses": [], "summary": "Manual data entry"},
        "ai_leverage": {"status": "open", "responses": [], "summary": None},
        "differentiation": {"status": "open", "responses": [], "summary": None},
    }


def _all_done():
    return {
        "business_model": {"status": "answered", "responses": [], "summary": "Small businesses"},
        "user_problem": {"status": "answered", "responses": [], "summary": "Manual work"},
        "ai_leverage": {"status": "answered", "responses": [], "summary": "Smart predictions"},
        "differentiation": {"status": "answered", "responses": [], "summary": "Simpler UX"},
    }


# ---------------------------------------------------------------------------
# JSON parsing tests
# ---------------------------------------------------------------------------

class TestParseResponse:
    def test_valid_json(self):
        raw = json.dumps({
            "bot_message": "Hello!",
            "options": ["A", "B"],
            "options_mode": "single",
            "dimension_updates": {},
            "is_complete": False,
            "synthesis": None,
        })
        result = _parse_llm_response(raw)
        assert result is not None
        assert result["bot_message"] == "Hello!"

    def test_json_with_code_fences(self):
        raw = '```json\n{"bot_message": "Hello!", "options": []}\n```'
        result = _parse_llm_response(raw)
        assert result is not None
        assert result["bot_message"] == "Hello!"

    def test_json_with_plain_fences(self):
        raw = '```\n{"bot_message": "Hi there"}\n```'
        result = _parse_llm_response(raw)
        assert result is not None
        assert result["bot_message"] == "Hi there"

    def test_invalid_json_returns_none(self):
        assert _parse_llm_response("not json at all") is None

    def test_missing_bot_message_returns_none(self):
        raw = json.dumps({"options": ["A"]})
        assert _parse_llm_response(raw) is None

    def test_non_dict_returns_none(self):
        assert _parse_llm_response("[1, 2, 3]") is None

    def test_whitespace_handling(self):
        raw = '  \n  {"bot_message": "spaced out"}  \n  '
        result = _parse_llm_response(raw)
        assert result is not None
        assert result["bot_message"] == "spaced out"


class TestDictToAdvisorResponse:
    def test_full_response(self):
        data = {
            "bot_message": "Great choice!",
            "options": ["A", "B", "C"],
            "options_mode": "single",
            "dimension_updates": {"business_model": "Enterprise teams"},
            "is_complete": False,
            "synthesis": None,
        }
        resp = _dict_to_advisor_response(data)
        assert isinstance(resp, AdvisorResponse)
        assert resp.bot_message == "Great choice!"
        assert len(resp.options) == 3
        assert resp.dimension_updates == {"business_model": "Enterprise teams"}
        assert resp.fallback_used is False

    def test_minimal_response(self):
        data = {"bot_message": "Hi"}
        resp = _dict_to_advisor_response(data)
        assert resp.bot_message == "Hi"
        assert resp.options == []
        assert resp.options_mode == "single"
        assert resp.dimension_updates == {}
        assert resp.is_complete is False

    def test_complete_with_synthesis(self):
        data = {
            "bot_message": "All done!",
            "is_complete": True,
            "synthesis": {
                "business_model": "B2B SaaS",
                "user_problem": "Slow processes",
                "ai_leverage": "Automation",
                "differentiation": "Speed",
            },
        }
        resp = _dict_to_advisor_response(data)
        assert resp.is_complete is True
        assert resp.synthesis is not None
        assert resp.synthesis["business_model"] == "B2B SaaS"


# ---------------------------------------------------------------------------
# Message building tests
# ---------------------------------------------------------------------------

class TestBuildMessages:
    def test_empty_history(self):
        msgs = build_advisor_messages("Build an AI tool", [], _all_open())
        assert len(msgs) >= 1
        assert msgs[0]["role"] == "user"
        assert "Build an AI tool" in msgs[0]["content"]
        assert "NEEDS EXPLORATION" in msgs[0]["content"]

    def test_with_conversation_history(self):
        history = [
            {"role": "user", "text": "Small business owners"},
            {"role": "bot", "text": "What problem do they face?"},
        ]
        msgs = build_advisor_messages("Build an AI tool", history, _partially_done())
        assert len(msgs) >= 2
        # First message should have context
        assert "Build an AI tool" in msgs[0]["content"]
        assert "ANSWERED" in msgs[0]["content"]

    def test_dimension_status_shown(self):
        msgs = build_advisor_messages("Test idea", [], _partially_done())
        content = msgs[0]["content"]
        assert "business_model: ANSWERED" in content
        assert "ai_leverage: NEEDS EXPLORATION" in content

    def test_idea_prominently_displayed(self):
        msgs = build_advisor_messages("Build an AI training builder", [], _all_open())
        content = msgs[0]["content"]
        assert "USER'S PROJECT IDEA" in content
        assert "Build an AI training builder" in content

    def test_turn_number_included(self):
        history = [
            {"role": "user", "text": "Small biz"},
            {"role": "bot", "text": "Got it"},
        ]
        msgs = build_advisor_messages("Test idea", history, _all_open())
        content = msgs[0]["content"]
        assert "Turn number:" in content

    def test_instruction_section_present(self):
        msgs = build_advisor_messages("Test idea", [], _all_open())
        content = msgs[0]["content"]
        assert "INSTRUCTION" in content
        assert "SPECIFIC" in content


class TestEnsureAlternating:
    def test_already_alternating(self):
        msgs = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello"},
            {"role": "user", "content": "How are you?"},
        ]
        result = _ensure_alternating(msgs)
        assert len(result) == 3

    def test_merges_consecutive_same_role(self):
        msgs = [
            {"role": "user", "content": "Hi"},
            {"role": "user", "content": "More info"},
            {"role": "assistant", "content": "Got it"},
        ]
        result = _ensure_alternating(msgs)
        assert len(result) == 2
        assert "Hi" in result[0]["content"]
        assert "More info" in result[0]["content"]

    def test_prepends_user_if_starts_with_assistant(self):
        msgs = [
            {"role": "assistant", "content": "Hello"},
            {"role": "user", "content": "Hi"},
        ]
        result = _ensure_alternating(msgs)
        assert result[0]["role"] == "user"

    def test_empty_list(self):
        assert _ensure_alternating([]) == []


# ---------------------------------------------------------------------------
# Fallback tests
# ---------------------------------------------------------------------------

class TestFallbackResponse:
    def test_returns_first_unanswered_dimension(self):
        resp = get_fallback_response(_all_open())
        assert resp.fallback_used is True
        assert "Who is this product for" in resp.bot_message
        assert len(resp.options) > 0
        assert resp.options_mode == "single"

    def test_skips_answered_dimensions(self):
        resp = get_fallback_response(_partially_done())
        assert resp.fallback_used is True
        # business_model and user_problem answered, so should ask about ai_leverage
        assert "AI help most" in resp.bot_message
        assert resp.options_mode == "single"

    def test_all_done_signals_complete(self):
        resp = get_fallback_response(_all_done())
        assert resp.fallback_used is True
        assert resp.is_complete is True


# ---------------------------------------------------------------------------
# Main entry point tests
# ---------------------------------------------------------------------------

class TestGetIdeationResponse:
    def test_uses_fallback_when_llm_disabled(self, monkeypatch):
        monkeypatch.setattr("execution.ideation_advisor.LLM_ENABLED", False)
        resp = get_ideation_response("Build something", [], _all_open())
        assert resp.fallback_used is True

    def test_uses_fallback_when_no_api_key(self, monkeypatch):
        monkeypatch.setattr("execution.ideation_advisor.LLM_ENABLED", True)
        monkeypatch.setattr("execution.ideation_advisor.llm_client.is_available", lambda: False)
        resp = get_ideation_response("Build something", [], _all_open())
        assert resp.fallback_used is True

    def test_successful_llm_call(self, monkeypatch):
        monkeypatch.setattr("execution.ideation_advisor.LLM_ENABLED", True)
        monkeypatch.setattr("execution.ideation_advisor.llm_client.is_available", lambda: True)

        llm_json = json.dumps({
            "bot_message": "Interesting idea! Who will use this?",
            "options": ["Startups", "Large companies"],
            "options_mode": "single",
            "dimension_updates": {},
            "is_complete": False,
            "synthesis": None,
        })
        mock_llm_response = LLMResponse(
            content=llm_json, model="test", usage={}, stop_reason="end_turn",
        )
        monkeypatch.setattr(
            "execution.ideation_advisor.llm_client.chat",
            lambda **kwargs: mock_llm_response,
        )

        resp = get_ideation_response("Build an AI scheduler", [], _all_open())
        assert resp.fallback_used is False
        assert resp.bot_message == "Interesting idea! Who will use this?"
        assert "Startups" in resp.options

    def test_llm_parse_failure_falls_back(self, monkeypatch):
        monkeypatch.setattr("execution.ideation_advisor.LLM_ENABLED", True)
        monkeypatch.setattr("execution.ideation_advisor.llm_client.is_available", lambda: True)

        # LLM returns garbage
        mock_llm_response = LLMResponse(
            content="I'm not sure what format to use...",
            model="test", usage={}, stop_reason="end_turn",
        )
        monkeypatch.setattr(
            "execution.ideation_advisor.llm_client.chat",
            lambda **kwargs: mock_llm_response,
        )

        resp = get_ideation_response("Build something", [], _all_open())
        assert resp.fallback_used is True

    def test_llm_unavailable_error_falls_back(self, monkeypatch):
        monkeypatch.setattr("execution.ideation_advisor.LLM_ENABLED", True)
        monkeypatch.setattr("execution.ideation_advisor.llm_client.is_available", lambda: True)

        def raise_unavailable(**kwargs):
            raise LLMUnavailableError("no key")

        monkeypatch.setattr(
            "execution.ideation_advisor.llm_client.chat", raise_unavailable,
        )

        resp = get_ideation_response("Build something", [], _all_open())
        assert resp.fallback_used is True

    def test_llm_client_error_falls_back(self, monkeypatch):
        monkeypatch.setattr("execution.ideation_advisor.LLM_ENABLED", True)
        monkeypatch.setattr("execution.ideation_advisor.llm_client.is_available", lambda: True)

        def raise_client_error(**kwargs):
            raise LLMClientError("API error")

        monkeypatch.setattr(
            "execution.ideation_advisor.llm_client.chat", raise_client_error,
        )

        resp = get_ideation_response("Build something", [], _all_open())
        assert resp.fallback_used is True

    def test_complete_response_with_synthesis(self, monkeypatch):
        monkeypatch.setattr("execution.ideation_advisor.LLM_ENABLED", True)
        monkeypatch.setattr("execution.ideation_advisor.llm_client.is_available", lambda: True)

        llm_json = json.dumps({
            "bot_message": "Great, I have a clear picture now!",
            "options": [],
            "options_mode": "single",
            "dimension_updates": {"differentiation": "10x faster processing"},
            "is_complete": True,
            "synthesis": {
                "business_model": "B2B SaaS for logistics",
                "user_problem": "Slow manual routing",
                "ai_leverage": "Predictive routing + anomaly detection",
                "differentiation": "10x faster processing",
            },
        })
        mock_llm_response = LLMResponse(
            content=llm_json, model="test", usage={}, stop_reason="end_turn",
        )
        monkeypatch.setattr(
            "execution.ideation_advisor.llm_client.chat",
            lambda **kwargs: mock_llm_response,
        )

        resp = get_ideation_response("AI logistics optimizer", [], _partially_done())
        assert resp.is_complete is True
        assert resp.synthesis is not None
        assert "logistics" in resp.synthesis["business_model"]
        assert resp.dimension_updates["differentiation"] == "10x faster processing"

    def test_passes_response_format_to_llm(self, monkeypatch):
        monkeypatch.setattr("execution.ideation_advisor.LLM_ENABLED", True)
        monkeypatch.setattr("execution.ideation_advisor.llm_client.is_available", lambda: True)

        captured_kwargs = {}

        def mock_chat(**kwargs):
            captured_kwargs.update(kwargs)
            llm_json = json.dumps({
                "bot_message": "Hi!",
                "options": ["A", "B"],
                "options_mode": "single",
                "dimension_updates": {},
                "is_complete": False,
                "synthesis": None,
            })
            return LLMResponse(
                content=llm_json, model="test", usage={}, stop_reason="end_turn",
            )

        monkeypatch.setattr("execution.ideation_advisor.llm_client.chat", mock_chat)

        get_ideation_response("Build something", [], _all_open())
        assert captured_kwargs.get("response_format") == {"type": "json_object"}

    def test_ensure_options_applied_to_llm_response(self, monkeypatch):
        """Verify _ensure_options fills in missing options from LLM."""
        monkeypatch.setattr("execution.ideation_advisor.LLM_ENABLED", True)
        monkeypatch.setattr("execution.ideation_advisor.llm_client.is_available", lambda: True)

        # LLM returns valid JSON but with empty options
        llm_json = json.dumps({
            "bot_message": "Tell me more about your idea!",
            "options": [],
            "options_mode": "single",
            "dimension_updates": {},
            "is_complete": False,
            "synthesis": None,
        })
        mock_llm_response = LLMResponse(
            content=llm_json, model="test", usage={}, stop_reason="end_turn",
        )
        monkeypatch.setattr(
            "execution.ideation_advisor.llm_client.chat",
            lambda **kwargs: mock_llm_response,
        )

        resp = get_ideation_response("Build something", [], _all_open())
        assert resp.fallback_used is False
        # Options should have been filled by _ensure_options
        assert len(resp.options) >= 3

    def test_no_features_extracted_field(self, monkeypatch):
        """AdvisorResponse should NOT have a features_extracted field."""
        resp = AdvisorResponse(bot_message="Test")
        assert not hasattr(resp, "features_extracted")


# ---------------------------------------------------------------------------
# Options safety net tests
# ---------------------------------------------------------------------------

class TestEnsureOptions:
    def test_preserves_good_options(self):
        resp = AdvisorResponse(
            bot_message="Question?",
            options=["A", "B", "C"],
        )
        result = _ensure_options(resp)
        assert result.options == ["A", "B", "C"]

    def test_strips_other_from_llm_response(self):
        resp = AdvisorResponse(
            bot_message="Question?",
            options=["A", "B", "C", "Other (I'll type my own)"],
        )
        result = _ensure_options(resp)
        assert "Other (I'll type my own)" not in result.options
        assert result.options == ["A", "B", "C"]

    def test_generates_fallback_when_empty(self):
        resp = AdvisorResponse(
            bot_message="Question?",
            options=[],
        )
        result = _ensure_options(resp)
        assert len(result.options) >= 3

    def test_generates_fallback_when_single_option(self):
        resp = AdvisorResponse(
            bot_message="Question?",
            options=["Just one"],
        )
        result = _ensure_options(resp)
        assert len(result.options) >= 3

    def test_skips_when_complete(self):
        resp = AdvisorResponse(
            bot_message="Done!",
            options=[],
            is_complete=True,
        )
        result = _ensure_options(resp)
        assert result.options == []

    def test_fallback_uses_single_mode(self):
        resp = AdvisorResponse(
            bot_message="Question?",
            options=[],
        )
        result = _ensure_options(resp)
        assert result.options_mode == "single"
