"""Tests for campaign management."""

import pytest


@pytest.fixture
def advisory_output_dir(monkeypatch, tmp_path):
    """Redirect advisory output to temp directory."""
    import config.settings as settings
    import execution.advisory.campaign_manager as cm

    advisory_dir = tmp_path / "advisory"
    advisory_dir.mkdir()
    monkeypatch.setattr(settings, "ADVISORY_OUTPUT_DIR", advisory_dir)
    monkeypatch.setattr(cm, "_CAMPAIGNS_DB_PATH", advisory_dir / "_campaigns_db.json")
    return advisory_dir


class TestEnsureAdvisoryCampaign:
    def test_creates_campaign_on_first_call(self, advisory_output_dir):
        from execution.advisory.campaign_manager import ensure_advisory_campaign

        campaign = ensure_advisory_campaign()
        assert campaign["campaign_name"] == "AI Workforce Designer"
        assert campaign["campaign_type"] == "inbound_advisory"
        assert campaign["campaign_id"]

    def test_returns_same_campaign_on_subsequent_calls(self, advisory_output_dir):
        from execution.advisory.campaign_manager import ensure_advisory_campaign

        c1 = ensure_advisory_campaign()
        c2 = ensure_advisory_campaign()
        assert c1["campaign_id"] == c2["campaign_id"]


class TestEnrollLead:
    def test_enrolls_new_lead(self, advisory_output_dir):
        from execution.advisory.campaign_manager import enroll_lead, ensure_advisory_campaign

        campaign = ensure_advisory_campaign()
        enrollment = enroll_lead("alice@example.com", campaign["campaign_id"])
        assert enrollment["email"] == "alice@example.com"
        assert enrollment["current_stage"] == "Started Advisory"
        assert len(enrollment["stage_history"]) == 1

    def test_returns_existing_enrollment(self, advisory_output_dir):
        from execution.advisory.campaign_manager import enroll_lead, ensure_advisory_campaign

        campaign = ensure_advisory_campaign()
        e1 = enroll_lead("alice@example.com", campaign["campaign_id"])
        e2 = enroll_lead("alice@example.com", campaign["campaign_id"])
        assert e1["enrollment_id"] == e2["enrollment_id"]


class TestAdvanceStage:
    def test_advances_to_new_stage(self, advisory_output_dir):
        from execution.advisory.campaign_manager import advance_stage, enroll_lead, ensure_advisory_campaign

        campaign = ensure_advisory_campaign()
        enroll_lead("alice@example.com", campaign["campaign_id"])
        updated = advance_stage("alice@example.com", campaign["campaign_id"], "Captured Lead")
        assert updated["current_stage"] == "Captured Lead"
        assert len(updated["stage_history"]) == 2

    def test_no_duplicate_stage_on_same_advance(self, advisory_output_dir):
        from execution.advisory.campaign_manager import advance_stage, enroll_lead, ensure_advisory_campaign

        campaign = ensure_advisory_campaign()
        enroll_lead("alice@example.com", campaign["campaign_id"])
        advance_stage("alice@example.com", campaign["campaign_id"], "Captured Lead")
        updated = advance_stage("alice@example.com", campaign["campaign_id"], "Captured Lead")
        assert len(updated["stage_history"]) == 2  # Not 3

    def test_returns_none_for_unenrolled(self, advisory_output_dir):
        from execution.advisory.campaign_manager import advance_stage, ensure_advisory_campaign

        campaign = ensure_advisory_campaign()
        assert advance_stage("nobody@example.com", campaign["campaign_id"], "X") is None


class TestCampaignStats:
    def test_returns_stage_counts(self, advisory_output_dir):
        from execution.advisory.campaign_manager import (
            advance_stage,
            enroll_lead,
            ensure_advisory_campaign,
            get_campaign_stats,
        )

        campaign = ensure_advisory_campaign()
        enroll_lead("alice@example.com", campaign["campaign_id"])
        enroll_lead("bob@example.com", campaign["campaign_id"])
        advance_stage("alice@example.com", campaign["campaign_id"], "Captured Lead")

        stats = get_campaign_stats(campaign["campaign_id"])
        assert stats["total_enrolled"] == 2
        assert stats["stage_counts"]["Captured Lead"] == 1
        assert stats["stage_counts"]["Started Advisory"] == 1
