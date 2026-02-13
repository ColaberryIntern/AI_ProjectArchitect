"""Tests for chapter build routes."""

import pytest
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
def chapter_project(client, created_project):
    """Create a project in the chapter_build phase with locked outline."""
    state = load_state(created_project)
    record_idea(state, "Test idea")
    advance_phase(state, "feature_discovery")
    add_feature(state, "core", "f1", "Feature", "Desc", "Rationale for testing", problem_mapped_to="p1", build_order=1)
    approve_features(state)
    advance_phase(state, "outline_generation")
    sections = [
        {"index": i, "title": t, "type": "required", "summary": f"Summary {i}"}
        for i, t in enumerate([
            "System Purpose & Context",
            "Target Users & Roles",
            "Core Capabilities",
            "Non-Goals & Explicit Exclusions",
            "High-Level Architecture",
            "Execution Phases",
            "Risks, Constraints, and Assumptions",
        ], start=1)
    ]
    set_outline_sections(state, sections)
    advance_phase(state, "outline_approval")
    lock_outline(state)
    advance_phase(state, "chapter_build")
    save_state(state, created_project)
    return created_project


class TestChapterBuildPage:
    def test_shows_overview(self, client, chapter_project):
        response = client.get(f"/projects/{chapter_project}/chapter-build")
        assert response.status_code == 200
        assert "Chapter Overview" in response.text

    def test_no_chat_panel(self, client, chapter_project):
        response = client.get(f"/projects/{chapter_project}/chapter-build")
        assert response.status_code == 200
        assert "Project Assistant" not in response.text

    def test_shows_chapter_detail(self, client, chapter_project):
        response = client.get(f"/projects/{chapter_project}/chapter-build/1")
        assert response.status_code == 200
        assert "System Purpose" in response.text


class TestSubmitChapter:
    def test_submit_creates_file(self, client, chapter_project):
        response = client.post(
            f"/projects/{chapter_project}/chapter-build/1/submit",
            data={
                "purpose": "This chapter defines the system purpose and context for the project.",
                "design_intent": "We chose this structure to provide clarity about why the system exists.",
                "implementation_guidance": (
                    "First, review the project idea from the ideation phase. "
                    "Then, define the core problem being solved. "
                    "Next, describe the target environment. "
                    "The input is the approved ideation summary. "
                    "The output is a clear purpose statement. "
                    "This step depends on the ideation being approved. "
                    "The execution order is: review, define, describe. "
                    "Step 1 is review. Step 2 is definition."
                ),
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        state = load_state(chapter_project)
        assert state["chapters"][0]["status"] == "draft"
        assert state["chapters"][0]["content_path"] is not None


class TestApproveChapter:
    def test_approve_chapter(self, client, chapter_project):
        # Submit first
        client.post(
            f"/projects/{chapter_project}/chapter-build/1/submit",
            data={
                "purpose": "Defines purpose.",
                "design_intent": "Clarity.",
                "implementation_guidance": "Step-by-step.",
            },
            follow_redirects=False,
        )
        # Approve
        response = client.post(
            f"/projects/{chapter_project}/chapter-build/1/approve",
            follow_redirects=False,
        )
        assert response.status_code == 303
        state = load_state(chapter_project)
        assert state["chapters"][0]["status"] == "approved"
