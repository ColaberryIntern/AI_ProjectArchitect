"""Tests for outline generation and approval routes."""

import pytest
from execution.state_manager import (
    add_feature,
    advance_phase,
    approve_features,
    get_build_depth_mode,
    load_state,
    record_idea,
    save_state,
    set_outline_sections,
)


@pytest.fixture
def outline_project(client, created_project):
    """Create a project in the outline_generation phase."""
    state = load_state(created_project)
    record_idea(state, "Test idea")
    advance_phase(state, "feature_discovery")
    add_feature(state, "core", "f1", "Feature", "Desc", "Rationale for testing", problem_mapped_to="p1", build_order=1)
    approve_features(state)
    advance_phase(state, "outline_generation")
    save_state(state, created_project)
    return created_project


SECTIONS_DATA = {
    f"title_{i}": title
    for i, title in enumerate([
        "System Purpose & Context",
        "Target Users & Roles",
        "Core Capabilities",
        "Non-Goals & Explicit Exclusions",
        "High-Level Architecture",
        "Execution Phases",
        "Risks, Constraints, and Assumptions",
    ], start=1)
}
SECTIONS_DATA.update({
    f"summary_{i}": f"Summary for section {i}"
    for i in range(1, 8)
})


class TestOutlineGenerationPage:
    def test_shows_form(self, client, outline_project):
        response = client.get(f"/projects/{outline_project}/outline-generation")
        assert response.status_code == 200
        assert "Requirements Document Outline" in response.text

    def test_no_chat_panel(self, client, outline_project):
        """Outline generation page must NOT show the chat panel."""
        response = client.get(f"/projects/{outline_project}/outline-generation")
        assert response.status_code == 200
        assert 'id="chat-messages"' not in response.text
        assert 'id="chat-form"' not in response.text

    def test_auto_generates_sections_on_first_visit(self, client, outline_project):
        """First visit should auto-generate 7 outline sections."""
        response = client.get(f"/projects/{outline_project}/outline-generation")
        assert response.status_code == 200
        state = load_state(outline_project)
        assert len(state["outline"]["sections"]) == 7


class TestSaveSectionsPhaseMismatch:
    def test_save_sections_wrong_phase_redirects(self, client, outline_project):
        """Saving sections when not in outline_generation should redirect."""
        state = load_state(outline_project)
        advance_phase(state, "outline_approval")
        save_state(state, outline_project)

        response = client.post(
            f"/projects/{outline_project}/outline-generation/sections",
            data=SECTIONS_DATA,
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert f"/projects/{outline_project}" in response.headers["location"]

    def test_advance_wrong_phase_redirects(self, client, outline_project):
        """Advancing when not in outline_generation should redirect."""
        state = load_state(outline_project)
        advance_phase(state, "outline_approval")
        save_state(state, outline_project)

        response = client.post(
            f"/projects/{outline_project}/outline-generation/advance",
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert f"/projects/{outline_project}" in response.headers["location"]


class TestSaveSections:
    def test_saves_sections(self, client, outline_project):
        response = client.post(
            f"/projects/{outline_project}/outline-generation/sections",
            data=SECTIONS_DATA,
            follow_redirects=False,
        )
        assert response.status_code == 303
        state = load_state(outline_project)
        assert len(state["outline"]["sections"]) == 7


class TestValidateOutline:
    def test_returns_json(self, client, outline_project):
        client.post(
            f"/projects/{outline_project}/outline-generation/sections",
            data=SECTIONS_DATA,
            follow_redirects=False,
        )
        response = client.get(f"/projects/{outline_project}/api/outline/validate")
        assert response.status_code == 200
        data = response.json()
        assert "all_passed" in data


class TestAdvanceToApproval:
    def test_advances_phase(self, client, outline_project):
        client.post(
            f"/projects/{outline_project}/outline-generation/sections",
            data=SECTIONS_DATA,
            follow_redirects=False,
        )
        response = client.post(
            f"/projects/{outline_project}/outline-generation/advance",
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "outline-approval" in response.headers["location"]


class TestOutlineApprovalPage:
    def test_shows_preview(self, client, outline_project):
        client.post(
            f"/projects/{outline_project}/outline-generation/sections",
            data=SECTIONS_DATA,
            follow_redirects=False,
        )
        advance_state = load_state(outline_project)
        advance_phase(advance_state, "outline_approval")
        save_state(advance_state, outline_project)

        response = client.get(f"/projects/{outline_project}/outline-approval")
        assert response.status_code == 200
        assert "Outline Approval" in response.text

    def test_no_chat_panel(self, client, outline_project):
        """Outline approval page must NOT show the chat panel."""
        client.post(
            f"/projects/{outline_project}/outline-generation/sections",
            data=SECTIONS_DATA,
            follow_redirects=False,
        )
        advance_state = load_state(outline_project)
        advance_phase(advance_state, "outline_approval")
        save_state(advance_state, outline_project)

        response = client.get(f"/projects/{outline_project}/outline-approval")
        assert response.status_code == 200
        assert 'id="chat-messages"' not in response.text
        assert 'id="chat-form"' not in response.text


class TestLockOutline:
    def test_lock_creates_chapters(self, client, outline_project):
        client.post(
            f"/projects/{outline_project}/outline-generation/sections",
            data=SECTIONS_DATA,
            follow_redirects=False,
        )
        state = load_state(outline_project)
        advance_phase(state, "outline_approval")
        save_state(state, outline_project)

        response = client.post(
            f"/projects/{outline_project}/outline-approval/lock",
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "auto-build" in response.headers["location"]
        state = load_state(outline_project)
        assert len(state["chapters"]) == 7
        assert state["current_phase"] == "chapter_build"


class TestDepthModeSelector:
    """Tests for depth mode selector on outline approval page."""

    @pytest.fixture
    def approval_project(self, client, outline_project):
        """Project in outline_approval phase with sections saved."""
        client.post(
            f"/projects/{outline_project}/outline-generation/sections",
            data=SECTIONS_DATA,
            follow_redirects=False,
        )
        state = load_state(outline_project)
        advance_phase(state, "outline_approval")
        save_state(state, outline_project)
        return outline_project

    def test_depth_selector_visible(self, client, approval_project):
        response = client.get(f"/projects/{approval_project}/outline-approval")
        assert response.status_code == 200
        assert "Build Depth" in response.text
        assert "Enterprise" in response.text

    def test_set_depth_mode(self, client, approval_project):
        response = client.post(
            f"/projects/{approval_project}/outline-approval/depth",
            data={"depth_mode": "architect"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        state = load_state(approval_project)
        assert get_build_depth_mode(state) == "enterprise"

    def test_set_invalid_depth_mode_rejected(self, client, approval_project):
        response = client.post(
            f"/projects/{approval_project}/outline-approval/depth",
            data={"depth_mode": "extreme"},
            follow_redirects=False,
        )
        # Should redirect back; state should remain at default
        assert response.status_code == 303
        state = load_state(approval_project)
        assert get_build_depth_mode(state) == "professional"
