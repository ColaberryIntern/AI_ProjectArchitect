"""Tests for quality gates routes."""

import pytest
from execution.state_manager import (
    add_feature,
    advance_phase,
    approve_features,
    load_state,
    lock_outline,
    record_chapter_status,
    record_idea,
    save_state,
    set_outline_sections,
)
from execution.template_renderer import render_chapter
from config.settings import OUTPUT_DIR


@pytest.fixture
def quality_project(client, created_project, tmp_output_dir):
    """Create a project in the quality_gates phase with all chapters approved."""
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

    # Create chapter files and approve all
    chapter_dir = tmp_output_dir / created_project / "chapters"
    chapter_dir.mkdir(parents=True, exist_ok=True)
    for i, sec in enumerate(sections, start=1):
        content = render_chapter(
            index=i, title=sec["title"],
            purpose=f"This chapter defines {sec['summary'].lower()}",
            design_intent="This approach was chosen to ensure clarity and reduce ambiguity.",
            implementation_guidance=(
                "First, review the previous context. "
                "Then, implement the described logic. "
                "Next, validate against acceptance criteria. "
                "The input is the approved ideation data. "
                "The output is a structured section. "
                "This depends on the outline being locked. "
                "The execution order is: review, implement, validate. "
                "Step 1 is review. Step 2 is implementation."
            ),
        )
        ch_path = chapter_dir / f"ch{i}.md"
        ch_path.write_text(content, encoding="utf-8")
        record_chapter_status(state, i, "draft", str(ch_path))
        record_chapter_status(state, i, "approved")

    advance_phase(state, "quality_gates")
    save_state(state, created_project)
    return created_project


class TestQualityGatesPage:
    def test_shows_page(self, client, quality_project):
        response = client.get(f"/projects/{quality_project}/quality-gates")
        assert response.status_code == 200
        assert "Quality Gates" in response.text
        assert "chat-panel" not in response.text


class TestRunQualityGates:
    def test_run_gates(self, client, quality_project):
        response = client.post(
            f"/projects/{quality_project}/quality-gates/run",
            follow_redirects=False,
        )
        assert response.status_code == 303
        state = load_state(quality_project)
        assert state["quality"]["final_report"]["ran_at"] is not None
