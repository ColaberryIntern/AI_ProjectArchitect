"""Tests for final assembly and complete routes."""

from pathlib import Path

import pytest
from execution.state_manager import (
    add_feature,
    advance_phase,
    approve_features,
    load_state,
    lock_outline,
    record_chapter_status,
    record_final_quality,
    record_idea,
    save_state,
    set_outline_sections,
)
from execution.template_renderer import render_chapter


@pytest.fixture
def assembly_project(client, created_project, tmp_output_dir):
    """Create a project in the final_assembly phase."""
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
    record_final_quality(state, {"all_passed": True, "details": []})
    advance_phase(state, "final_assembly")
    save_state(state, created_project)
    return created_project


class TestFinalAssemblyPage:
    def test_shows_checklist(self, client, assembly_project):
        response = client.get(f"/projects/{assembly_project}/final-assembly")
        assert response.status_code == 200
        assert "Final Assembly" in response.text
        assert "Pre-Assembly Checklist" in response.text

    def test_shows_assemble_button_when_checks_fail(self, client, assembly_project):
        """Button should be visible with a warning even when checks fail."""
        state = load_state(assembly_project)
        state["quality"]["final_report"]["all_passed"] = False
        save_state(state, assembly_project)

        response = client.get(f"/projects/{assembly_project}/final-assembly")
        assert response.status_code == 200
        assert "Assemble Document" in response.text
        assert "btn-warning" in response.text
        assert "Not all pre-assembly checks pass" in response.text

    def test_shows_download_and_reassemble_after_assembly(self, client, assembly_project):
        """After assembly, page should show both download link and re-assemble button."""
        client.post(
            f"/projects/{assembly_project}/final-assembly/assemble",
            follow_redirects=False,
        )
        response = client.get(f"/projects/{assembly_project}/final-assembly")
        assert response.status_code == 200
        assert "Download Build Guide" in response.text
        assert "Re-Assemble Document" in response.text

    def test_shows_file_missing_message(self, client, assembly_project):
        """When assembled file is deleted, show file-missing message and re-assemble."""
        client.post(
            f"/projects/{assembly_project}/final-assembly/assemble",
            follow_redirects=False,
        )
        state = load_state(assembly_project)
        Path(state["document"]["output_path"]).unlink()

        response = client.get(f"/projects/{assembly_project}/final-assembly")
        assert response.status_code == 200
        assert "File missing from disk" in response.text
        assert "Re-Assemble Document" in response.text
        assert "Download Build Guide" not in response.text


class TestAssembleDocument:
    def test_assembles_and_completes(self, client, assembly_project):
        response = client.post(
            f"/projects/{assembly_project}/final-assembly/assemble",
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "complete" in response.headers["location"]
        state = load_state(assembly_project)
        assert state["current_phase"] == "complete"
        assert state["document"]["filename"] is not None

    def test_force_assemble_with_failed_checks(self, client, assembly_project):
        """Assembly should succeed even when pre-assembly checks fail."""
        state = load_state(assembly_project)
        state["quality"]["final_report"]["all_passed"] = False
        save_state(state, assembly_project)

        response = client.post(
            f"/projects/{assembly_project}/final-assembly/assemble",
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "complete" in response.headers["location"]

    def test_reassemble_from_complete_phase(self, client, assembly_project):
        """Should allow re-assembly when already in complete phase."""
        client.post(
            f"/projects/{assembly_project}/final-assembly/assemble",
            follow_redirects=False,
        )
        state = load_state(assembly_project)
        assert state["current_phase"] == "complete"
        first_assembled_at = state["document"]["assembled_at"]

        response = client.post(
            f"/projects/{assembly_project}/final-assembly/assemble",
            follow_redirects=False,
        )
        assert response.status_code == 303
        state = load_state(assembly_project)
        assert state["current_phase"] == "complete"
        assert state["document"]["assembled_at"] != first_assembled_at

    def test_reassemble_after_file_deletion(self, client, assembly_project):
        """Should re-assemble successfully after output file is deleted."""
        client.post(
            f"/projects/{assembly_project}/final-assembly/assemble",
            follow_redirects=False,
        )
        state = load_state(assembly_project)
        output_path = state["document"]["output_path"]
        Path(output_path).unlink()
        assert not Path(output_path).exists()

        response = client.post(
            f"/projects/{assembly_project}/final-assembly/assemble",
            follow_redirects=False,
        )
        assert response.status_code == 303
        state = load_state(assembly_project)
        assert Path(state["document"]["output_path"]).exists()

    def test_assemble_rejects_wrong_phase(self, client, created_project):
        """Assembly from a non-allowed phase should return 409."""
        response = client.post(
            f"/projects/{created_project}/final-assembly/assemble",
            follow_redirects=False,
        )
        assert response.status_code == 409


class TestCompletePage:
    def test_shows_completion(self, client, assembly_project):
        # First assemble
        client.post(
            f"/projects/{assembly_project}/final-assembly/assemble",
            follow_redirects=False,
        )
        response = client.get(f"/projects/{assembly_project}/complete")
        assert response.status_code == 200
        assert "Project Complete!" in response.text
        assert "Download Build Guide" in response.text

    def test_shows_page_estimates(self, client, assembly_project):
        """Complete page should show estimated and target pages."""
        client.post(
            f"/projects/{assembly_project}/final-assembly/assemble",
            follow_redirects=False,
        )
        response = client.get(f"/projects/{assembly_project}/complete")
        assert response.status_code == 200
        assert "Est. Pages" in response.text
        assert "Target" in response.text


class TestDownload:
    def test_download_after_assembly(self, client, assembly_project):
        client.post(
            f"/projects/{assembly_project}/final-assembly/assemble",
            follow_redirects=False,
        )
        response = client.get(
            f"/projects/{assembly_project}/final-assembly/download",
            follow_redirects=False,
        )
        assert response.status_code == 200
        assert "text/markdown" in response.headers.get("content-type", "")
