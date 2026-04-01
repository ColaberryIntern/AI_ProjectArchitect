"""Tests for the lead scoring engine."""

import pytest

from execution.advisory.lead_scoring_engine import score_lead


def _make_lead(**overrides):
    base = {
        "email": "test@example.com",
        "name": "Test User",
        "company": "Acme Corp",
        "role": "",
        "company_size": "",
        "metadata": {},
        "advisory_session_ids": [],
        "pdf_paths": [],
    }
    base.update(overrides)
    return base


class TestScoreLead:
    def test_returns_required_fields(self):
        result = score_lead(_make_lead())
        assert "lead_score" in result
        assert "breakdown" in result
        assert "tier" in result
        assert 0 <= result["lead_score"] <= 100

    def test_ceo_scores_high_on_role(self):
        result = score_lead(_make_lead(role="CEO"))
        assert result["breakdown"]["role_score"] == 25

    def test_vp_scores_medium_on_role(self):
        result = score_lead(_make_lead(role="VP of Operations"))
        assert result["breakdown"]["role_score"] == 20

    def test_manager_scores_lower(self):
        result = score_lead(_make_lead(role="Project Manager"))
        assert result["breakdown"]["role_score"] == 12

    def test_unknown_role_gets_minimal(self):
        result = score_lead(_make_lead(role=""))
        assert result["breakdown"]["role_score"] == 5

    def test_enterprise_company_scores_high(self):
        result = score_lead(_make_lead(company_size="5000 employees"))
        assert result["breakdown"]["company_score"] == 25

    def test_small_company_scores_low(self):
        result = score_lead(_make_lead(company_size="15"))
        assert result["breakdown"]["company_score"] == 6

    def test_high_roi_scores_well(self):
        lead = _make_lead(metadata={
            "estimated_roi_3yr": 500,
            "estimated_annual_savings": 600000,
            "estimated_revenue_lift": 200000,
        })
        result = score_lead(lead)
        assert result["breakdown"]["roi_score"] >= 15

    def test_many_departments_scores_well(self):
        lead = _make_lead(metadata={"key_departments": ["Ops", "Sales", "Finance", "HR", "Tech"]})
        result = score_lead(lead)
        assert result["breakdown"]["department_score"] == 15

    def test_mid_maturity_is_sweet_spot(self):
        lead_mid = _make_lead(metadata={"maturity_score": 2.5})
        lead_high = _make_lead(metadata={"maturity_score": 4.5})
        mid = score_lead(lead_mid)["breakdown"]["maturity_score"]
        high = score_lead(lead_high)["breakdown"]["maturity_score"]
        assert mid > high  # Mid-range scores higher (ready to buy)

    def test_engagement_signals_add_points(self):
        lead = _make_lead(
            advisory_session_ids=["s1"],
            pdf_paths=["/path/to/pdf"],
            metadata={"idea_input": "Build AI system"},
        )
        result = score_lead(lead)
        assert result["breakdown"]["engagement_score"] == 5


class TestScoringTiers:
    def test_hot_lead(self):
        """CEO of enterprise with high ROI across many departments."""
        lead = _make_lead(
            role="CEO",
            company_size="5000",
            advisory_session_ids=["s1"],
            pdf_paths=["/pdf"],
            metadata={
                "estimated_roi_3yr": 500,
                "estimated_annual_savings": 800000,
                "estimated_revenue_lift": 500000,
                "key_departments": ["Ops", "Sales", "Finance", "HR", "Tech"],
                "maturity_score": 2.8,
                "idea_input": "Transform operations",
            },
        )
        result = score_lead(lead)
        assert result["tier"] == "hot"
        assert result["lead_score"] >= 80

    def test_cold_lead(self):
        """Minimal information, no engagement."""
        result = score_lead(_make_lead())
        assert result["tier"] in ("cold", "nurture")
        assert result["lead_score"] < 40
