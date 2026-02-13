"""Tests for project management routes."""

import pytest


class TestIndexPage:
    def test_index_returns_200(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "Projects" in response.text

    def test_index_shows_no_projects_message(self, client):
        response = client.get("/")
        assert "No projects yet" in response.text


class TestCreateProject:
    def test_create_redirects_to_idea_intake(self, client):
        response = client.post(
            "/projects/new",
            data={"project_name": "My Test Project"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "/idea-intake" in response.headers["location"]

    def test_create_project_appears_in_list(self, client):
        client.post(
            "/projects/new",
            data={"project_name": "Listed Project"},
            follow_redirects=False,
        )
        response = client.get("/")
        assert "Listed Project" in response.text

    def test_create_project_with_empty_name_fails(self, client):
        response = client.post(
            "/projects/new",
            data={"project_name": ""},
            follow_redirects=False,
        )
        assert response.status_code == 422


class TestProjectDashboard:
    def test_dashboard_redirects_to_current_phase(self, created_project):
        from tests.app.conftest import TestClient
        from app.main import app

        client = TestClient(app)
        response = client.get(
            f"/projects/{created_project}",
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "idea-intake" in response.headers["location"]

    def test_nonexistent_project_returns_404(self, client):
        response = client.get("/projects/nonexistent-slug")
        assert response.status_code == 404


class TestGuidedIdeationRedirect:
    def test_guided_ideation_redirects(self, client, created_project):
        response = client.get(
            f"/projects/{created_project}/guided-ideation",
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert f"/projects/{created_project}" in response.headers["location"]


class TestDashboardPhaseMigration:
    def test_unknown_phase_with_idea_migrates_to_feature_discovery(self, client, created_project):
        """Dashboard auto-migrates deprecated phases to the correct valid phase."""
        from execution.state_manager import load_state, record_idea, save_state

        state = load_state(created_project)
        record_idea(state, "Test idea")
        state["current_phase"] = "guided_ideation"
        save_state(state, created_project)

        response = client.get(
            f"/projects/{created_project}",
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "feature-discovery" in response.headers["location"]

        # Verify state was persisted
        state = load_state(created_project)
        assert state["current_phase"] == "feature_discovery"

    def test_unknown_phase_without_idea_migrates_to_idea_intake(self, client, created_project):
        """Dashboard migrates to idea_intake if no idea was captured."""
        from execution.state_manager import load_state, save_state

        state = load_state(created_project)
        state["current_phase"] = "guided_ideation"
        save_state(state, created_project)

        response = client.get(
            f"/projects/{created_project}",
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "idea-intake" in response.headers["location"]


class TestDeleteProject:
    def test_delete_existing_project(self, client, created_project):
        response = client.post(
            f"/projects/{created_project}/delete",
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/"
        # Verify project is gone
        response = client.get(f"/projects/{created_project}")
        assert response.status_code == 404

    def test_delete_nonexistent_project(self, client):
        response = client.post(
            "/projects/nonexistent-slug/delete",
            follow_redirects=False,
        )
        assert response.status_code == 404

    def test_delete_get_not_allowed(self, client, created_project):
        response = client.get(f"/projects/{created_project}/delete")
        assert response.status_code == 405

    def test_delete_permission_error_shows_message(self, client, created_project):
        from unittest.mock import patch
        with patch("app.routers.projects.delete_project", side_effect=OSError("Cannot delete: files are locked.")):
            response = client.post(
                f"/projects/{created_project}/delete",
                follow_redirects=False,
            )
        assert response.status_code == 303
        assert "error=" in response.headers["location"]


class TestDeleteAllProjects:
    def test_delete_all_with_projects(self, client):
        # Create 3 projects (use names that won't substring-match the app title)
        for name in ["Alpha Test", "Beta Test", "Gamma Test"]:
            client.post("/projects/new", data={"project_name": name}, follow_redirects=False)
        # Verify they exist
        response = client.get("/")
        assert "Alpha Test" in response.text
        # Delete all
        response = client.post("/projects/delete-all", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/"
        # Verify they're gone
        response = client.get("/")
        assert "Alpha Test" not in response.text
        assert "Beta Test" not in response.text
        assert "Gamma Test" not in response.text

    def test_delete_all_with_no_projects(self, client):
        response = client.post("/projects/delete-all", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/"
