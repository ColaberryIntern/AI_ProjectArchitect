"""Tests for the business interpretation engine."""

import pytest


def _sample_answers():
    """Return a set of sample advisory answers."""
    return [
        {"question_id": "q1_business_overview", "question_text": "What does your business do?",
         "answer_text": "We run a logistics company that handles shipping and warehouse operations for e-commerce businesses."},
        {"question_id": "q2_bottlenecks", "question_text": "Bottlenecks?",
         "answer_text": "Manual route planning is slow and error-prone. Inventory tracking is done via spreadsheets."},
        {"question_id": "q3_customer_interaction", "question_text": "Customer interactions?",
         "answer_text": "We handle customer support through email and phone. Response times are slow."},
        {"question_id": "q4_repetitive_tasks", "question_text": "Repetitive tasks?",
         "answer_text": "Data entry, invoice processing, and manual scheduling consume most of our team's time."},
        {"question_id": "q5_tech_stack", "question_text": "Tech stack?",
         "answer_text": "We use a basic ERP system, Excel for tracking, and email for communication."},
        {"question_id": "q6_performance_metrics", "question_text": "Performance metrics?",
         "answer_text": "We track delivery times and customer complaints but mostly in spreadsheets."},
        {"question_id": "q7_budget", "question_text": "Budget?",
         "answer_text": "Around $200k per year for technology."},
        {"question_id": "q8_competitors", "question_text": "Competitors?",
         "answer_text": "Major competitors are using AI for route optimization and demand forecasting."},
        {"question_id": "q9_success_vision", "question_text": "Success vision?",
         "answer_text": "Cut delivery costs by 30% and improve customer satisfaction scores."},
        {"question_id": "q10_barriers", "question_text": "Barriers?",
         "answer_text": "Lack of technical expertise and unclear ROI have been the main barriers."},
    ]


class TestFallbackCapabilityMap:
    def test_produces_departments(self):
        from execution.advisory.business_interpreter import _fallback_capability_map

        answers = _sample_answers()
        result = _fallback_capability_map(answers, "AI-powered logistics platform")
        assert "departments" in result
        assert len(result["departments"]) >= 2

    def test_always_includes_operations_and_technology(self):
        from execution.advisory.business_interpreter import _fallback_capability_map

        result = _fallback_capability_map([], "generic business")
        dept_ids = {d["id"] for d in result["departments"]}
        assert "operations" in dept_ids
        assert "technology" in dept_ids

    def test_departments_have_capabilities(self):
        from execution.advisory.business_interpreter import _fallback_capability_map

        result = _fallback_capability_map(_sample_answers(), "logistics company")
        for dept in result["departments"]:
            assert "capabilities" in dept
            assert len(dept["capabilities"]) >= 1
            for cap in dept["capabilities"]:
                assert "name" in cap
                assert "automation_potential" in cap
                assert cap["automation_potential"] in ("high", "medium", "low")

    def test_detects_relevant_departments_from_keywords(self):
        from execution.advisory.business_interpreter import _fallback_capability_map

        answers = _sample_answers()
        result = _fallback_capability_map(answers, "logistics and shipping")
        dept_ids = {d["id"] for d in result["departments"]}
        # Logistics answers mention shipping, customer, finance keywords
        assert "operations" in dept_ids
        assert "customer_support" in dept_ids

    def test_has_generated_at_timestamp(self):
        from execution.advisory.business_interpreter import _fallback_capability_map

        result = _fallback_capability_map([], "test")
        assert "generated_at" in result


class TestInterpretAnswers:
    def test_interpret_answers_returns_capability_map(self, mocker):
        from execution.advisory.business_interpreter import interpret_answers

        # Mock LLM to fail so we get fallback
        mocker.patch(
            "execution.advisory.business_interpreter._llm_interpret",
            side_effect=Exception("LLM unavailable"),
        )
        result = interpret_answers(_sample_answers(), "logistics platform")
        assert "departments" in result
        assert len(result["departments"]) >= 2

    def test_llm_path_when_available(self, mocker):
        from execution.advisory.business_interpreter import interpret_answers

        mock_result = {
            "departments": [{"id": "ops", "name": "Operations", "capabilities": []}],
            "generated_at": "2025-01-01T00:00:00+00:00",
        }
        mocker.patch(
            "execution.advisory.business_interpreter._llm_interpret",
            return_value=mock_result,
        )
        result = interpret_answers(_sample_answers(), "test")
        assert result == mock_result
