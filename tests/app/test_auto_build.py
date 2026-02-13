"""Tests for auto-build routes."""

import pytest
from unittest.mock import patch

from execution.auto_builder import (
    BuildEvent,
    _append_event,
    clear_build_progress,
)
from execution.state_manager import (
    add_feature,
    advance_phase,
    approve_features,
    load_state,
    lock_outline,
    record_idea,
    save_state,
    set_outline_sections,
)


@pytest.fixture
def chapter_build_project(client, created_project):
    """Create a project in the chapter_build phase with locked outline."""
    state = load_state(created_project)
    record_idea(state, "Test auto-build idea")
    advance_phase(state, "feature_discovery")
    add_feature(
        state, "core", "f1", "Feature One", "First feature",
        "Core", problem_mapped_to="p1", build_order=1,
    )
    approve_features(state)
    advance_phase(state, "outline_generation")
    sections = [
        {"index": i, "title": t, "type": "required", "summary": f"Summary for {t}"}
        for i, t in enumerate(
            ["Executive Summary", "Functional Requirements", "Technical Architecture"],
            start=1,
        )
    ]
    set_outline_sections(state, sections)
    advance_phase(state, "outline_approval")
    lock_outline(state)
    advance_phase(state, "chapter_build")
    save_state(state, created_project)
    return created_project


@pytest.fixture
def complete_project(client, created_project):
    """Create a project in the complete phase."""
    state = load_state(created_project)
    record_idea(state, "Complete project idea")
    advance_phase(state, "feature_discovery")
    add_feature(
        state, "core", "f1", "Feature", "Desc",
        "Core", problem_mapped_to="p1", build_order=1,
    )
    approve_features(state)
    advance_phase(state, "outline_generation")
    sections = [
        {"index": 1, "title": "Summary", "type": "required", "summary": "s"},
    ]
    set_outline_sections(state, sections)
    advance_phase(state, "outline_approval")
    lock_outline(state)
    advance_phase(state, "chapter_build")
    advance_phase(state, "quality_gates")
    advance_phase(state, "final_assembly")
    advance_phase(state, "complete")
    save_state(state, created_project)
    return created_project


class TestAutoBuildPage:
    """Tests for GET /auto-build."""

    def test_shows_progress_template(self, client, chapter_build_project):
        response = client.get(f"/projects/{chapter_build_project}/auto-build")
        assert response.status_code == 200
        assert "Building Your Project Guide" in response.text

    def test_shows_chapter_list(self, client, chapter_build_project):
        response = client.get(f"/projects/{chapter_build_project}/auto-build")
        assert response.status_code == 200
        assert "Executive Summary" in response.text
        assert "Technical Architecture" in response.text

    def test_complete_project_redirects(self, client, complete_project):
        response = client.get(
            f"/projects/{complete_project}/auto-build",
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "/complete" in response.headers["location"]

    def test_nonexistent_project_404(self, client):
        response = client.get("/projects/nonexistent-slug/auto-build")
        assert response.status_code == 404


class TestStartAutoBuild:
    """Tests for POST /auto-build/start."""

    @patch("app.routers.auto_build.run_auto_build_sync")
    def test_start_returns_started(self, mock_sync, client, chapter_build_project):
        response = client.post(
            f"/projects/{chapter_build_project}/auto-build/start",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "started"

    def test_wrong_phase_returns_409(self, client, created_project):
        response = client.post(
            f"/projects/{created_project}/auto-build/start",
        )
        assert response.status_code == 409
        data = response.json()
        assert data["status"] == "error"

    @patch("app.routers.auto_build.run_auto_build_sync")
    def test_already_running_returns_409(self, mock_sync, client, chapter_build_project):
        # Simulate a build in progress
        _append_event(
            chapter_build_project,
            BuildEvent("chapter", "Working...", 1, 3, 10),
        )
        try:
            response = client.post(
                f"/projects/{chapter_build_project}/auto-build/start",
            )
            assert response.status_code == 409
            data = response.json()
            assert data["status"] == "already_running"
        finally:
            clear_build_progress(chapter_build_project)


class TestAutoBuildEvents:
    """Tests for GET /auto-build/events (SSE)."""

    def test_sse_returns_event_stream_content_type(self, client, chapter_build_project):
        # Seed a complete event so the stream closes immediately
        _append_event(
            chapter_build_project,
            BuildEvent("complete", "Done!", 0, 3, 100),
        )
        try:
            response = client.get(
                f"/projects/{chapter_build_project}/auto-build/events",
            )
            assert response.status_code == 200
            assert "text/event-stream" in response.headers["content-type"]
        finally:
            clear_build_progress(chapter_build_project)

    def test_sse_streams_events(self, client, chapter_build_project):
        # Seed events
        _append_event(
            chapter_build_project,
            BuildEvent("chapter", "Writing chapter 1", 1, 3, 10),
        )
        _append_event(
            chapter_build_project,
            BuildEvent("complete", "Done!", 0, 3, 100),
        )
        try:
            response = client.get(
                f"/projects/{chapter_build_project}/auto-build/events",
            )
            assert "Writing chapter 1" in response.text
            assert "Done!" in response.text
        finally:
            clear_build_progress(chapter_build_project)


class TestAutoBuildStatus:
    """Tests for GET /api/auto-build/status (polling fallback)."""

    def test_status_returns_json(self, client, chapter_build_project):
        response = client.get(
            f"/projects/{chapter_build_project}/api/auto-build/status",
        )
        assert response.status_code == 200
        data = response.json()
        assert "phase" in data
        assert "building" in data
        assert "event_count" in data
        assert data["phase"] == "chapter_build"

    def test_status_shows_building_when_events_present(self, client, chapter_build_project):
        _append_event(
            chapter_build_project,
            BuildEvent("chapter", "Working...", 1, 3, 10),
        )
        try:
            response = client.get(
                f"/projects/{chapter_build_project}/api/auto-build/status",
            )
            data = response.json()
            assert data["building"] is True
            assert data["event_count"] == 1
            assert data["latest_event"]["message"] == "Working..."
        finally:
            clear_build_progress(chapter_build_project)

    def test_status_no_events(self, client, chapter_build_project):
        clear_build_progress(chapter_build_project)
        response = client.get(
            f"/projects/{chapter_build_project}/api/auto-build/status",
        )
        data = response.json()
        assert data["building"] is False
        assert data["event_count"] == 0
        assert data["latest_event"] is None


class TestAutoBuildEnterpriseUI:
    """Tests for enterprise features in the auto-build page."""

    def test_depth_label_shown(self, client, chapter_build_project):
        """Auto-build page should show depth mode label and target pages."""
        response = client.get(f"/projects/{chapter_build_project}/auto-build")
        assert response.status_code == 200
        assert "Professional" in response.text
        assert "80-120" in response.text

    def test_scoring_event_rendered(self, client, chapter_build_project):
        """SSE scoring events should include data fields."""
        _append_event(
            chapter_build_project,
            BuildEvent("scoring", "Chapter 1: 85/100", 1, 3, 25,
                        data={"score": 85, "word_count": 3000, "status": "complete"}),
        )
        _append_event(
            chapter_build_project,
            BuildEvent("complete", "Done!", 0, 3, 100),
        )
        try:
            response = client.get(
                f"/projects/{chapter_build_project}/auto-build/events",
            )
            assert "85/100" in response.text
            assert "3000" in response.text
        finally:
            clear_build_progress(chapter_build_project)

    def test_validation_event_streamed(self, client, chapter_build_project):
        """SSE validation events should be streamed."""
        _append_event(
            chapter_build_project,
            BuildEvent("validation", "Running post-build validation...", 0, 3, 72),
        )
        _append_event(
            chapter_build_project,
            BuildEvent("complete", "Done!", 0, 3, 100),
        )
        try:
            response = client.get(
                f"/projects/{chapter_build_project}/auto-build/events",
            )
            assert "post-build validation" in response.text
        finally:
            clear_build_progress(chapter_build_project)

    def test_regenerating_event_streamed(self, client, chapter_build_project):
        """SSE regenerating events should be streamed."""
        _append_event(
            chapter_build_project,
            BuildEvent("regenerating", "Regenerating chapter 2 (score: 60/100)...", 2, 3, 75),
        )
        _append_event(
            chapter_build_project,
            BuildEvent("complete", "Done!", 0, 3, 100),
        )
        try:
            response = client.get(
                f"/projects/{chapter_build_project}/auto-build/events",
            )
            assert "Regenerating chapter 2" in response.text
        finally:
            clear_build_progress(chapter_build_project)

    def test_right_pane_shows_score_thresholds(self, client, chapter_build_project):
        """Right pane should show scoring threshold information."""
        response = client.get(f"/projects/{chapter_build_project}/auto-build")
        assert response.status_code == 200
        assert "Score thresholds" in response.text
        assert "Incomplete" in response.text
        assert "Complete" in response.text

    def test_hidden_phases_not_in_nav(self, client, chapter_build_project):
        """Quality Gates and Final Assembly should not appear in phase nav."""
        response = client.get(f"/projects/{chapter_build_project}/auto-build")
        assert response.status_code == 200
        assert "Quality Gates" not in response.text
        assert "Final Assembly" not in response.text

    def test_nav_shows_six_steps(self, client, chapter_build_project):
        """Phase nav should show 6 visible steps."""
        response = client.get(f"/projects/{chapter_build_project}/auto-build")
        assert response.status_code == 200
        # Visible phases: Idea Intake, Feature Discovery, Outline Generation,
        # Outline Approval, Chapter Build, Complete
        assert "Idea Intake" in response.text
        assert "Complete" in response.text


class TestCompletePage:
    """Tests for GET /complete."""

    def test_complete_page_shows_summary(self, client, complete_project):
        """Complete page should show project summary, not chat panel."""
        response = client.get(f"/projects/{complete_project}/complete")
        assert response.status_code == 200
        assert "Project Complete!" in response.text
        assert "Download Build Guide" in response.text
        # Should NOT show the chat panel
        assert "chat-panel" not in response.text

    def test_complete_page_shows_project_name(self, client, complete_project):
        response = client.get(f"/projects/{complete_project}/complete")
        assert response.status_code == 200
        # The project name should appear somewhere on the page
        state = load_state(complete_project)
        assert state["project"]["name"] in response.text

    def test_complete_page_hides_quality_gates_from_nav(self, client, complete_project):
        """Complete page nav should not show Quality Gates or Final Assembly."""
        response = client.get(f"/projects/{complete_project}/complete")
        assert response.status_code == 200
        assert "Quality Gates" not in response.text
        assert "Final Assembly" not in response.text
