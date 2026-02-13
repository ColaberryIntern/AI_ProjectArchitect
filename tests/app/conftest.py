"""Test fixtures for the web layer."""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client(tmp_output_dir, monkeypatch):
    """Create a TestClient with output directed to temp directory."""
    import app.dependencies as deps

    monkeypatch.setattr(deps, "OUTPUT_DIR", tmp_output_dir)
    return TestClient(app)


@pytest.fixture
def created_project(client):
    """Create a project and return its slug."""
    response = client.post(
        "/projects/new",
        data={"project_name": "Test Web Project"},
        follow_redirects=False,
    )
    # Extract slug from redirect URL
    location = response.headers["location"]
    slug = location.split("/projects/")[1].split("/")[0]
    return slug
