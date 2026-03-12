"""Tests for the smart auto-selection engine."""

import json
from unittest.mock import patch

import pytest

from execution.smart_selector import (
    KEYWORD_THRESHOLD,
    _build_keyword_set,
    _parse_id_list,
    _score_item,
    _select_features_by_keywords,
    _select_skills_by_keywords,
    smart_select_features,
    smart_select_skills,
)


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

SAMPLE_CATALOG = [
    {"id": "user_registration", "name": "User Registration", "description": "Account creation with email and profile setup", "category": "Core"},
    {"id": "dashboard", "name": "Dashboard", "description": "Central hub showing key metrics and activity", "category": "Core"},
    {"id": "ai_recommendations", "name": "AI Recommendations", "description": "Personalized suggestions powered by machine learning", "category": "AI"},
    {"id": "nlp_search", "name": "Natural Language Search", "description": "Search using natural language queries", "category": "AI"},
    {"id": "payment_gateway", "name": "Payment Gateway", "description": "Stripe integration for billing and subscriptions", "category": "Integrations"},
    {"id": "dark_mode", "name": "Dark Mode", "description": "Alternate color scheme reducing eye strain", "category": "UX"},
]

SAMPLE_REGISTRY = [
    {"id": "web_search", "name": "Web Search", "description": "Search the internet for real-time information", "tags": ["search", "web", "real-time"]},
    {"id": "rag_pipeline", "name": "RAG Pipeline", "description": "Retrieve relevant documents from vector stores", "tags": ["rag", "retrieval", "vector-store"]},
    {"id": "mcp_github", "name": "MCP GitHub Server", "description": "Interact with GitHub repos via MCP", "tags": ["github", "mcp", "vcs"]},
    {"id": "code_interpreter", "name": "Code Interpreter", "description": "Execute code in sandboxed environments", "tags": ["code", "execution", "sandbox"]},
    {"id": "email_sender", "name": "Email Sending Service", "description": "Send emails via SendGrid or SMTP", "tags": ["email", "sendgrid", "notifications"]},
]

SAMPLE_PROFILE = {
    "problem_definition": {"selected": "Users need AI-powered search and recommendations"},
    "target_user": {"selected": "Developers and data scientists"},
    "value_proposition": {"selected": "Intelligent search with machine learning"},
    "deployment_type": {"selected": "SaaS web application"},
    "ai_depth": {"selected": "AI-native"},
    "monetization_model": {"selected": "Freemium SaaS"},
    "mvp_scope": {"selected": "Core search and recommendations"},
}

EMPTY_PROFILE = {
    "problem_definition": {"selected": ""},
    "target_user": {"selected": ""},
    "value_proposition": {"selected": ""},
    "deployment_type": {"selected": ""},
    "ai_depth": {"selected": ""},
}


# ---------------------------------------------------------------------------
# _build_keyword_set
# ---------------------------------------------------------------------------


class TestBuildKeywordSet:
    def test_extracts_from_profile(self):
        kw = _build_keyword_set(SAMPLE_PROFILE, "")
        assert "search" in kw
        assert "machine" in kw

    def test_extracts_from_idea(self):
        kw = _build_keyword_set({}, "Build an AI chatbot for customer support")
        assert "chatbot" in kw
        assert "customer" in kw

    def test_filters_short_words(self):
        kw = _build_keyword_set({}, "An AI app to do big things")
        assert "an" not in kw
        assert "to" not in kw
        assert "do" not in kw

    def test_empty_inputs(self):
        kw = _build_keyword_set({}, "")
        assert kw == set()


# ---------------------------------------------------------------------------
# _score_item
# ---------------------------------------------------------------------------


class TestScoreItem:
    def test_scores_by_keyword_overlap(self):
        item = {"name": "AI Search Engine", "description": "Search with machine learning"}
        keywords = {"search", "machine", "learning", "ai"}
        score = _score_item(item, keywords)
        assert score >= 3

    def test_zero_score_no_overlap(self):
        item = {"name": "Dark Mode", "description": "Color scheme"}
        keywords = {"database", "sql", "query"}
        assert _score_item(item, keywords) == 0

    def test_uses_tags_when_enabled(self):
        item = {"name": "Tool", "description": "A tool", "tags": ["search", "web"]}
        keywords = {"search", "web"}
        score_with = _score_item(item, keywords, use_tags=True)
        score_without = _score_item(item, keywords, use_tags=False)
        assert score_with > score_without


# ---------------------------------------------------------------------------
# _parse_id_list
# ---------------------------------------------------------------------------


class TestParseIdList:
    def test_parses_valid_json(self):
        raw = json.dumps({"selected": ["a", "b", "c"]})
        result = _parse_id_list(raw, {"a", "b", "c", "d"})
        assert result == ["a", "b", "c"]

    def test_filters_invalid_ids(self):
        raw = json.dumps({"selected": ["a", "invalid", "b"]})
        result = _parse_id_list(raw, {"a", "b"})
        assert result == ["a", "b"]

    def test_returns_empty_on_bad_json(self):
        assert _parse_id_list("not json", {"a"}) == []

    def test_returns_empty_on_missing_key(self):
        raw = json.dumps({"other_key": ["a"]})
        assert _parse_id_list(raw, {"a"}) == []


# ---------------------------------------------------------------------------
# smart_select_features — keyword fallback
# ---------------------------------------------------------------------------


class TestSmartSelectFeatures:
    def test_returns_empty_for_empty_catalog(self):
        assert smart_select_features(SAMPLE_PROFILE, "test", []) == []

    @patch("execution.smart_selector.is_available", return_value=False)
    def test_keyword_fallback_selects_relevant_features(self, _):
        result = smart_select_features(SAMPLE_PROFILE, "AI search platform", SAMPLE_CATALOG)
        # Should select features with keyword overlap (AI, search, etc.)
        assert isinstance(result, list)
        assert len(result) > 0
        # AI-related features should be selected
        assert "ai_recommendations" in result or "nlp_search" in result

    @patch("execution.smart_selector.is_available", return_value=False)
    def test_keyword_fallback_with_empty_profile_selects_all(self, _):
        result = smart_select_features(EMPTY_PROFILE, "", SAMPLE_CATALOG)
        # With no keywords, all features are selected
        assert len(result) == len(SAMPLE_CATALOG)

    @patch("execution.smart_selector.is_available", return_value=True)
    @patch("execution.smart_selector.chat")
    def test_llm_path_returns_ids(self, mock_chat, _):
        from execution.llm_client import LLMResponse
        mock_chat.return_value = LLMResponse(
            content=json.dumps({"selected": ["user_registration", "dashboard", "ai_recommendations", "nlp_search", "payment_gateway"]}),
            model="test", usage={}, stop_reason="stop",
        )
        result = smart_select_features(SAMPLE_PROFILE, "test idea", SAMPLE_CATALOG)
        assert len(result) == 5
        assert "ai_recommendations" in result

    @patch("execution.smart_selector.is_available", return_value=True)
    @patch("execution.smart_selector.chat", side_effect=Exception("LLM error"))
    def test_falls_back_on_llm_error(self, *_):
        result = smart_select_features(SAMPLE_PROFILE, "AI search", SAMPLE_CATALOG)
        assert isinstance(result, list)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# smart_select_skills — keyword fallback
# ---------------------------------------------------------------------------


class TestSmartSelectSkills:
    def test_returns_empty_for_empty_registry(self):
        assert smart_select_skills(SAMPLE_PROFILE, [], []) == []

    @patch("execution.smart_selector.is_available", return_value=False)
    def test_keyword_fallback_selects_relevant_skills(self, _):
        features = [
            {"name": "AI Search", "description": "Search with machine learning"},
            {"name": "Web Scraping", "description": "Scrape websites for data"},
        ]
        result = smart_select_skills(SAMPLE_PROFILE, features, SAMPLE_REGISTRY)
        assert isinstance(result, list)
        # web_search should match (search, web keywords)
        assert "web_search" in result

    @patch("execution.smart_selector.is_available", return_value=False)
    def test_keyword_fallback_empty_features(self, _):
        result = smart_select_skills(EMPTY_PROFILE, [], SAMPLE_REGISTRY)
        # No keywords → empty result
        assert result == []

    @patch("execution.smart_selector.is_available", return_value=True)
    @patch("execution.smart_selector.chat")
    def test_llm_path_returns_ids(self, mock_chat, _):
        from execution.llm_client import LLMResponse
        mock_chat.return_value = LLMResponse(
            content=json.dumps({"selected": ["web_search", "rag_pipeline", "code_interpreter"]}),
            model="test", usage={}, stop_reason="stop",
        )
        features = [{"name": "Search", "description": "search"}]
        result = smart_select_skills(SAMPLE_PROFILE, features, SAMPLE_REGISTRY)
        assert len(result) == 3


# ---------------------------------------------------------------------------
# _select_features_by_keywords threshold behavior
# ---------------------------------------------------------------------------


class TestKeywordThreshold:
    def test_relaxes_threshold_when_too_few(self):
        """When threshold >= 2 yields < 10 results on a large catalog, relax to >= 1."""
        # Create a catalog of 20 items where most have low overlap
        catalog = [
            {"id": f"feat_{i}", "name": f"Feature {i}", "description": f"Generic thing {i}"}
            for i in range(20)
        ]
        # Add one feature with a single matching keyword
        catalog.append({"id": "match_one", "name": "Search Tool", "description": "Does searching"})
        profile = {"problem_definition": {"selected": "search tool"}}
        result = _select_features_by_keywords(profile, "search", catalog)
        # Should include match_one even with score=1 due to relaxation
        assert "match_one" in result
