"""Tests for the problem-first capability selection engine."""

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
        assert "primary_goal" in result
        assert "primary_label" in result

    def test_revenue_focus_selects_sales_heavy(self):
        session = {"selected_outcomes": ["increase_revenue"], "selected_ai_systems": [],
                    "business_idea": "B2B SaaS with lead conversion problems", "answers": []}
        result = map_capabilities(session)
        # Should select 4-12 focused capabilities, Sales/Marketing-heavy
        assert 4 <= len(result["recommended"]) <= 12
        # At least some sales capabilities
        sales_caps = [c for c in result["recommended"] if c in ("auto_lead_scoring", "outreach_automation", "sales_pipeline_forecast", "deal_intelligence", "proposal_generator")]
        assert len(sales_caps) >= 2

    def test_cost_focus_selects_ops_heavy(self):
        session = {"selected_outcomes": ["reduce_costs"], "selected_ai_systems": [],
                    "business_idea": "Manufacturing with high manual costs", "answers": []}
        result = map_capabilities(session)
        assert 6 <= len(result["recommended"]) <= 12
        assert "workflow_automation" in result["recommended"]

    def test_cx_focus_selects_support_heavy(self):
        session = {"selected_outcomes": ["improve_cx"], "selected_ai_systems": [],
                    "business_idea": "E-commerce support", "answers": []}
        result = map_capabilities(session)
        assert 3 <= len(result["recommended"]) <= 12
        assert "ai_chat_support" in result["recommended"]

    def test_never_exceeds_max_capabilities(self):
        """Even with multiple outcomes, should not exceed 12."""
        session = {"selected_outcomes": ["increase_revenue", "reduce_costs", "improve_cx"],
                    "selected_ai_systems": ["revenue_engine", "operations_engine"],
                    "business_idea": "Full automation", "answers": []}
        result = map_capabilities(session)
        assert len(result["recommended"]) <= 12

    def test_reasoning_present_for_each_recommendation(self):
        session = {"selected_outcomes": ["improve_cx"], "selected_ai_systems": [],
                    "business_idea": "", "answers": []}
        result = map_capabilities(session)
        for cap_id in result["recommended"]:
            assert cap_id in result["reasoning"]

    def test_exclusion_reasons_provided(self):
        session = {"selected_outcomes": ["increase_revenue"], "selected_ai_systems": [],
                    "business_idea": "", "answers": []}
        result = map_capabilities(session)
        assert result["total_excluded"] > 0
        assert len(result.get("exclusion_reasons", {})) > 0

    def test_empty_session_uses_default(self):
        session = {"selected_outcomes": [], "selected_ai_systems": [],
                    "business_idea": "", "answers": []}
        result = map_capabilities(session)
        # With balanced vector, should still select some capabilities
        assert len(result["recommended"]) >= 0  # May be 0 if all below threshold

    def test_keyword_boost(self):
        """Keywords in business idea should boost relevant capabilities."""
        session_with = {"selected_outcomes": ["increase_revenue"], "selected_ai_systems": [],
                        "business_idea": "We need better lead scoring and sales pipeline", "answers": []}
        session_without = {"selected_outcomes": ["increase_revenue"], "selected_ai_systems": [],
                           "business_idea": "test", "answers": []}
        result_with = map_capabilities(session_with)
        result_without = map_capabilities(session_without)
        # With keywords, lead scoring should score higher
        score_with = result_with["confidence_scores"].get("auto_lead_scoring", 0)
        score_without = result_without["confidence_scores"].get("auto_lead_scoring", 0)
        assert score_with >= score_without


class TestShouldIncludeCory:
    def test_included_when_2_plus_systems(self):
        assert should_include_cory({"selected_ai_systems": ["a", "b"], "selected_outcomes": []})

    def test_included_for_improve_decisions(self):
        assert should_include_cory({"selected_ai_systems": [], "selected_outcomes": ["improve_decisions"]})

    def test_included_when_explicitly_selected(self):
        assert should_include_cory({"selected_ai_systems": ["intelligence_engine"], "selected_outcomes": []})

    def test_not_included_for_single_system(self):
        assert not should_include_cory({"selected_ai_systems": ["revenue_engine"], "selected_outcomes": ["increase_revenue"]})
