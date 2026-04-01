"""Tests for the sales intelligence generator."""

import pytest

from execution.advisory.sales_intelligence import generate_sales_context


def _make_lead(**overrides):
    base = {
        "email": "alice@acme.com",
        "name": "Alice Smith",
        "company": "Acme Corp",
        "role": "CEO",
        "industry": "logistics",
        "metadata": {
            "idea_input": "Build an AI-powered logistics optimization platform to reduce manual routing",
            "maturity_score": 2.8,
            "estimated_annual_savings": 250000,
            "estimated_revenue_lift": 150000,
            "key_departments": ["Operations", "Sales", "Customer Support"],
            "total_ai_roles": 8,
            "total_fte_equivalent": 6.5,
        },
    }
    base.update(overrides)
    return base


class TestGenerateSalesContext:
    def test_returns_all_sections(self):
        result = generate_sales_context(_make_lead())
        assert "summary" in result
        assert "pain_points" in result
        assert "recommended_angle" in result
        assert "likely_objections" in result
        assert "close_strategy" in result

    def test_summary_includes_name_and_company(self):
        result = generate_sales_context(_make_lead())
        assert "Alice Smith" in result["summary"]
        assert "Acme Corp" in result["summary"]

    def test_summary_includes_impact_numbers(self):
        result = generate_sales_context(_make_lead())
        # Should mention savings or revenue
        assert "$" in result["summary"]

    def test_pain_points_are_relevant(self):
        result = generate_sales_context(_make_lead())
        assert len(result["pain_points"]) >= 1
        # Should detect "manual" from the idea_input
        found_manual = any("manual" in p.lower() or "Manual" in p for p in result["pain_points"])
        found_ops = any("operat" in p.lower() for p in result["pain_points"])
        assert found_manual or found_ops

    def test_objections_have_responses(self):
        result = generate_sales_context(_make_lead())
        assert len(result["likely_objections"]) >= 2
        for obj in result["likely_objections"]:
            assert "objection" in obj
            assert "response" in obj
            assert len(obj["response"]) > 20

    def test_close_strategy_is_actionable(self):
        result = generate_sales_context(_make_lead())
        assert len(result["close_strategy"]) > 50

    def test_cfo_gets_numbers_focused_angle(self):
        lead = _make_lead(role="CFO")
        result = generate_sales_context(lead)
        assert "$" in result["recommended_angle"]

    def test_cto_gets_technical_angle(self):
        lead = _make_lead(role="CTO")
        result = generate_sales_context(lead)
        assert "technical" in result["recommended_angle"].lower() or "architecture" in result["recommended_angle"].lower()

    def test_minimal_lead_still_produces_output(self):
        lead = {"email": "test@example.com", "name": "", "company": "", "role": "", "metadata": {}}
        result = generate_sales_context(lead)
        assert result["summary"]
        assert result["close_strategy"]


class TestRevenuePipeline:
    """Test the full pipeline integration."""

    @pytest.fixture
    def advisory_output_dir(self, monkeypatch, tmp_path):
        import config.settings as settings
        import execution.advisory.advisory_state_manager as asm
        import execution.advisory.campaign_manager as cm
        import execution.advisory.event_tracker as et
        import execution.advisory.lead_manager as lm

        advisory_dir = tmp_path / "advisory"
        advisory_dir.mkdir()
        monkeypatch.setattr(settings, "ADVISORY_OUTPUT_DIR", advisory_dir)
        monkeypatch.setattr(asm, "ADVISORY_OUTPUT_DIR", advisory_dir)
        monkeypatch.setattr(lm, "_LEADS_DB_PATH", advisory_dir / "_leads_db.json")
        monkeypatch.setattr(cm, "_CAMPAIGNS_DB_PATH", advisory_dir / "_campaigns_db.json")
        monkeypatch.setattr(et, "_EVENTS_LOG_PATH", advisory_dir / "_events_log.json")
        return advisory_dir

    def test_full_pipeline_enriches_lead(self, advisory_output_dir):
        from execution.advisory.lead_manager import create_lead, get_lead_by_email
        from execution.advisory.revenue_pipeline import run_revenue_pipeline

        create_lead(
            "alice@acme.com",
            name="Alice Smith",
            company="Acme Corp",
            role="CEO",
            company_size="500",
        )
        # Add metadata manually (normally done by advisory_to_lead_mapper)
        from execution.advisory.lead_manager import add_lead_metadata
        add_lead_metadata("alice@acme.com", {
            "maturity_score": 3.0,
            "estimated_annual_savings": 200000,
            "estimated_revenue_lift": 100000,
            "estimated_roi_3yr": 250,
            "key_departments": ["Operations", "Sales", "Finance"],
            "total_ai_roles": 8,
            "idea_input": "AI-powered logistics",
        })

        result = run_revenue_pipeline("alice@acme.com")
        assert result is not None
        assert result["score"]["lead_score"] > 50
        assert result["offer"]["recommended_offer"] in ("enterprise", "custom_build", "advisory")
        assert result["sales_context"]["summary"]

        # Verify stored on lead
        lead = get_lead_by_email("alice@acme.com")
        assert lead["lead_score"] is not None
        assert lead["recommended_offer"] is not None
        assert lead["sales_intelligence"] is not None

    def test_pipeline_returns_none_for_unknown_lead(self, advisory_output_dir):
        from execution.advisory.revenue_pipeline import run_revenue_pipeline

        assert run_revenue_pipeline("nobody@example.com") is None
