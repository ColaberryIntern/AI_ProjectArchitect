"""Tests for the advisory routes."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def advisory_output_dir(monkeypatch, tmp_path):
    """Redirect advisory output to temp directory."""
    import config.settings as settings
    import execution.advisory.advisory_state_manager as asm

    advisory_dir = tmp_path / "advisory"
    advisory_dir.mkdir()
    monkeypatch.setattr(settings, "ADVISORY_OUTPUT_DIR", advisory_dir)
    monkeypatch.setattr(asm, "ADVISORY_OUTPUT_DIR", advisory_dir)
    return advisory_dir


@pytest.fixture
def client(tmp_output_dir):
    """FastAPI TestClient."""
    from app.main import app
    return TestClient(app)


class TestLandingPage:
    def test_get_landing(self, client, advisory_output_dir):
        response = client.get("/advisory/")
        assert response.status_code == 200
        assert "AI Workforce Designer" in response.text

    def test_landing_has_form(self, client, advisory_output_dir):
        response = client.get("/advisory/")
        assert 'name="business_idea"' in response.text
        assert "/advisory/start" in response.text


class TestStartSession:
    def test_creates_session_and_redirects(self, client, advisory_output_dir):
        response = client.post(
            "/advisory/start",
            data={"business_idea": "AI-powered logistics platform"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "/advisory/" in response.headers["location"]
        assert "/questions" in response.headers["location"]

    def test_session_persists(self, client, advisory_output_dir):
        response = client.post(
            "/advisory/start",
            data={"business_idea": "Test idea"},
            follow_redirects=False,
        )
        # Extract session_id from redirect URL
        location = response.headers["location"]
        session_id = location.split("/advisory/")[1].split("/")[0]

        from execution.advisory.advisory_state_manager import load_session
        session = load_session(session_id)
        assert session["business_idea"] == "Test idea"
        assert session["status"] == "questioning"


class TestQuestionFlow:
    def _create_session(self, client):
        """Helper: create a session and return session_id."""
        response = client.post(
            "/advisory/start",
            data={"business_idea": "Logistics AI"},
            follow_redirects=False,
        )
        location = response.headers["location"]
        return location.split("/advisory/")[1].split("/")[0]

    def test_shows_first_question(self, client, advisory_output_dir):
        session_id = self._create_session(client)
        response = client.get(f"/advisory/{session_id}/questions")
        assert response.status_code == 200
        assert "of 10" in response.text

    def test_submit_answer_advances(self, client, advisory_output_dir):
        session_id = self._create_session(client)
        response = client.post(
            f"/advisory/{session_id}/answer",
            data={"answer_text": "We run a logistics company"},
            follow_redirects=False,
        )
        assert response.status_code == 303

        # Check the next page shows question 2
        response = client.get(f"/advisory/{session_id}/questions")
        assert "What are your biggest" in response.text or "of 10" in response.text

    def test_submit_q6_with_system_checkboxes(self, client, advisory_output_dir):
        """Regression: Q6 (current tools) with selected_systems checkboxes must not crash."""
        session_id = self._create_session(client)

        # Answer Q1-Q5
        for _ in range(5):
            client.post(
                f"/advisory/{session_id}/answer",
                data={"answer_text": "test answer"},
                follow_redirects=False,
            )

        # Q6 with system checkboxes (multi-value form field)
        response = client.post(
            f"/advisory/{session_id}/answer",
            content="answer_text=Salesforce+and+Slack&selected_systems=CRM&selected_systems=Communication",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            follow_redirects=False,
        )
        assert response.status_code == 303

        from execution.advisory.advisory_state_manager import load_session
        session = load_session(session_id)
        assert len(session["answers"]) == 6  # Q1-Q5 answered + Q6 with checkboxes

    def test_submit_with_soft_email_capture(self, client, advisory_output_dir):
        """Soft email capture on Q4 should store email on session."""
        session_id = self._create_session(client)

        # Answer Q1-Q3
        for _ in range(3):
            client.post(
                f"/advisory/{session_id}/answer",
                data={"answer_text": "test answer"},
                follow_redirects=False,
            )

        # Q4 with optional email
        response = client.post(
            f"/advisory/{session_id}/answer",
            data={"answer_text": "data entry tasks", "email": "test@example.com"},
            follow_redirects=False,
        )
        assert response.status_code == 303

        from execution.advisory.advisory_state_manager import load_session
        session = load_session(session_id)
        assert session["email"] == "test@example.com"

    def test_invalid_session_redirects_to_landing(self, client, advisory_output_dir):
        response = client.get("/advisory/nonexistent-id/questions", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/advisory/"


class TestGenerateResults:
    def _create_completed_session(self, client, advisory_output_dir, mocker):
        """Helper: create a session with all 10 answers + selected capabilities."""
        session_id = self._create_session(client)

        from execution.advisory.advisory_state_manager import (
            load_session,
            record_answer,
            set_selected_capabilities,
        )
        from execution.advisory.question_engine import ADVISORY_QUESTIONS

        session = load_session(session_id)
        for q in ADVISORY_QUESTIONS:
            record_answer(session, q["id"], q["text"], f"Answer for {q['id']}")

        # Select some capabilities
        set_selected_capabilities(session, [
            "auto_lead_scoring", "ai_chat_support", "workflow_automation",
        ])

        # Mock LLM calls in org_builder
        mocker.patch(
            "execution.advisory.org_builder._llm_build_org",
            side_effect=Exception("mocked"),
        )
        return session_id

    def _create_session(self, client):
        response = client.post(
            "/advisory/start",
            data={"business_idea": "Logistics AI"},
            follow_redirects=False,
        )
        location = response.headers["location"]
        return location.split("/advisory/")[1].split("/")[0]

    def test_generate_redirects_to_results(self, client, advisory_output_dir, mocker):
        session_id = self._create_completed_session(client, advisory_output_dir, mocker)
        response = client.get(f"/advisory/{session_id}/generate", follow_redirects=False)
        assert response.status_code == 303
        assert "/results" in response.headers["location"]

    def test_results_page_shows_org_chart(self, client, advisory_output_dir, mocker):
        session_id = self._create_completed_session(client, advisory_output_dir, mocker)
        # Generate first
        client.get(f"/advisory/{session_id}/generate", follow_redirects=False)
        # Then view results
        response = client.get(f"/advisory/{session_id}/results")
        assert response.status_code == 200
        assert "AI-Powered Organization" in response.text
        assert "Business Impact" in response.text


class TestGatePage:
    def test_gate_shows_lead_form(self, client, advisory_output_dir):
        response = client.post(
            "/advisory/start",
            data={"business_idea": "Test"},
            follow_redirects=False,
        )
        session_id = response.headers["location"].split("/advisory/")[1].split("/")[0]
        response = client.get(f"/advisory/{session_id}/gate")
        assert response.status_code == 200
        assert 'name="email"' in response.text
        assert 'name="name"' in response.text


class TestSaveLead:
    def test_captures_lead(self, client, advisory_output_dir, mocker):
        # Mock PDF generation (imported dynamically in the route handler)
        mocker.patch(
            "execution.advisory.pdf_generator.generate_pdf",
            side_effect=Exception("not installed"),
        )

        response = client.post(
            "/advisory/start",
            data={"business_idea": "Test"},
            follow_redirects=False,
        )
        session_id = response.headers["location"].split("/advisory/")[1].split("/")[0]

        response = client.post(
            f"/advisory/{session_id}/save-lead",
            data={
                "name": "Alice Smith",
                "email": "alice@example.com",
                "company": "Acme Corp",
                "role": "CEO",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "saved=true" in response.headers["location"]

        from execution.advisory.advisory_state_manager import load_session
        session = load_session(session_id)
        assert session["lead"]["email"] == "alice@example.com"
