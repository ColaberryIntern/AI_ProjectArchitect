"""Tests for the offer classification engine."""

import pytest

from execution.advisory.offer_router import OFFER_TIERS, classify_lead


def _make_lead(**overrides):
    base = {
        "email": "test@example.com",
        "name": "Test User",
        "role": "",
        "company_size": "",
        "metadata": {},
    }
    base.update(overrides)
    return base


class TestClassifyLead:
    def test_returns_required_fields(self):
        result = classify_lead(_make_lead())
        assert "recommended_offer" in result
        assert "offer_label" in result
        assert "confidence" in result
        assert "reasoning" in result
        assert result["recommended_offer"] in OFFER_TIERS

    def test_enterprise_for_large_high_roi_multi_dept(self):
        lead = _make_lead(
            role="CIO",
            company_size="5000",
            metadata={
                "estimated_roi_3yr": 400,
                "estimated_annual_savings": 500000,
                "estimated_revenue_lift": 300000,
                "key_departments": ["Ops", "Sales", "Finance", "HR", "Tech"],
                "total_ai_roles": 15,
                "idea_input": "Transform the whole company",
            },
        )
        result = classify_lead(lead)
        assert result["recommended_offer"] == "enterprise"

    def test_custom_build_for_strong_use_case(self):
        lead = _make_lead(
            role="Director of Operations",
            company_size="200",
            metadata={
                "estimated_annual_savings": 150000,
                "estimated_revenue_lift": 50000,
                "key_departments": ["Ops", "Sales"],
                "maturity_score": 3.0,
                "idea_input": "Automate our logistics routing",
            },
        )
        result = classify_lead(lead)
        assert result["recommended_offer"] in ("custom_build", "enterprise")

    def test_advisory_for_senior_exec_early_stage(self):
        lead = _make_lead(
            role="CEO",
            company_size="100",
            metadata={
                "maturity_score": 1.5,
                "idea_input": "Explore AI for our business",
            },
        )
        result = classify_lead(lead)
        assert result["recommended_offer"] in ("advisory", "custom_build")

    def test_accelerator_for_small_team(self):
        lead = _make_lead(
            role="Developer",
            company_size="5",
            metadata={},
        )
        result = classify_lead(lead)
        assert result["recommended_offer"] == "accelerator"

    def test_all_offers_have_pipeline(self):
        for tier_id, tier_info in OFFER_TIERS.items():
            assert "campaign_pipeline" in tier_info
            assert tier_info["campaign_pipeline"]
