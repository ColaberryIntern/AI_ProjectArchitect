"""Tests for advisory session state management."""

import json
from pathlib import Path

import pytest


@pytest.fixture
def advisory_output_dir(monkeypatch, tmp_path):
    """Redirect ADVISORY_OUTPUT_DIR to a temp directory."""
    import config.settings as settings
    import execution.advisory.advisory_state_manager as asm

    advisory_dir = tmp_path / "advisory"
    advisory_dir.mkdir()
    monkeypatch.setattr(settings, "ADVISORY_OUTPUT_DIR", advisory_dir)
    monkeypatch.setattr(asm, "ADVISORY_OUTPUT_DIR", advisory_dir)
    return advisory_dir


class TestInitializeSession:
    def test_creates_session_with_required_fields(self, advisory_output_dir):
        from execution.advisory.advisory_state_manager import initialize_session

        session = initialize_session("Build an AI-powered logistics platform")
        assert session["session_id"]
        assert session["status"] == "idea_input"
        assert session["business_idea"] == "Build an AI-powered logistics platform"
        assert session["current_question_index"] == 0
        assert session["answers"] == []
        assert session["capability_map"] is None
        assert session["org_structure"] is None
        assert session["impact_model"] is None
        assert session["maturity_score"] is None
        assert session["lead"] is None
        assert session["pdf_path"] is None

    def test_persists_session_to_disk(self, advisory_output_dir):
        from execution.advisory.advisory_state_manager import initialize_session

        session = initialize_session("Test idea")
        state_file = advisory_output_dir / session["session_id"] / "advisory_state.json"
        assert state_file.exists()

        with open(state_file) as f:
            loaded = json.load(f)
        assert loaded["session_id"] == session["session_id"]


class TestLoadSession:
    def test_loads_existing_session(self, advisory_output_dir):
        from execution.advisory.advisory_state_manager import initialize_session, load_session

        session = initialize_session("Test idea")
        loaded = load_session(session["session_id"])
        assert loaded["session_id"] == session["session_id"]
        assert loaded["business_idea"] == "Test idea"

    def test_raises_on_missing_session(self, advisory_output_dir):
        from execution.advisory.advisory_state_manager import load_session

        with pytest.raises(FileNotFoundError):
            load_session("nonexistent-session-id")


class TestRecordAnswer:
    def test_appends_answer_and_advances_index(self, advisory_output_dir):
        from execution.advisory.advisory_state_manager import initialize_session, record_answer

        session = initialize_session("Test idea")
        updated = record_answer(session, "q1_business_overview", "What do you do?", "We run a logistics company")
        assert len(updated["answers"]) == 1
        assert updated["answers"][0]["question_id"] == "q1_business_overview"
        assert updated["answers"][0]["answer_text"] == "We run a logistics company"
        assert updated["current_question_index"] == 1

    def test_multiple_answers_advance_correctly(self, advisory_output_dir):
        from execution.advisory.advisory_state_manager import initialize_session, record_answer

        session = initialize_session("Test idea")
        record_answer(session, "q1", "Q1", "A1")
        record_answer(session, "q2", "Q2", "A2")
        record_answer(session, "q3", "Q3", "A3")
        assert len(session["answers"]) == 3
        assert session["current_question_index"] == 3

    def test_persists_answer_to_disk(self, advisory_output_dir):
        from execution.advisory.advisory_state_manager import initialize_session, load_session, record_answer

        session = initialize_session("Test idea")
        record_answer(session, "q1", "Q1", "A1")
        loaded = load_session(session["session_id"])
        assert len(loaded["answers"]) == 1


class TestSetters:
    def test_set_capability_map(self, advisory_output_dir):
        from execution.advisory.advisory_state_manager import initialize_session, set_capability_map

        session = initialize_session("Test")
        cap_map = {"departments": [{"id": "ops", "name": "Operations", "capabilities": []}]}
        updated = set_capability_map(session, cap_map)
        assert updated["capability_map"] == cap_map

    def test_set_org_structure(self, advisory_output_dir):
        from execution.advisory.advisory_state_manager import initialize_session, set_org_structure

        session = initialize_session("Test")
        nodes = [{"id": "root", "title": "AI COO", "type": "executive"}]
        updated = set_org_structure(session, nodes)
        assert updated["org_structure"] == nodes

    def test_set_impact_model(self, advisory_output_dir):
        from execution.advisory.advisory_state_manager import initialize_session, set_impact_model

        session = initialize_session("Test")
        impact = {"cost_savings": {"total_annual": 50000}}
        updated = set_impact_model(session, impact)
        assert updated["impact_model"] == impact

    def test_set_maturity_score(self, advisory_output_dir):
        from execution.advisory.advisory_state_manager import initialize_session, set_maturity_score

        session = initialize_session("Test")
        score = {"overall": 3.5, "dimensions": {"data_readiness": 4}}
        updated = set_maturity_score(session, score)
        assert updated["maturity_score"] == score

    def test_set_pdf_path(self, advisory_output_dir):
        from execution.advisory.advisory_state_manager import initialize_session, set_pdf_path

        session = initialize_session("Test")
        updated = set_pdf_path(session, "/output/advisory/123/report.pdf")
        assert updated["pdf_path"] == "/output/advisory/123/report.pdf"

    def test_set_linked_project(self, advisory_output_dir):
        from execution.advisory.advisory_state_manager import initialize_session, set_linked_project

        session = initialize_session("Test")
        updated = set_linked_project(session, "my-project")
        assert updated["linked_project_slug"] == "my-project"


class TestAdvanceStatus:
    def test_advances_to_valid_status(self, advisory_output_dir):
        from execution.advisory.advisory_state_manager import initialize_session, advance_status

        session = initialize_session("Test")
        updated = advance_status(session, "questioning")
        assert updated["status"] == "questioning"

    def test_rejects_invalid_status(self, advisory_output_dir):
        from execution.advisory.advisory_state_manager import initialize_session, advance_status

        session = initialize_session("Test")
        with pytest.raises(ValueError, match="Invalid status"):
            advance_status(session, "invalid_status")


class TestLeadCapture:
    def test_record_lead_on_session(self, advisory_output_dir):
        from execution.advisory.advisory_state_manager import initialize_session, record_lead

        session = initialize_session("Test")
        updated = record_lead(session, "Alice", "alice@example.com", "Acme Corp", "CEO")
        assert updated["lead"]["name"] == "Alice"
        assert updated["lead"]["email"] == "alice@example.com"
        assert updated["lead"]["company"] == "Acme Corp"
        assert updated["lead"]["role"] == "CEO"
        assert updated["lead"]["captured_at"]

    def test_appends_to_leads_index(self, advisory_output_dir):
        from execution.advisory.advisory_state_manager import initialize_session, load_leads_index, record_lead

        session = initialize_session("Test")
        record_lead(session, "Alice", "alice@example.com", "Acme", "CEO")
        leads = load_leads_index()
        assert len(leads) == 1
        assert leads[0]["email"] == "alice@example.com"
        assert leads[0]["session_id"] == session["session_id"]

    def test_multiple_leads_accumulate_in_index(self, advisory_output_dir):
        from execution.advisory.advisory_state_manager import initialize_session, load_leads_index, record_lead

        s1 = initialize_session("Idea 1")
        s2 = initialize_session("Idea 2")
        record_lead(s1, "Alice", "alice@example.com", "Acme", "CEO")
        record_lead(s2, "Bob", "bob@example.com", "Beta Inc", "CTO")
        leads = load_leads_index()
        assert len(leads) == 2


class TestListSessions:
    def test_lists_session_ids(self, advisory_output_dir):
        from execution.advisory.advisory_state_manager import initialize_session, list_sessions

        s1 = initialize_session("Idea 1")
        s2 = initialize_session("Idea 2")
        ids = list_sessions()
        assert s1["session_id"] in ids
        assert s2["session_id"] in ids

    def test_empty_when_no_sessions(self, advisory_output_dir):
        from execution.advisory.advisory_state_manager import list_sessions

        assert list_sessions() == []


class TestSessionExists:
    def test_true_for_existing(self, advisory_output_dir):
        from execution.advisory.advisory_state_manager import initialize_session, session_exists

        session = initialize_session("Test")
        assert session_exists(session["session_id"]) is True

    def test_false_for_missing(self, advisory_output_dir):
        from execution.advisory.advisory_state_manager import session_exists

        assert session_exists("nonexistent") is False
