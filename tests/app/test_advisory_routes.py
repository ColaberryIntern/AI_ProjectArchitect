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

    def test_question_flow_renders_tailored_examples(self, client, advisory_output_dir):
        """Idea-tailored question text + example chips overlay the static ones."""
        import execution.advisory.advisory_state_manager as asm

        session_id = self._create_session(client)
        session = asm.load_session(session_id)
        session["tailored_questions"] = {
            "q1_business_overview": {
                "text": "Tell us about your salon and how booking works today",
                "examples": [
                    "We run a 3-chair hair salon",
                    "We take walk-ins and online bookings",
                    "Nail & lash studio, mostly regulars",
                ],
            }
        }
        asm.save_session(session)

        response = client.get(f"/advisory/{session_id}/questions")
        assert response.status_code == 200
        assert "Tell us about your salon and how booking works today" in response.text
        assert "We run a 3-chair hair salon" in response.text
        # The generic static examples must no longer appear for Q1.
        assert "Healthcare staffing agency" not in response.text

    def test_start_sets_tailoring_status(self, client, advisory_output_dir):
        import execution.advisory.advisory_state_manager as asm
        response = client.post(
            "/advisory/start",
            data={"business_idea": "A booking app for my salon"},
            follow_redirects=False,
        )
        sid = response.headers["location"].split("/advisory/")[1].split("/")[0]
        session = asm.load_session(sid)
        # Generation runs in the background; status is set either way.
        assert session.get("tailoring_status") in ("pending", "done")

    def test_tailoring_json_reports_pending_then_ready(self, client, advisory_output_dir):
        import execution.advisory.advisory_state_manager as asm

        session = asm.initialize_session("A booking app for my salon")
        session["tailoring_status"] = "pending"
        asm.save_session(session)
        sid = session["session_id"]

        r1 = client.get(f"/advisory/{sid}/tailoring.json")
        assert r1.status_code == 200
        assert r1.json() == {"ready": False, "tailored": {}}
        assert r1.headers["cache-control"] == "no-store"

        session["tailoring_status"] = "done"
        session["tailored_questions"] = {
            "q1_business_overview": {"text": "Tell us about your salon", "examples": ["a"]}
        }
        asm.save_session(session)

        body = client.get(f"/advisory/{sid}/tailoring.json").json()
        assert body["ready"] is True
        assert body["tailored"]["q1_business_overview"]["text"] == "Tell us about your salon"

    def test_tailoring_json_missing_session_is_ready_empty(self, client, advisory_output_dir):
        r = client.get("/advisory/does-not-exist/tailoring.json")
        assert r.status_code == 200
        assert r.json() == {"ready": True, "tailored": {}}

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


class TestSystemDiscovery:
    """The My-Day build flow walks the AI System Discovery one question at a time."""

    def _myday_session(self, idea="A booking and payments app for my hair salon", i=0, answers=None):
        import execution.advisory.advisory_state_manager as asm
        from execution.advisory.system_discovery import PHASES, _fallback_question
        session = asm.initialize_session(idea)
        session["myday_build"] = True
        session["discovery"] = {
            "i": i,
            "answers": answers or [],
            "current": _fallback_question(PHASES[i]),
        }
        asm.save_session(session)
        return session

    def test_start_myday_generates_first_question(self, client, advisory_output_dir):
        import execution.advisory.advisory_state_manager as asm
        r = client.post(
            "/advisory/start",
            data={"business_idea": "A booking and payments app for my hair salon", "myday_build": "1"},
            follow_redirects=False,
        )
        sid = r.headers["location"].split("/advisory/")[1].split("/")[0]
        session = asm.load_session(sid)
        assert session.get("myday_build") is True
        disc = session.get("discovery") or {}
        assert disc.get("i") == 0
        assert disc["current"]["phase"] == "control"
        assert len(disc["current"]["options"]) == 3

    def test_questions_route_renders_one_question(self, client, advisory_output_dir):
        session = self._myday_session()
        r = client.get(f"/advisory/{session['session_id']}/questions")
        assert r.status_code == 200
        assert "Question 1 of 9" in r.text
        assert "Control &amp; autonomy" in r.text or "Control & autonomy" in r.text
        assert "discNext" in r.text                # the Next button
        assert "Skip this one" in r.text

    def test_questions_route_redirects_when_done(self, client, advisory_output_dir):
        import execution.advisory.advisory_state_manager as asm
        session = self._myday_session()
        session["discovery_answers"] = {"control": "B"}
        asm.save_session(session)
        r = client.get(f"/advisory/{session['session_id']}/questions", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"].endswith("/build-setup")

    def test_answer_records_and_advances_to_next_question(self, client, advisory_output_dir):
        import execution.advisory.advisory_state_manager as asm
        session = self._myday_session(i=0)
        sid = session["session_id"]
        r = client.post(f"/advisory/{sid}/discovery-answer", data={"choice": "B"}, follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"].endswith("/questions")

        disc = asm.load_session(sid)["discovery"]
        assert disc["i"] == 1
        assert len(disc["answers"]) == 1
        assert disc["answers"][0]["phase"] == "control"
        assert disc["answers"][0]["choice"]["letter"] == "B"
        assert disc["current"]["phase"] == "intelligence"   # next dimension

    def test_answer_accepts_custom_text(self, client, advisory_output_dir):
        import execution.advisory.advisory_state_manager as asm
        session = self._myday_session(i=0)
        sid = session["session_id"]
        client.post(
            f"/advisory/{sid}/discovery-answer",
            data={"custom_answer": "Text clients the night before to confirm or cancel"},
            follow_redirects=False,
        )
        disc = asm.load_session(sid)["discovery"]
        assert disc["i"] == 1
        assert len(disc["answers"]) == 1
        choice = disc["answers"][0]["choice"]
        assert choice["letter"] == "custom"
        assert "night before" in choice["description"]

    def test_skip_advances_without_recording(self, client, advisory_output_dir):
        import execution.advisory.advisory_state_manager as asm
        session = self._myday_session(i=0)
        sid = session["session_id"]
        client.post(f"/advisory/{sid}/discovery-answer", data={"skip": "1"}, follow_redirects=False)
        disc = asm.load_session(sid)["discovery"]
        assert disc["i"] == 1
        assert disc["answers"] == []

    def test_last_answer_stores_refined_idea_and_advances_to_build(self, client, advisory_output_dir):
        import execution.advisory.advisory_state_manager as asm
        # Start on the final dimension with eight already answered.
        prior = [{"phase": "control", "label": "Control & autonomy",
                  "question": "?", "choice": {"letter": "B", "label": "Act with guardrails", "description": "d"}}]
        session = self._myday_session(i=8, answers=prior)
        sid = session["session_id"]

        r = client.post(f"/advisory/{sid}/discovery-answer", data={"choice": "C"}, follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"].endswith("/build-setup")

        reloaded = asm.load_session(sid)
        assert "control" in reloaded["discovery_answers"]
        assert "differentiators" in reloaded["discovery_answers"]
        assert reloaded["refined_idea"].startswith("Original idea:")
        assert "Control & autonomy" in reloaded["refined_idea"]
