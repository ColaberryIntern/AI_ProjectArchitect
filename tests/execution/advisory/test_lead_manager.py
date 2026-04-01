"""Tests for lead management."""

import pytest


@pytest.fixture
def advisory_output_dir(monkeypatch, tmp_path):
    """Redirect advisory output to temp directory."""
    import config.settings as settings
    import execution.advisory.lead_manager as lm

    advisory_dir = tmp_path / "advisory"
    advisory_dir.mkdir()
    monkeypatch.setattr(settings, "ADVISORY_OUTPUT_DIR", advisory_dir)
    monkeypatch.setattr(lm, "_LEADS_DB_PATH", advisory_dir / "_leads_db.json")
    return advisory_dir


class TestCreateLead:
    def test_creates_with_required_fields(self, advisory_output_dir):
        from execution.advisory.lead_manager import create_lead

        lead = create_lead("alice@example.com", name="Alice", company="Acme")
        assert lead["email"] == "alice@example.com"
        assert lead["name"] == "Alice"
        assert lead["lead_id"]
        assert lead["advisory_session_ids"] == []
        assert lead["campaign_enrollments"] == []

    def test_persists_to_disk(self, advisory_output_dir):
        from execution.advisory.lead_manager import create_lead, list_all_leads

        create_lead("alice@example.com")
        leads = list_all_leads()
        assert len(leads) == 1


class TestUpsertLead:
    def test_creates_new_when_not_exists(self, advisory_output_dir):
        from execution.advisory.lead_manager import list_all_leads, upsert_lead

        upsert_lead("alice@example.com", name="Alice")
        assert len(list_all_leads()) == 1

    def test_updates_existing_by_email(self, advisory_output_dir):
        from execution.advisory.lead_manager import get_lead_by_email, list_all_leads, upsert_lead

        upsert_lead("alice@example.com", name="Alice")
        upsert_lead("alice@example.com", name="Alice Smith", company="Acme")
        leads = list_all_leads()
        assert len(leads) == 1
        lead = get_lead_by_email("alice@example.com")
        assert lead["name"] == "Alice Smith"
        assert lead["company"] == "Acme"

    def test_case_insensitive_email(self, advisory_output_dir):
        from execution.advisory.lead_manager import list_all_leads, upsert_lead

        upsert_lead("Alice@Example.com", name="Alice")
        upsert_lead("alice@example.com", name="Alice Updated")
        assert len(list_all_leads()) == 1


class TestLeadOperations:
    def test_link_advisory_session(self, advisory_output_dir):
        from execution.advisory.lead_manager import create_lead, get_lead_by_email, link_advisory_session

        create_lead("alice@example.com")
        link_advisory_session("alice@example.com", "session-123")
        lead = get_lead_by_email("alice@example.com")
        assert "session-123" in lead["advisory_session_ids"]

    def test_link_session_idempotent(self, advisory_output_dir):
        from execution.advisory.lead_manager import create_lead, get_lead_by_email, link_advisory_session

        create_lead("alice@example.com")
        link_advisory_session("alice@example.com", "session-123")
        link_advisory_session("alice@example.com", "session-123")
        lead = get_lead_by_email("alice@example.com")
        assert lead["advisory_session_ids"].count("session-123") == 1

    def test_add_metadata(self, advisory_output_dir):
        from execution.advisory.lead_manager import add_lead_metadata, create_lead, get_lead_by_email

        create_lead("alice@example.com")
        add_lead_metadata("alice@example.com", {"maturity_score": 3.5, "roi": "150%"})
        lead = get_lead_by_email("alice@example.com")
        assert lead["metadata"]["maturity_score"] == 3.5

    def test_attach_pdf(self, advisory_output_dir):
        from execution.advisory.lead_manager import attach_pdf, create_lead, get_lead_by_email

        create_lead("alice@example.com")
        attach_pdf("alice@example.com", "/path/to/report.pdf")
        lead = get_lead_by_email("alice@example.com")
        assert "/path/to/report.pdf" in lead["pdf_paths"]

    def test_get_lead_by_id(self, advisory_output_dir):
        from execution.advisory.lead_manager import create_lead, get_lead_by_id

        lead = create_lead("alice@example.com")
        found = get_lead_by_id(lead["lead_id"])
        assert found["email"] == "alice@example.com"

    def test_get_nonexistent_lead(self, advisory_output_dir):
        from execution.advisory.lead_manager import get_lead_by_email

        assert get_lead_by_email("nobody@example.com") is None
