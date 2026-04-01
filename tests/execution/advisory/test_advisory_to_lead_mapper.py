"""Tests for the advisory-to-lead mapper."""

import pytest


@pytest.fixture
def advisory_output_dir(monkeypatch, tmp_path):
    """Redirect all advisory output paths to temp directory."""
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


def _make_session(email="alice@example.com"):
    """Build a complete advisory session dict for testing."""
    return {
        "session_id": "test-session-123",
        "status": "gated",
        "business_idea": "AI-powered logistics platform for last-mile delivery",
        "answers": [
            {"question_id": "q1_business_overview", "question_text": "Q1",
             "answer_text": "We run a logistics company in the transportation industry"},
        ],
        "lead": {
            "name": "Alice Smith",
            "email": email,
            "company": "Acme Logistics",
            "role": "CEO",
        },
        "maturity_score": {
            "overall": 3.2,
            "dimensions": {"data_readiness": 3, "process_maturity": 4},
        },
        "impact_model": {
            "cost_savings": {"total_annual": 250000},
            "revenue_impact": {"estimated_annual_revenue_gain": 400000},
            "roi_summary": {"three_year_roi_percent": 320, "payback_period_months": 8},
        },
        "capability_map": {
            "departments": [
                {"name": "Operations", "capabilities": []},
                {"name": "Sales", "capabilities": []},
            ],
        },
        "org_structure": [
            {"id": "1", "title": "AI Control Tower", "estimated_fte_equivalent": 1.0},
            {"id": "2", "title": "AI Ops Manager", "estimated_fte_equivalent": 2.0},
        ],
        "pdf_path": "/output/advisory/test/report.pdf",
    }


class TestMapAdvisoryToLead:
    def test_creates_lead_from_session(self, advisory_output_dir):
        from execution.advisory.advisory_to_lead_mapper import map_advisory_to_lead
        from execution.advisory.lead_manager import get_lead_by_email

        session = _make_session()
        lead = map_advisory_to_lead(session)
        assert lead is not None
        assert lead["email"] == "alice@example.com"

        # Verify persisted
        stored = get_lead_by_email("alice@example.com")
        assert stored is not None
        assert stored["name"] == "Alice Smith"

    def test_links_advisory_session(self, advisory_output_dir):
        from execution.advisory.advisory_to_lead_mapper import map_advisory_to_lead
        from execution.advisory.lead_manager import get_lead_by_email

        map_advisory_to_lead(_make_session())
        lead = get_lead_by_email("alice@example.com")
        assert "test-session-123" in lead["advisory_session_ids"]

    def test_stores_sales_metadata(self, advisory_output_dir):
        from execution.advisory.advisory_to_lead_mapper import map_advisory_to_lead
        from execution.advisory.lead_manager import get_lead_by_email

        map_advisory_to_lead(_make_session())
        lead = get_lead_by_email("alice@example.com")
        metadata = lead["metadata"]
        assert metadata["maturity_score"] == 3.2
        assert metadata["estimated_annual_savings"] == 250000
        assert metadata["estimated_revenue_lift"] == 400000
        assert "Operations" in metadata["key_departments"]
        assert metadata["total_ai_roles"] == 2

    def test_enrolls_in_campaign(self, advisory_output_dir):
        from execution.advisory.advisory_to_lead_mapper import map_advisory_to_lead
        from execution.advisory.campaign_manager import get_enrollments_by_email

        map_advisory_to_lead(_make_session())
        enrollments = get_enrollments_by_email("alice@example.com")
        assert len(enrollments) == 1
        # Revenue pipeline runs after mapping, advancing stage beyond "Captured Lead"
        assert "Captured Lead" in [s["stage"] for s in enrollments[0]["stage_history"]]

    def test_attaches_pdf(self, advisory_output_dir):
        from execution.advisory.advisory_to_lead_mapper import map_advisory_to_lead
        from execution.advisory.lead_manager import get_lead_by_email

        map_advisory_to_lead(_make_session())
        lead = get_lead_by_email("alice@example.com")
        assert "/output/advisory/test/report.pdf" in lead["pdf_paths"]

    def test_returns_none_without_email(self, advisory_output_dir):
        from execution.advisory.advisory_to_lead_mapper import map_advisory_to_lead

        session = _make_session()
        session["lead"] = None
        assert map_advisory_to_lead(session) is None

    def test_upserts_existing_lead(self, advisory_output_dir):
        from execution.advisory.advisory_to_lead_mapper import map_advisory_to_lead
        from execution.advisory.lead_manager import get_lead_by_email, list_all_leads

        # First session
        map_advisory_to_lead(_make_session())
        # Second session with same email
        session2 = _make_session()
        session2["session_id"] = "session-456"
        session2["lead"]["company"] = "Acme Logistics v2"
        map_advisory_to_lead(session2)

        leads = list_all_leads()
        assert len(leads) == 1
        lead = get_lead_by_email("alice@example.com")
        assert lead["company"] == "Acme Logistics v2"
        assert "session-456" in lead["advisory_session_ids"]


class TestAdvanceCampaignForSession:
    def test_advances_stage(self, advisory_output_dir):
        from execution.advisory.advisory_to_lead_mapper import (
            advance_campaign_for_session,
            map_advisory_to_lead,
        )
        from execution.advisory.campaign_manager import get_enrollments_by_email

        session = _make_session()
        map_advisory_to_lead(session)
        advance_campaign_for_session(session, "Booked Strategy Call")

        enrollments = get_enrollments_by_email("alice@example.com")
        assert enrollments[0]["current_stage"] == "Booked Strategy Call"
