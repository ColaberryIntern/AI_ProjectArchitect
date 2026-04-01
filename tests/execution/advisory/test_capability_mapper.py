"""Tests for the capability auto-selection engine."""

from execution.advisory.capability_mapper import (
    AI_SYSTEMS,
    BUSINESS_OUTCOMES,
    map_capabilities,
    should_include_cory,
)


class TestBusinessOutcomes:
    def test_outcomes_defined(self):
        assert len(BUSINESS_OUTCOMES) >= 5
        for o in BUSINESS_OUTCOMES:
            assert "id" in o
            assert "label" in o
            assert "icon" in o


class TestAISystems:
    def test_systems_defined(self):
        assert len(AI_SYSTEMS) >= 6
        for s in AI_SYSTEMS:
            assert "id" in s
            assert "label" in s
            assert "departments" in s


class TestMapCapabilities:
    def test_returns_required_fields(self):
        session = {"selected_outcomes": ["increase_revenue"], "selected_ai_systems": ["revenue_engine"],
                    "business_idea": "test", "answers": []}
        result = map_capabilities(session)
        assert "recommended" in result
        assert "optional" in result
        assert "confidence_scores" in result
        assert "reasoning" in result

    def test_revenue_outcome_recommends_sales_caps(self):
        session = {"selected_outcomes": ["increase_revenue"], "selected_ai_systems": [],
                    "business_idea": "", "answers": []}
        result = map_capabilities(session)
        assert "auto_lead_scoring" in result["recommended"]
        assert "sales_pipeline_forecast" in result["recommended"]

    def test_reduce_costs_recommends_ops_caps(self):
        session = {"selected_outcomes": ["reduce_costs"], "selected_ai_systems": [],
                    "business_idea": "", "answers": []}
        result = map_capabilities(session)
        assert "workflow_automation" in result["recommended"]

    def test_system_mapping_adds_caps(self):
        session = {"selected_outcomes": [], "selected_ai_systems": ["customer_engine"],
                    "business_idea": "", "answers": []}
        result = map_capabilities(session)
        assert "ai_chat_support" in result["recommended"]

    def test_keyword_matching_from_idea(self):
        session = {"selected_outcomes": [], "selected_ai_systems": [],
                    "business_idea": "We need better lead management and hiring",
                    "answers": []}
        result = map_capabilities(session)
        all_caps = result["recommended"] + result["optional"]
        assert "auto_lead_scoring" in all_caps
        assert "resume_screening" in all_caps

    def test_combined_scoring_boosts_confidence(self):
        session = {
            "selected_outcomes": ["increase_revenue"],
            "selected_ai_systems": ["revenue_engine"],
            "business_idea": "sales optimization platform",
            "answers": [],
        }
        result = map_capabilities(session)
        # auto_lead_scoring should have high confidence (hit by outcome + system + keyword)
        assert result["confidence_scores"].get("auto_lead_scoring", 0) >= 80

    def test_reasoning_explains_selections(self):
        session = {"selected_outcomes": ["improve_cx"], "selected_ai_systems": [],
                    "business_idea": "", "answers": []}
        result = map_capabilities(session)
        assert "ai_chat_support" in result["reasoning"]
        reasons = result["reasoning"]["ai_chat_support"]
        assert any("goal" in r.lower() for r in reasons)

    def test_empty_session_returns_empty(self):
        session = {"selected_outcomes": [], "selected_ai_systems": [],
                    "business_idea": "", "answers": []}
        result = map_capabilities(session)
        assert result["recommended"] == []


class TestShouldIncludeCory:
    def test_included_when_2_plus_systems(self):
        assert should_include_cory({"selected_ai_systems": ["a", "b"], "selected_outcomes": []})

    def test_included_for_improve_decisions(self):
        assert should_include_cory({"selected_ai_systems": [], "selected_outcomes": ["improve_decisions"]})

    def test_included_when_explicitly_selected(self):
        assert should_include_cory({"selected_ai_systems": ["intelligence_engine"], "selected_outcomes": []})

    def test_not_included_for_single_system(self):
        assert not should_include_cory({"selected_ai_systems": ["revenue_engine"], "selected_outcomes": ["increase_revenue"]})
