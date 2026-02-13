"""Tests for execution/intelligence_goals.py."""

import json
from unittest.mock import patch

import pytest

from execution.intelligence_goals import (
    AI_DEPTH_TRIGGERS,
    AI_TRIGGER_KEYWORDS,
    CONFIDENCE_GOAL_TYPES,
    CONFIDENCE_LEVELS,
    GOAL_TYPE_ARCHITECTURE_RULES,
    GOAL_TYPES,
    HIGH_CONFIDENCE_VALUES,
    _build_architecture_section,
    _build_functional_section,
    _fallback_goals,
    _parse_goals_response,
    build_intelligence_goals_prompt_section,
    check_intelligence_goals_alignment,
    generate_intelligence_goals,
    should_show_intelligence_goals,
)


# --- Fixtures ---

@pytest.fixture
def ai_features():
    """Features with AI-related keywords."""
    return [
        {"name": "AI Recommendation Engine", "description": "ML-based recommendations"},
        {"name": "User Dashboard", "description": "Central hub for users"},
    ]


@pytest.fixture
def non_ai_features():
    """Features with no AI keywords."""
    return [
        {"name": "User Authentication", "description": "Login and signup"},
        {"name": "Dashboard", "description": "Project overview"},
    ]


@pytest.fixture
def valid_goals_json():
    """Valid LLM response JSON for goals."""
    return json.dumps({
        "goals": [
            {"id": "goal_1", "user_facing_label": "Predict user churn", "description": "Forecast which users will leave.", "goal_type": "prediction"},
            {"id": "goal_2", "user_facing_label": "Classify support tickets", "description": "Auto-categorize tickets.", "goal_type": "classification"},
            {"id": "goal_3", "user_facing_label": "Recommend content", "description": "Suggest relevant items.", "goal_type": "recommendation"},
            {"id": "goal_4", "user_facing_label": "Detect anomalies", "description": "Find unusual patterns.", "goal_type": "anomaly_detection"},
        ]
    })


# --- Constants Tests ---

class TestConstants:
    def test_goal_types_not_empty(self):
        assert len(GOAL_TYPES) >= 7

    def test_confidence_levels_not_empty(self):
        assert len(CONFIDENCE_LEVELS) >= 3

    def test_confidence_goal_types_subset_of_goal_types(self):
        for gt in CONFIDENCE_GOAL_TYPES:
            assert gt in GOAL_TYPES

    def test_ai_depth_triggers_not_empty(self):
        assert len(AI_DEPTH_TRIGGERS) >= 2

    def test_ai_trigger_keywords_not_empty(self):
        assert len(AI_TRIGGER_KEYWORDS) >= 10


# --- should_show_intelligence_goals Tests ---

class TestShouldShowIntelligenceGoals:
    def test_high_ai_depth_triggers(self):
        assert should_show_intelligence_goals("", [], "predictive_ml") is True

    def test_autonomous_ai_triggers(self):
        assert should_show_intelligence_goals("", [], "autonomous_ai") is True

    def test_ai_assisted_triggers(self):
        assert should_show_intelligence_goals("", [], "ai_assisted") is True

    def test_low_ai_depth_no_trigger(self):
        assert should_show_intelligence_goals("", [], "no_ai") is False

    def test_idea_with_ai_keyword(self):
        assert should_show_intelligence_goals("Build an AI chatbot", [], "") is True

    def test_idea_with_ml_keyword(self):
        assert should_show_intelligence_goals("Machine learning prediction engine", [], "") is True

    def test_idea_without_ai_keywords(self):
        assert should_show_intelligence_goals("Build a todo app", [], "") is False

    def test_features_with_ai_keyword(self, ai_features):
        assert should_show_intelligence_goals("", ai_features, "") is True

    def test_features_without_ai_keyword(self, non_ai_features):
        assert should_show_intelligence_goals("", non_ai_features, "") is False

    def test_empty_everything_no_trigger(self):
        assert should_show_intelligence_goals("", [], "") is False

    def test_recommendation_keyword_triggers(self):
        features = [{"name": "Recommendation engine", "description": "Suggests items"}]
        assert should_show_intelligence_goals("", features, "") is True


# --- _parse_goals_response Tests ---

class TestParseGoalsResponse:
    def test_valid_json_returns_goals(self, valid_goals_json):
        result = _parse_goals_response(valid_goals_json, "idea", [])
        assert len(result) == 4
        assert result[0]["id"] == "goal_1"
        assert result[0]["goal_type"] == "prediction"
        assert result[0]["user_facing_label"] == "Predict user churn"

    def test_canonical_field_names(self, valid_goals_json):
        result = _parse_goals_response(valid_goals_json, "idea", [])
        for goal in result:
            assert "goal_type" in goal
            assert "user_facing_label" in goal
            assert "confidence_required" in goal
            assert "impact_level" in goal

    def test_accepts_old_field_names(self):
        """Parser should accept LLM responses with old field names."""
        data = json.dumps({"goals": [
            {"id": f"g{i}", "label": f"Goal {i}", "description": f"Desc {i}", "type": "prediction"}
            for i in range(5)
        ]})
        result = _parse_goals_response(data, "idea", [])
        assert len(result) == 5
        assert result[0]["user_facing_label"] == "Goal 0"
        assert result[0]["goal_type"] == "prediction"

    def test_invalid_json_falls_back(self):
        result = _parse_goals_response("not json", "idea", [])
        assert len(result) >= 4

    def test_too_few_goals_falls_back(self):
        data = json.dumps({"goals": [
            {"id": "g1", "user_facing_label": "L", "description": "D", "goal_type": "prediction"},
        ]})
        result = _parse_goals_response(data, "Build an AI tool", [])
        assert len(result) >= 4

    def test_invalid_type_gets_corrected(self):
        data = json.dumps({"goals": [
            {"id": f"g{i}", "user_facing_label": f"Goal {i}", "description": f"Desc {i}", "goal_type": "invalid_type"}
            for i in range(5)
        ]})
        result = _parse_goals_response(data, "idea", [])
        for goal in result:
            assert goal["goal_type"] in GOAL_TYPES

    def test_truncates_long_labels(self):
        data = json.dumps({"goals": [
            {"id": f"g{i}", "user_facing_label": "A" * 200, "description": "D", "goal_type": "prediction"}
            for i in range(5)
        ]})
        result = _parse_goals_response(data, "idea", [])
        for goal in result:
            assert len(goal["user_facing_label"]) <= 100

    def test_max_8_goals(self):
        data = json.dumps({"goals": [
            {"id": f"g{i}", "user_facing_label": f"Goal {i}", "description": f"Desc {i}", "goal_type": "prediction"}
            for i in range(12)
        ]})
        result = _parse_goals_response(data, "idea", [])
        assert len(result) <= 8


# --- _fallback_goals Tests ---

class TestFallbackGoals:
    def test_returns_at_least_4_goals(self):
        result = _fallback_goals("Build a prediction engine", [])
        assert len(result) >= 4

    def test_uses_canonical_field_names(self):
        result = _fallback_goals("Build a prediction engine", [])
        for goal in result:
            assert "user_facing_label" in goal
            assert "goal_type" in goal
            assert "confidence_required" in goal
            assert "impact_level" in goal

    def test_prediction_keyword_triggers_prediction_goal(self):
        result = _fallback_goals("Build a prediction system", [])
        types = [g["goal_type"] for g in result]
        assert "prediction" in types

    def test_recommendation_keyword_triggers_goal(self):
        result = _fallback_goals("Personalized recommendation engine", [])
        ids = [g["id"] for g in result]
        assert "goal_recommend" in ids

    def test_nlp_keyword_triggers_goal(self):
        result = _fallback_goals("NLP chatbot platform", [])
        ids = [g["id"] for g in result]
        assert "goal_nlp" in ids

    def test_anomaly_keyword_triggers_goal(self):
        result = _fallback_goals("Fraud detection system", [])
        ids = [g["id"] for g in result]
        assert "goal_anomaly" in ids

    def test_empty_input_still_returns_4(self):
        result = _fallback_goals("", [])
        assert len(result) >= 4

    def test_generic_input_returns_generic_goals(self):
        result = _fallback_goals("", [])
        assert all("id" in g and "user_facing_label" in g and "goal_type" in g for g in result)

    def test_feature_text_checked(self):
        features = [{"name": "Sentiment Analysis", "description": "NLP for reviews"}]
        result = _fallback_goals("", features)
        ids = [g["id"] for g in result]
        assert "goal_nlp" in ids


# --- generate_intelligence_goals Tests ---

class TestGenerateIntelligenceGoals:
    @patch("execution.intelligence_goals.is_available", return_value=False)
    def test_fallback_when_llm_unavailable(self, mock_avail):
        result = generate_intelligence_goals("AI prediction tool", [], "predictive_ml")
        assert len(result) >= 4

    @patch("execution.intelligence_goals.chat")
    @patch("execution.intelligence_goals.is_available", return_value=True)
    def test_llm_call_returns_goals(self, mock_avail, mock_chat, valid_goals_json):
        from execution.llm_client import LLMResponse
        mock_chat.return_value = LLMResponse(
            content=valid_goals_json, model="gpt-4o-mini",
            usage={"prompt_tokens": 100, "completion_tokens": 200},
            stop_reason="stop",
        )
        result = generate_intelligence_goals("Build AI tool", [], "predictive_ml")
        assert len(result) == 4
        assert mock_chat.called

    @patch("execution.intelligence_goals.chat", side_effect=Exception("API error"))
    @patch("execution.intelligence_goals.is_available", return_value=True)
    def test_exception_falls_back(self, mock_avail, mock_chat):
        result = generate_intelligence_goals("AI tool", [], "predictive_ml")
        assert len(result) >= 4


# --- check_intelligence_goals_alignment Tests ---

class TestCheckIntelligenceGoalsAlignment:
    def test_no_goals_passes(self):
        result = check_intelligence_goals_alignment([], [])
        assert result["passed"] is True

    def test_goals_with_ai_features_passes(self, ai_features):
        goals = [{"id": "g1", "user_facing_label": "Predict churn", "goal_type": "prediction"}]
        result = check_intelligence_goals_alignment(goals, ai_features)
        assert result["passed"] is True

    def test_goals_without_ai_features_warns(self, non_ai_features):
        goals = [{"id": "g1", "user_facing_label": "Predict churn", "goal_type": "prediction"}]
        result = check_intelligence_goals_alignment(goals, non_ai_features)
        assert result["passed"] is False
        assert len(result["warnings"]) == 1

    def test_alignment_warning_message_matches_spec(self, non_ai_features):
        goals = [{"id": "g1", "user_facing_label": "Predict churn", "goal_type": "prediction"}]
        result = check_intelligence_goals_alignment(goals, non_ai_features)
        expected = (
            "This project includes intelligent behavior goals but no AI "
            "capabilities are selected. Consider enabling relevant AI features."
        )
        assert result["warnings"][0] == expected

    def test_empty_features_with_goals_warns(self):
        goals = [{"id": "g1", "user_facing_label": "Goal", "goal_type": "prediction"}]
        result = check_intelligence_goals_alignment(goals, [])
        assert result["passed"] is False


# --- Goal-Type Architecture Rules Tests ---

class TestGoalTypeArchitectureRules:
    def test_all_goal_types_have_rules(self):
        for gt in GOAL_TYPES:
            assert gt in GOAL_TYPE_ARCHITECTURE_RULES

    def test_each_rule_has_functional_requirements(self):
        for gt, rules in GOAL_TYPE_ARCHITECTURE_RULES.items():
            assert "functional_requirements" in rules
            assert len(rules["functional_requirements"]) > 0

    def test_each_rule_has_architecture_sections(self):
        for gt, rules in GOAL_TYPE_ARCHITECTURE_RULES.items():
            assert "architecture_sections" in rules
            assert len(rules["architecture_sections"]) >= 3

    def test_prediction_has_high_confidence_additions(self):
        rules = GOAL_TYPE_ARCHITECTURE_RULES["prediction"]
        assert "high_confidence_additions" in rules
        assert len(rules["high_confidence_additions"]) >= 2

    def test_forecasting_has_high_confidence_additions(self):
        rules = GOAL_TYPE_ARCHITECTURE_RULES["forecasting"]
        assert "high_confidence_additions" in rules


# --- build_intelligence_goals_prompt_section Tests ---

class TestBuildIntelligenceGoalsPromptSection:
    @pytest.fixture
    def sample_goals(self):
        return [
            {
                "id": "g1", "user_facing_label": "Predict user churn",
                "description": "Forecast which users will leave.",
                "goal_type": "prediction", "confidence_required": None,
                "impact_level": None,
            },
            {
                "id": "g2", "user_facing_label": "Recommend content",
                "description": "Suggest relevant items.",
                "goal_type": "recommendation", "confidence_required": None,
                "impact_level": None,
            },
        ]

    def test_empty_goals_returns_empty(self):
        result = build_intelligence_goals_prompt_section([], "Functional Requirements")
        assert result == ""

    def test_functional_requirements_chapter(self, sample_goals):
        result = build_intelligence_goals_prompt_section(sample_goals, "Functional Requirements")
        assert "Predict user churn" in result
        assert "Behavioral requirement:" in result
        assert "prediction inputs, outputs" in result

    def test_ai_architecture_chapter(self, sample_goals):
        result = build_intelligence_goals_prompt_section(sample_goals, "AI & Intelligence Architecture")
        assert "Predict user churn" in result
        assert "type: prediction" in result
        assert "Required architecture components:" in result

    def test_other_chapter_gets_brief_summary(self, sample_goals):
        result = build_intelligence_goals_prompt_section(sample_goals, "Executive Summary")
        assert result.startswith("Intelligence Goals:")
        assert "Predict user churn" in result
        assert "Recommend content" in result

    def test_functional_section_includes_recommendation_rules(self, sample_goals):
        result = _build_functional_section(sample_goals)
        assert "ranking criteria" in result

    def test_architecture_section_high_confidence_additions(self):
        goals = [{
            "id": "g1", "user_facing_label": "Predict churn",
            "description": "Forecast churn.",
            "goal_type": "prediction", "confidence_required": "high_confidence",
            "impact_level": None,
        }]
        result = _build_architecture_section(goals)
        assert "High-confidence additions:" in result
        assert "Shadow scoring pipeline" in result

    def test_architecture_section_no_additions_for_low_confidence(self):
        goals = [{
            "id": "g1", "user_facing_label": "Predict churn",
            "description": "Forecast churn.",
            "goal_type": "prediction", "confidence_required": "informational",
            "impact_level": None,
        }]
        result = _build_architecture_section(goals)
        assert "High-confidence additions:" not in result

    def test_intelligence_keyword_triggers_architecture(self):
        goals = [{"id": "g1", "user_facing_label": "Goal", "goal_type": "prediction",
                   "description": "D", "confidence_required": None, "impact_level": None}]
        result = build_intelligence_goals_prompt_section(goals, "AI & Intelligence Architecture")
        assert "architecture components" in result.lower() or "architecture" in result.lower()


# --- expansion_depth Tests ---

class TestExpansionDepth:
    @pytest.fixture
    def sample_goals(self):
        return [
            {
                "id": "g1", "user_facing_label": "Predict user churn",
                "description": "Forecast which users will leave.",
                "goal_type": "prediction", "confidence_required": "high_confidence",
                "impact_level": None,
            },
            {
                "id": "g2", "user_facing_label": "Recommend content",
                "description": "Suggest relevant items.",
                "goal_type": "recommendation", "confidence_required": None,
                "impact_level": None,
            },
        ]

    def test_brief_returns_labels_only(self, sample_goals):
        """Brief expansion should include goal labels but not architecture rules."""
        result = build_intelligence_goals_prompt_section(
            sample_goals, "Functional Requirements", expansion_depth="brief"
        )
        assert "Predict user churn" in result
        assert "Recommend content" in result
        # Brief should NOT include detailed rules
        assert "Behavioral requirement:" not in result

    def test_standard_returns_labels_and_descriptions(self, sample_goals):
        """Standard expansion should include labels and descriptions."""
        result = build_intelligence_goals_prompt_section(
            sample_goals, "Functional Requirements", expansion_depth="standard"
        )
        assert "Predict user churn" in result
        assert "Forecast which users will leave" in result

    def test_detailed_includes_full_rules(self, sample_goals):
        """Detailed expansion (default) should include architecture rules."""
        result = build_intelligence_goals_prompt_section(
            sample_goals, "Functional Requirements", expansion_depth="detailed"
        )
        assert "Predict user churn" in result
        assert "Behavioral requirement:" in result

    def test_comprehensive_includes_full_rules(self, sample_goals):
        """Comprehensive expansion should include rules plus elaboration."""
        result = build_intelligence_goals_prompt_section(
            sample_goals, "AI & Intelligence Architecture", expansion_depth="comprehensive"
        )
        assert "Predict user churn" in result
        assert "Required architecture components:" in result

    def test_default_expansion_preserves_current_behavior(self, sample_goals):
        """Default (no expansion_depth) should produce same result as 'detailed'."""
        default_result = build_intelligence_goals_prompt_section(
            sample_goals, "Functional Requirements"
        )
        detailed_result = build_intelligence_goals_prompt_section(
            sample_goals, "Functional Requirements", expansion_depth="detailed"
        )
        assert default_result == detailed_result

    def test_brief_functional_section(self, sample_goals):
        result = _build_functional_section(sample_goals, expansion_depth="brief")
        assert "Predict user churn" in result
        assert "Behavioral requirement:" not in result

    def test_brief_architecture_section(self, sample_goals):
        result = _build_architecture_section(sample_goals, expansion_depth="brief")
        assert "Predict user churn" in result
        assert "Required architecture components:" not in result
