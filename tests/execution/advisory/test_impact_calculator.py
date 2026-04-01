"""Tests for the financial impact calculator."""

import pytest

from execution.advisory.impact_calculator import (
    _extract_budget,
    _parse_budget_text,
    calculate_impact,
    format_currency,
)


def _sample_capability_map():
    return {
        "departments": [
            {
                "id": "operations",
                "name": "Operations",
                "capabilities": [
                    {"id": "ops_1", "name": "Workflow Automation", "automation_potential": "high"},
                    {"id": "ops_2", "name": "Resource Scheduling", "automation_potential": "medium"},
                ],
            },
            {
                "id": "sales",
                "name": "Sales",
                "capabilities": [
                    {"id": "sales_1", "name": "Lead Qualification", "automation_potential": "high"},
                ],
            },
            {
                "id": "customer_support",
                "name": "Customer Support",
                "capabilities": [
                    {"id": "cs_1", "name": "Ticket Triage", "automation_potential": "high"},
                    {"id": "cs_2", "name": "Chatbot", "automation_potential": "high"},
                ],
            },
        ],
    }


def _sample_maturity():
    return {"overall": 2.5, "dimensions": {"data_readiness": 2, "process_maturity": 3}}


def _sample_answers():
    return [
        {"question_id": "q7_budget", "question_text": "Budget?", "answer_text": "Around $200k per year"},
    ]


class TestCalculateImpact:
    def test_returns_all_sections(self):
        result = calculate_impact(
            _sample_capability_map(), _sample_maturity(), _sample_answers()
        )
        assert "cost_savings" in result
        assert "revenue_impact" in result
        assert "efficiency_gains" in result
        assert "opportunity_cost" in result
        assert "roi_summary" in result
        assert "calculated_at" in result

    def test_cost_savings_are_positive(self):
        result = calculate_impact(
            _sample_capability_map(), _sample_maturity(), _sample_answers()
        )
        assert result["cost_savings"]["total_annual"] > 0
        assert result["cost_savings"]["annual_labor_savings"] > 0

    def test_cost_savings_have_breakdown(self):
        result = calculate_impact(
            _sample_capability_map(), _sample_maturity(), _sample_answers()
        )
        assert len(result["cost_savings"]["breakdown"]) > 0
        for item in result["cost_savings"]["breakdown"]:
            assert "department" in item
            assert "capability" in item
            assert "annual_savings" in item

    def test_revenue_impact_is_positive(self):
        result = calculate_impact(
            _sample_capability_map(), _sample_maturity(), _sample_answers()
        )
        assert result["revenue_impact"]["estimated_annual_revenue_gain"] > 0

    def test_efficiency_gains_are_positive(self):
        result = calculate_impact(
            _sample_capability_map(), _sample_maturity(), _sample_answers()
        )
        assert result["efficiency_gains"]["time_saved_hours_per_week"] > 0
        assert result["efficiency_gains"]["processes_automated"] > 0

    def test_roi_summary_has_payback(self):
        result = calculate_impact(
            _sample_capability_map(), _sample_maturity(), _sample_answers()
        )
        assert result["roi_summary"]["payback_period_months"] > 0
        assert result["roi_summary"]["three_year_roi_percent"] >= 0

    def test_higher_maturity_means_different_savings(self):
        low_maturity = {"overall": 1.5, "dimensions": {}}
        high_maturity = {"overall": 4.5, "dimensions": {}}
        low_result = calculate_impact(_sample_capability_map(), low_maturity, _sample_answers())
        high_result = calculate_impact(_sample_capability_map(), high_maturity, _sample_answers())
        # Lower maturity = more room for savings
        assert low_result["cost_savings"]["total_annual"] > high_result["cost_savings"]["total_annual"]


class TestParseBudget:
    @pytest.mark.parametrize("text,expected", [
        ("$200k", 200_000),
        ("200k per year", 200_000),
        ("$1.5M annually", 1_500_000),
        ("1.5 million", 1_500_000),
        ("$500,000", 500_000),
        ("about 300k", 300_000),
        ("50", 50_000),  # Small number assumed to be thousands
    ])
    def test_parses_various_formats(self, text, expected):
        result = _parse_budget_text(text)
        assert result == expected

    def test_default_on_unparseable(self):
        result = _parse_budget_text("not sure")
        assert result == 100_000

    def test_extract_budget_from_answers(self):
        answers = [{"question_id": "q7_budget", "answer_text": "$500k"}]
        assert _extract_budget(answers) == 500_000

    def test_extract_budget_default_when_missing(self):
        assert _extract_budget([]) == 100_000


class TestFormatCurrency:
    @pytest.mark.parametrize("amount,expected", [
        (1_500_000, "$1.5M"),
        (500_000, "$500K"),
        (75_000, "$75K"),
        (999, "$999"),
    ])
    def test_formats_correctly(self, amount, expected):
        assert format_currency(amount) == expected
