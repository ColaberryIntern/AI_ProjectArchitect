"""Tests for idea intake routes."""

import pytest
from unittest.mock import patch

from execution.state_manager import (
    PROFILE_REQUIRED_FIELDS,
    get_project_profile,
    load_state,
    save_state,
    set_profile_derived,
    set_profile_field,
)


class TestIdeaIntakePage:
    def test_shows_form_not_chat(self, client, created_project):
        response = client.get(f"/projects/{created_project}/idea-intake")
        assert response.status_code == 200
        # Form elements present
        assert 'name="raw_idea"' in response.text
        assert "Capture Idea" in response.text
        # Chat panel NOT present
        assert 'id="chat-messages"' not in response.text
        assert 'id="chat-form"' not in response.text
        # No feature checkboxes
        assert 'type="checkbox"' not in response.text
        assert "Confirm selections" not in response.text

    def test_nonexistent_project_404(self, client):
        response = client.get("/projects/nonexistent/idea-intake")
        assert response.status_code == 404

    def test_redirects_to_profile_if_generated(self, client, created_project):
        """If profile already generated, GET /idea-intake redirects to profile review."""
        state = load_state(created_project)
        get_project_profile(state)
        set_profile_derived(state, ["c1"], ["nfr1"], ["m1"], ["r1"], ["uc1"])
        save_state(state, created_project)

        response = client.get(
            f"/projects/{created_project}/idea-intake",
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "idea-intake/profile" in response.headers["location"]


class TestSubmitIdeaPhaseMismatch:
    def test_submit_idea_when_past_phase_redirects(self, client, created_project):
        """Submitting idea when project is past idea_intake should redirect, not 409."""
        from execution.state_manager import record_idea, advance_phase

        state = load_state(created_project)
        record_idea(state, "Already captured")
        advance_phase(state, "feature_discovery")
        save_state(state, created_project)

        response = client.post(
            f"/projects/{created_project}/idea-intake",
            data={"raw_idea": "duplicate submission"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert f"/projects/{created_project}" in response.headers["location"]


class TestSubmitIdea:
    def test_captures_idea_and_redirects_to_profile(self, client, created_project):
        """Submitting idea now redirects to profile review, not feature discovery."""
        response = client.post(
            f"/projects/{created_project}/idea-intake",
            data={"raw_idea": "Build an AI-powered task manager"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "idea-intake/profile" in response.headers["location"]
        state = load_state(created_project)
        assert state["idea"]["original_raw"] == "Build an AI-powered task manager"
        # Phase stays idea_intake until profile confirmed
        assert state["current_phase"] == "idea_intake"
        # Profile should be generated
        assert state["project_profile"]["generated_at"] is not None

    def test_profile_fields_populated(self, client, created_project):
        """After idea submission, all 7 profile fields should have options."""
        client.post(
            f"/projects/{created_project}/idea-intake",
            data={"raw_idea": "Build an AI learning platform"},
            follow_redirects=False,
        )
        state = load_state(created_project)
        profile = state["project_profile"]
        for field in PROFILE_REQUIRED_FIELDS:
            assert len(profile[field]["options"]) > 0, f"{field} has no options"


class TestProfileReviewPage:
    def test_shows_profile_fields(self, client, created_project):
        """Profile review page shows all 7 fields with radio buttons."""
        # First submit idea to generate profile
        client.post(
            f"/projects/{created_project}/idea-intake",
            data={"raw_idea": "Build an AI task manager"},
            follow_redirects=False,
        )
        response = client.get(f"/projects/{created_project}/idea-intake/profile")
        assert response.status_code == 200
        assert "Review Project Profile" in response.text
        assert 'type="radio"' in response.text
        assert "Confirm Profile" in response.text

    def test_redirects_without_profile(self, client, created_project):
        """Without generated profile, redirect back to idea intake."""
        response = client.get(
            f"/projects/{created_project}/idea-intake/profile",
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "idea-intake" in response.headers["location"]

    def test_redirects_if_past_phase(self, client, created_project):
        """If past idea_intake phase, redirect to project root."""
        from execution.state_manager import record_idea, advance_phase

        state = load_state(created_project)
        record_idea(state, "test")
        advance_phase(state, "feature_discovery")
        save_state(state, created_project)

        response = client.get(
            f"/projects/{created_project}/idea-intake/profile",
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert f"/projects/{created_project}" == response.headers["location"]


class TestConfirmProfile:
    def _submit_idea_and_get_state(self, client, slug):
        """Helper: submit idea and return state."""
        client.post(
            f"/projects/{slug}/idea-intake",
            data={"raw_idea": "Build an AI task manager for teams"},
            follow_redirects=False,
        )
        return load_state(slug)

    def test_confirm_all_fields_advances(self, client, created_project):
        """Confirming all 7 fields advances to feature_discovery."""
        state = self._submit_idea_and_get_state(client, created_project)
        profile = state["project_profile"]

        # Build form data with first option from each field
        form_data = {}
        for field in PROFILE_REQUIRED_FIELDS:
            options = profile[field]["options"]
            form_data[field] = options[0]["value"] if options else "fallback"

        response = client.post(
            f"/projects/{created_project}/idea-intake/profile",
            data=form_data,
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "feature-discovery" in response.headers["location"]

        state = load_state(created_project)
        assert state["current_phase"] == "feature_discovery"
        assert state["project_profile"]["confirmed_at"] is not None

    def test_missing_field_redirects_with_error(self, client, created_project):
        """Missing a field redirects back with error."""
        self._submit_idea_and_get_state(client, created_project)

        # Submit with only some fields
        form_data = {"problem_definition": "test_value", "target_user": "test_value"}

        response = client.post(
            f"/projects/{created_project}/idea-intake/profile",
            data=form_data,
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "error=all_required" in response.headers["location"]

    def test_past_phase_redirects(self, client, created_project):
        """Confirming profile when past idea_intake redirects."""
        from execution.state_manager import record_idea, advance_phase

        state = load_state(created_project)
        record_idea(state, "test")
        advance_phase(state, "feature_discovery")
        save_state(state, created_project)

        response = client.post(
            f"/projects/{created_project}/idea-intake/profile",
            data={f: "val" for f in PROFILE_REQUIRED_FIELDS},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert f"/projects/{created_project}" == response.headers["location"]
