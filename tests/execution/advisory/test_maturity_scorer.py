"""Tests for the maturity scoring engine."""

import pytest

from execution.advisory.maturity_scorer import (
    DIMENSIONS,
    get_dimension_label,
    get_maturity_label,
    score_maturity,
)


def _make_answers(**overrides):
    """Build a set of answers with specified text overrides by question_id."""
    defaults = {
        "q1_business_overview": "We run a logistics company",
        "q2_company_size": "200 employees",
        "q3_departments": "Sales, Operations, Customer Support",
        "q4_bottlenecks": "Manual processes and slow routing",
        "q5_customer_journey": "Email and phone support",
        "q6_current_tools": "Basic ERP, Excel spreadsheets",
        "q7_data_systems": "We track KPIs in spreadsheets",
        "q8_manual_processes": "Data entry and scheduling",
        "q9_budget_timeline": "$200k per year, 6 months",
        "q10_success_vision": "Cut costs by 30%, improve efficiency",
    }
    defaults.update(overrides)
    return [
        {"question_id": qid, "question_text": "Q", "answer_text": text}
        for qid, text in defaults.items()
    ]


class TestScoreMaturity:
    def test_returns_all_dimensions(self):
        answers = _make_answers()
        result = score_maturity(answers)
        assert "overall" in result
        assert "dimensions" in result
        assert "scored_at" in result
        for dim in DIMENSIONS:
            assert dim in result["dimensions"]

    def test_overall_is_between_1_and_5(self):
        answers = _make_answers()
        result = score_maturity(answers)
        assert 1 <= result["overall"] <= 5

    def test_each_dimension_is_between_1_and_5(self):
        answers = _make_answers()
        result = score_maturity(answers)
        for dim in DIMENSIONS:
            assert 1 <= result["dimensions"][dim] <= 5

    def test_high_tech_answers_score_higher(self):
        low_tech = _make_answers(
            q6_current_tools="Paper records and manual processes",
            q7_data_systems="We don't track anything",
        )
        high_tech = _make_answers(
            q6_current_tools="AWS cloud, Kubernetes, microservices, CI/CD pipeline, Terraform",
            q7_data_systems="Real-time dashboards with Tableau and BigQuery data warehouse",
        )
        low_result = score_maturity(low_tech)
        high_result = score_maturity(high_tech)
        assert high_result["dimensions"]["tech_infrastructure"] > low_result["dimensions"]["tech_infrastructure"]
        assert high_result["dimensions"]["data_readiness"] > low_result["dimensions"]["data_readiness"]

    def test_advanced_team_scores_higher(self):
        no_team = _make_answers(q10_success_vision="No expertise, never tried, don't know where to start")
        ai_team = _make_answers(q10_success_vision="We have a data team and have run pilot projects with ML engineers")
        low_result = score_maturity(no_team)
        high_result = score_maturity(ai_team)
        assert high_result["dimensions"]["team_capability"] > low_result["dimensions"]["team_capability"]

    def test_empty_answers_return_low_scores(self):
        answers = _make_answers(
            q4_bottlenecks="", q8_manual_processes="",
            q6_current_tools="", q7_data_systems="",
            q9_budget_timeline="", q10_success_vision="",
        )
        result = score_maturity(answers)
        assert result["overall"] <= 2.5


class TestMaturityLabels:
    @pytest.mark.parametrize("score,expected", [
        (5.0, "Leading"),
        (4.5, "Leading"),
        (4.0, "Advanced"),
        (3.5, "Advanced"),
        (3.0, "Developing"),
        (2.5, "Developing"),
        (2.0, "Emerging"),
        (1.5, "Emerging"),
        (1.0, "Nascent"),
    ])
    def test_label_mapping(self, score, expected):
        assert get_maturity_label(score) == expected


class TestDimensionLabels:
    def test_all_dimensions_have_labels(self):
        for dim in DIMENSIONS:
            label = get_dimension_label(dim)
            assert label
            assert "_" not in label  # Should be human-readable
