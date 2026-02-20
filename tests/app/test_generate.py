"""Tests for app/routers/generate.py."""

from unittest.mock import patch

import pytest

from execution.auto_builder import BuildEvent
from execution.full_pipeline import (
    _append_pipeline_event,
    clear_pipeline_progress,
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
