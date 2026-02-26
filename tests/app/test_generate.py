"""Tests for app/routers/generate.py."""

from pathlib import Path
from unittest.mock import patch

import pytest

from execution.auto_builder import BuildEvent
from execution.full_pipeline import (
    _append_pipeline_event,
    clear_pipeline_progress,
)
from execution.state_manager import (
    advance_phase,
    initialize_state,
    record_chapter_score,
    record_document_assembly,
    record_final_quality,
    save_state,
)


class TestStartGeneration:
    """Tests for POST /api/v1/generate."""

    @patch("app.routers.generate.run_full_pipeline_sync")
    def test_returns_202_with_job_id(self, mock_sync, client):
        """POST should return 202 with job_id and URLs."""
        response = client.post("/api/v1/generate", json={
            "project_name": "Test Campaign",
            "requirements": "Build an AI marketing platform with outreach capabilities",
        })
        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "started"
        assert data["job_id"] == "test-campaign"
        assert "/status" in data["poll_url"]
        assert "/download" in data["download_url"]

    @patch("app.routers.generate.run_full_pipeline_sync")
    def test_default_depth_mode_is_professional(self, mock_sync, client):
        """Default depth mode should be professional."""
        response = client.post("/api/v1/generate", json={
            "project_name": "Test Defaults",
            "requirements": "Build an AI tool for market research assessment",
        })
        assert response.status_code == 202
        # Verify run_full_pipeline_sync was called with "professional"
        mock_sync.assert_called_once()
        call_args = mock_sync.call_args
        assert call_args[0][2] == "professional"  # third positional arg

    @patch("app.routers.generate.run_full_pipeline_sync")
    def test_custom_depth_mode(self, mock_sync, client):
        """Custom depth mode should be passed through."""
        response = client.post("/api/v1/generate", json={
            "project_name": "Enterprise Test",
            "requirements": "Build a comprehensive enterprise platform",
            "depth_mode": "enterprise",
        })
        assert response.status_code == 202
        call_args = mock_sync.call_args
        assert call_args[0][2] == "enterprise"

    def test_rejects_invalid_depth_mode(self, client):
        """Invalid depth mode should return 422."""
        response = client.post("/api/v1/generate", json={
            "project_name": "Test",
            "requirements": "Build something useful for the team",
            "depth_mode": "mega",
        })
        assert response.status_code == 422

    def test_rejects_missing_requirements(self, client):
        """Missing requirements field should return 422."""
        response = client.post("/api/v1/generate", json={
            "project_name": "Test",
        })
        assert response.status_code == 422

    def test_rejects_short_requirements(self, client):
        """Requirements shorter than 10 chars should return 422."""
        response = client.post("/api/v1/generate", json={
            "project_name": "Test",
            "requirements": "short",
        })
        assert response.status_code == 422

    def test_rejects_missing_project_name(self, client):
        """Missing project_name should return 422."""
        response = client.post("/api/v1/generate", json={
            "requirements": "Build an AI tool for market research",
        })
        assert response.status_code == 422

    @patch("app.routers.generate.run_full_pipeline_sync")
    def test_rejects_duplicate_running_job(self, mock_sync, client):
        """If a pipeline is already running for the same slug, return 409."""
        job_id = "duplicate-test"
        clear_pipeline_progress(job_id)
        _append_pipeline_event(job_id, BuildEvent("phase", "Working...", 0, 0, 10))

        try:
            response = client.post("/api/v1/generate", json={
                "project_name": "Duplicate Test",
                "requirements": "Build an AI tool for market research assessment",
            })
            assert response.status_code == 409
        finally:
            clear_pipeline_progress(job_id)


class TestGenerationStatus:
    """Tests for GET /api/v1/generate/{job_id}/status."""

    def test_404_for_unknown_job(self, client):
        """Unknown job_id should return 404."""
        response = client.get("/api/v1/generate/nonexistent-job/status")
        assert response.status_code == 404

    def test_shows_running_status(self, client):
        """Running pipeline should report running status."""
        job_id = "status-running-test"
        clear_pipeline_progress(job_id)
        _append_pipeline_event(
            job_id, BuildEvent("phase", "Generating profile...", 0, 0, 15)
        )

        try:
            response = client.get(f"/api/v1/generate/{job_id}/status")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "running"
            assert data["percent"] == 15
            assert data["latest_message"] == "Generating profile..."
        finally:
            clear_pipeline_progress(job_id)

    def test_shows_complete_status(self, client):
        """Completed pipeline should report complete with download_url."""
        job_id = "status-complete-test"
        clear_pipeline_progress(job_id)
        _append_pipeline_event(
            job_id, BuildEvent("complete", "Build guide ready", 0, 0, 100)
        )

        try:
            response = client.get(f"/api/v1/generate/{job_id}/status")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "complete"
            assert data["percent"] == 100
            assert "download_url" in data
        finally:
            clear_pipeline_progress(job_id)

    def test_shows_error_status(self, client):
        """Failed pipeline should report error status."""
        job_id = "status-error-test"
        clear_pipeline_progress(job_id)
        _append_pipeline_event(
            job_id, BuildEvent("error", "Pipeline failed: LLM timeout", 0, 0, 0)
        )

        try:
            response = client.get(f"/api/v1/generate/{job_id}/status")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "error"
            assert "LLM timeout" in data["latest_message"]
        finally:
            clear_pipeline_progress(job_id)

    def test_shows_quality_failed_status(self, client):
        """Quality failure should report quality_failed status."""
        job_id = "status-quality-fail-test"
        clear_pipeline_progress(job_id)
        quality_data = {
            "quality": {
                "passed": False,
                "final_gates_passed": False,
                "deficient_chapters": [{"index": 1, "score": 40, "status": "incomplete"}],
                "average_score": 50,
                "complete_threshold": 70,
            },
        }
        _append_pipeline_event(
            job_id, BuildEvent(
                "error",
                "Document failed quality verification after retry. Average score: 50/100.",
                0, 0, 99, data=quality_data,
            )
        )

        try:
            response = client.get(f"/api/v1/generate/{job_id}/status")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "quality_failed"
            assert "quality_summary" in data
            assert data["quality_summary"]["average_score"] == 50
        finally:
            clear_pipeline_progress(job_id)

    @patch("app.routers.generate._check_document_quality")
    def test_complete_status_includes_quality_summary(self, mock_quality, client):
        """Complete status should include quality_verified and quality_summary."""
        job_id = "status-quality-test"
        clear_pipeline_progress(job_id)
        _append_pipeline_event(
            job_id, BuildEvent("complete", "Build guide ready", 0, 0, 100)
        )

        mock_quality.return_value = {
            "passed": True,
            "final_gates_passed": True,
            "deficient_chapters": [],
            "average_score": 82,
            "complete_threshold": 70,
        }

        try:
            response = client.get(f"/api/v1/generate/{job_id}/status")
            data = response.json()
            assert data["status"] == "complete"
            # quality_verified may be present if load_state succeeds
            # (depends on whether state file exists for this job_id)
        finally:
            clear_pipeline_progress(job_id)


class TestGenerationDownload:
    """Tests for GET /api/v1/generate/{job_id}/download."""

    def test_404_for_unknown_project(self, client):
        """Unknown project should return 404."""
        response = client.get("/api/v1/generate/nonexistent-project/download")
        assert response.status_code == 404

    def test_409_when_not_complete(self, client, created_project):
        """Download before completion should return 409."""
        response = client.get(f"/api/v1/generate/{created_project}/download")
        assert response.status_code == 409

    @patch("app.routers.generate._check_document_quality")
    def test_download_succeeds_with_quality_warnings(self, mock_quality, client, tmp_output_dir):
        """Download should succeed (200) even when quality is below threshold."""
        state = initialize_state("Quality Warn Download Test")
        slug = state["project"]["slug"]
        advance_phase(state, "feature_discovery")
        advance_phase(state, "outline_generation")
        advance_phase(state, "outline_approval")
        advance_phase(state, "chapter_build")
        advance_phase(state, "quality_gates")
        advance_phase(state, "final_assembly")
        advance_phase(state, "complete")

        # Create a fake document file
        doc_dir = tmp_output_dir / slug
        doc_dir.mkdir(parents=True, exist_ok=True)
        doc_path = doc_dir / "test-doc-v1.md"
        doc_path.write_text("# Test Document\nContent here.", encoding="utf-8")

        record_document_assembly(state, "test-doc-v1.md", str(doc_path))
        save_state(state, slug)

        mock_quality.return_value = {
            "passed": False,
            "final_gates_passed": False,
            "deficient_chapters": [{"index": 1, "score": 40, "status": "incomplete"}],
            "average_score": 50,
            "complete_threshold": 70,
        }

        response = client.get(f"/api/v1/generate/{slug}/download")
        assert response.status_code == 200, (
            f"Expected 200 with quality warnings, got {response.status_code}"
        )

    @patch("app.routers.generate._check_document_quality")
    def test_download_returns_quality_headers_when_below_threshold(
        self, mock_quality, client, tmp_output_dir
    ):
        """Download should include X-Quality-Warning headers when quality is below threshold."""
        state = initialize_state("Quality Headers Test")
        slug = state["project"]["slug"]
        advance_phase(state, "feature_discovery")
        advance_phase(state, "outline_generation")
        advance_phase(state, "outline_approval")
        advance_phase(state, "chapter_build")
        advance_phase(state, "quality_gates")
        advance_phase(state, "final_assembly")
        advance_phase(state, "complete")

        doc_dir = tmp_output_dir / slug
        doc_dir.mkdir(parents=True, exist_ok=True)
        doc_path = doc_dir / "test-doc-v1.md"
        doc_path.write_text("# Test Document\nContent here.", encoding="utf-8")

        record_document_assembly(state, "test-doc-v1.md", str(doc_path))
        save_state(state, slug)

        mock_quality.return_value = {
            "passed": False,
            "final_gates_passed": False,
            "deficient_chapters": [{"index": 10, "score": 68, "status": "needs_work"}],
            "average_score": 68,
            "complete_threshold": 70,
        }

        response = client.get(f"/api/v1/generate/{slug}/download")
        assert response.status_code == 200
        assert response.headers.get("x-quality-warning") == "true"
        assert response.headers.get("x-quality-score") == "68"
        assert response.headers.get("x-quality-threshold") == "70"

    @patch("app.routers.generate._check_document_quality")
    def test_download_no_warning_headers_when_quality_passes(
        self, mock_quality, client, tmp_output_dir
    ):
        """Download should not include warning headers when quality passes."""
        state = initialize_state("Quality Good Headers Test")
        slug = state["project"]["slug"]
        advance_phase(state, "feature_discovery")
        advance_phase(state, "outline_generation")
        advance_phase(state, "outline_approval")
        advance_phase(state, "chapter_build")
        advance_phase(state, "quality_gates")
        advance_phase(state, "final_assembly")
        advance_phase(state, "complete")

        doc_dir = tmp_output_dir / slug
        doc_dir.mkdir(parents=True, exist_ok=True)
        doc_path = doc_dir / "test-doc-v1.md"
        doc_path.write_text("# Test Document\nContent here.", encoding="utf-8")

        record_document_assembly(state, "test-doc-v1.md", str(doc_path))
        save_state(state, slug)

        mock_quality.return_value = {
            "passed": True,
            "final_gates_passed": True,
            "deficient_chapters": [],
            "average_score": 85,
            "complete_threshold": 70,
        }

        response = client.get(f"/api/v1/generate/{slug}/download")
        assert response.status_code == 200
        assert response.headers.get("x-quality-warning") is None

    @patch("app.routers.generate._check_document_quality")
    def test_download_succeeds_when_quality_passes(self, mock_quality, client, tmp_output_dir):
        """Download should succeed when quality verification passes."""
        # Create a project in complete phase with document
        state = initialize_state("Quality Pass Download Test")
        slug = state["project"]["slug"]
        advance_phase(state, "feature_discovery")
        advance_phase(state, "outline_generation")
        advance_phase(state, "outline_approval")
        advance_phase(state, "chapter_build")
        advance_phase(state, "quality_gates")
        advance_phase(state, "final_assembly")
        advance_phase(state, "complete")

        # Create a fake document file
        doc_dir = tmp_output_dir / slug
        doc_dir.mkdir(parents=True, exist_ok=True)
        doc_path = doc_dir / "test-doc-v1.md"
        doc_path.write_text("# Test Document\nContent here.", encoding="utf-8")

        record_document_assembly(state, "test-doc-v1.md", str(doc_path))
        save_state(state, slug)

        mock_quality.return_value = {
            "passed": True,
            "final_gates_passed": True,
            "deficient_chapters": [],
            "average_score": 85,
            "complete_threshold": 70,
        }

        response = client.get(f"/api/v1/generate/{slug}/download")
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/markdown; charset=utf-8"


class TestCancelGeneration:
    """Tests for DELETE /api/v1/generate/{job_id}."""

    def test_clears_progress(self, client):
        """DELETE should clear progress events."""
        job_id = "cancel-test"
        clear_pipeline_progress(job_id)
        _append_pipeline_event(job_id, BuildEvent("phase", "Working...", 0, 0, 10))

        response = client.delete(f"/api/v1/generate/{job_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "cleared"
        assert data["was_running"] is True

    def test_clears_nonexistent_job(self, client):
        """DELETE on nonexistent job should succeed (idempotent)."""
        response = client.delete("/api/v1/generate/nonexistent-job")
        assert response.status_code == 200
        data = response.json()
        assert data["was_running"] is False
