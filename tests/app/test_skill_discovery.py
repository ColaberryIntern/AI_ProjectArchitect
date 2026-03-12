"""Tests for skill discovery routes and UI integration."""

import pytest

from execution.state_manager import (
    PROFILE_REQUIRED_FIELDS,
    advance_phase,
    confirm_all_profile_fields,
    get_project_profile,
    get_selected_skills,
    load_state,
    record_idea,
    save_state,
    set_profile_derived,
    set_profile_field,
    set_selected_skills,
    set_skill_catalog,
)


@pytest.fixture
def feature_project(client, created_project):
    """Create a project in the feature_discovery phase with complete profile."""
    state = load_state(created_project)
    record_idea(state, "Build an AI tool")
    get_project_profile(state)
    for field in PROFILE_REQUIRED_FIELDS:
        set_profile_field(state, field, [
            {"value": f"{field}_v1", "label": "Option 1", "description": "Desc"},
        ], f"{field}_v1", 0.85)
    confirm_all_profile_fields(state, {f: f"{f}_v1" for f in PROFILE_REQUIRED_FIELDS})
    set_profile_derived(state, ["REST"], ["uptime"], ["metric"], ["risk"], ["use case"])
    advance_phase(state, "feature_discovery")
    save_state(state, created_project)
    return created_project


class TestSkillsTabRendering:
    """Verify the Skills & Tools tab appears on the feature discovery page."""

    def test_skills_tab_present(self, client, feature_project):
        response = client.get(f"/projects/{feature_project}/feature-discovery")
        assert response.status_code == 200
        assert "Skills" in response.text

    def test_skills_pane_present(self, client, feature_project):
        response = client.get(f"/projects/{feature_project}/feature-discovery")
        assert response.status_code == 200
        assert 'id="layer-skills"' in response.text

    def test_skills_auto_suggested_on_first_visit(self, client, feature_project):
        """First visit should populate the skill catalog in state."""
        client.get(f"/projects/{feature_project}/feature-discovery")
        state = load_state(feature_project)
        # Catalog should be populated (from registry or fallback)
        assert len(state["skills"]["catalog"]) > 0
        assert state["skills"]["suggested_at"] is not None


class TestSkillsSaveEndpoint:
    """Test POST /api/skills/save."""

    def test_save_skill_ids(self, client, feature_project):
        # Seed a catalog first
        state = load_state(feature_project)
        set_skill_catalog(state, [
            {"id": "web_search", "name": "Web Search", "description": "d", "category": "Data"},
            {"id": "mcp_github", "name": "GitHub", "description": "d", "category": "MCP"},
        ])
        save_state(state, feature_project)

        response = client.post(
            f"/projects/{feature_project}/api/skills/save",
            json={"skills": ["web_search"]},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["saved"] == 1

        state = load_state(feature_project)
        assert state["skills"]["selected"] == ["web_search"]

    def test_save_empty_skills(self, client, feature_project):
        response = client.post(
            f"/projects/{feature_project}/api/skills/save",
            json={"skills": []},
        )
        assert response.status_code == 200
        state = load_state(feature_project)
        assert state["skills"]["selected"] == []


class TestAddCustomSkillEndpoint:
    """Test POST /api/skills/add-custom."""

    def test_add_custom_skill(self, client, feature_project):
        response = client.post(
            f"/projects/{feature_project}/api/skills/add-custom",
            json={
                "id": "my_tool",
                "name": "My Custom Tool",
                "description": "Does custom things",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["added"] == "my_tool"

        state = load_state(feature_project)
        assert len(state["skills"]["custom"]) == 1
        assert state["skills"]["custom"][0]["id"] == "my_tool"

    def test_add_custom_skill_missing_fields(self, client, feature_project):
        """Missing required fields should return 400."""
        response = client.post(
            f"/projects/{feature_project}/api/skills/add-custom",
            json={"id": "x"},
        )
        assert response.status_code == 400


class TestSkillSuggestEndpoint:
    """Test GET /api/skills/suggest."""

    def test_suggest_returns_json(self, client, feature_project):
        response = client.get(f"/projects/{feature_project}/api/skills/suggest")
        assert response.status_code == 200
        data = response.json()
        assert "suggested" in data or "skills" in data or "ok" in data


class TestSkillsInFeatureSelection:
    """Test that skill selections are saved when features are submitted."""

    def test_skills_saved_with_feature_selection(self, client, feature_project):
        """Selecting features + skills should save both."""
        # First visit to generate catalogs
        client.get(f"/projects/{feature_project}/feature-discovery")
        state = load_state(feature_project)
        catalog = state["features"]["catalog"]
        feature_ids = [f["id"] for f in catalog[:3]]

        # Also seed skill catalog
        set_skill_catalog(state, [
            {"id": "web_search", "name": "Web Search", "description": "d", "category": "Data"},
        ])
        save_state(state, feature_project)

        response = client.post(
            f"/projects/{feature_project}/feature-discovery/select",
            data={"features": feature_ids, "skills": ["web_search"]},
            follow_redirects=False,
        )
        assert response.status_code == 303

        state = load_state(feature_project)
        assert state["skills"]["selected"] == ["web_search"]
        assert state["skills"]["approved"] is True
