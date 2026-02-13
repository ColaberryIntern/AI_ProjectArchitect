"""Tests for feature discovery routes."""

import pytest
from unittest.mock import patch

from execution.feature_catalog import FALLBACK_CATALOG
from execution.state_manager import (
    PROFILE_REQUIRED_FIELDS,
    advance_phase,
    confirm_all_profile_fields,
    get_project_profile,
    load_state,
    record_idea,
    save_state,
    set_profile_derived,
    set_profile_field,
)


@pytest.fixture
def feature_project(client, created_project):
    """Create a project in the feature_discovery phase with complete profile."""
    state = load_state(created_project)
    record_idea(state, "Build an AI tool")
    # Set up complete profile
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


class TestFeatureDiscoveryPage:
    def test_shows_page(self, client, feature_project):
        response = client.get(f"/projects/{feature_project}/feature-discovery")
        assert response.status_code == 200
        assert "Feature" in response.text

    def test_no_chat_panel_on_feature_discovery(self, client, feature_project):
        response = client.get(f"/projects/{feature_project}/feature-discovery")
        assert response.status_code == 200
        assert 'id="chat-messages"' not in response.text
        assert 'id="chat-form"' not in response.text

    def test_generates_catalog_on_first_visit(self, client, feature_project):
        """First visit should generate a catalog (falls back to generic)."""
        response = client.get(f"/projects/{feature_project}/feature-discovery")
        assert response.status_code == 200
        state = load_state(feature_project)
        assert len(state["features"]["catalog"]) >= 10

    def test_catalog_persists_across_reloads(self, client, feature_project):
        """Catalog should be generated once and persist."""
        client.get(f"/projects/{feature_project}/feature-discovery")
        state1 = load_state(feature_project)
        catalog1 = state1["features"]["catalog"]

        client.get(f"/projects/{feature_project}/feature-discovery")
        state2 = load_state(feature_project)
        catalog2 = state2["features"]["catalog"]

        assert catalog1 == catalog2


class TestSelectFeatures:
    def test_select_features_advances_to_outline(self, client, feature_project):
        """Selecting features from catalog should advance to outline generation."""
        # First visit to generate catalog
        client.get(f"/projects/{feature_project}/feature-discovery")
        state = load_state(feature_project)
        catalog = state["features"]["catalog"]
        # Select first 3 features
        feature_ids = [f["id"] for f in catalog[:3]]

        response = client.post(
            f"/projects/{feature_project}/feature-discovery/select",
            data={"features": feature_ids},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "outline-generation" in response.headers["location"]

        state = load_state(feature_project)
        assert len(state["features"]["core"]) == 3
        assert state["features"]["approved"] is True
        assert state["current_phase"] == "outline_generation"

    def test_selected_features_match_catalog(self, client, feature_project):
        """Selected feature names should match catalog entries."""
        client.get(f"/projects/{feature_project}/feature-discovery")
        state = load_state(feature_project)
        catalog = state["features"]["catalog"]
        picked = catalog[:2]
        feature_ids = [f["id"] for f in picked]

        client.post(
            f"/projects/{feature_project}/feature-discovery/select",
            data={"features": feature_ids},
            follow_redirects=False,
        )
        state = load_state(feature_project)
        core_names = [f["name"] for f in state["features"]["core"]]
        for feat in picked:
            assert feat["name"] in core_names


class TestAddFeature:
    def test_add_core_feature(self, client, feature_project):
        response = client.post(
            f"/projects/{feature_project}/feature-discovery/add",
            data={
                "feature_type": "core",
                "feature_id": "feat-001",
                "name": "Guided Questioning",
                "description": "Structured questioning",
                "rationale": "Eliminates idea vagueness through systematic inquiry",
                "problem_mapped_to": "vagueness",
                "build_order": "1",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        state = load_state(feature_project)
        assert len(state["features"]["core"]) == 1
        assert state["features"]["core"][0]["name"] == "Guided Questioning"

    def test_add_optional_feature(self, client, feature_project):
        response = client.post(
            f"/projects/{feature_project}/feature-discovery/add",
            data={
                "feature_type": "optional",
                "feature_id": "feat-opt-001",
                "name": "PDF Export",
                "description": "Export as PDF",
                "rationale": "Enables distribution to non-technical stakeholders",
                "deferred": "true",
                "defer_reason": "Not MVP",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        state = load_state(feature_project)
        assert len(state["features"]["optional"]) == 1


class TestApproveFeatures:
    def test_approve_advances_phase(self, client, feature_project):
        # Add a feature first
        client.post(
            f"/projects/{feature_project}/feature-discovery/add",
            data={
                "feature_type": "core",
                "feature_id": "feat-001",
                "name": "Test Feature",
                "description": "A test feature",
                "rationale": "Needed for testing the approval flow",
                "problem_mapped_to": "testing",
                "build_order": "1",
            },
            follow_redirects=False,
        )
        response = client.post(
            f"/projects/{feature_project}/feature-discovery/approve",
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "outline-generation" in response.headers["location"]


class TestValidateFeatures:
    def test_returns_json(self, client, feature_project):
        client.post(
            f"/projects/{feature_project}/feature-discovery/add",
            data={
                "feature_type": "core",
                "feature_id": "feat-001",
                "name": "Test Feature",
                "description": "A test feature",
                "rationale": "Needed for testing the validation endpoint",
                "problem_mapped_to": "testing",
                "build_order": "1",
            },
            follow_redirects=False,
        )
        response = client.get(f"/projects/{feature_project}/api/features/validate")
        assert response.status_code == 200
        data = response.json()
        assert "problem_mapping" in data
        assert "intern_explainability" in data


class TestProfileGating:
    def test_incomplete_profile_redirects_to_profile_review(self, client, created_project):
        """Feature discovery should redirect to profile review if profile incomplete."""
        state = load_state(created_project)
        record_idea(state, "Build something")
        advance_phase(state, "feature_discovery")
        # Profile NOT confirmed
        save_state(state, created_project)

        response = client.get(
            f"/projects/{created_project}/feature-discovery",
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "idea-intake/profile" in response.headers["location"]


class TestValidateAlignment:
    def test_alignment_endpoint_returns_json(self, client, feature_project):
        """Alignment validation endpoint returns structured JSON."""
        response = client.get(f"/projects/{feature_project}/api/features/validate-alignment")
        assert response.status_code == 200
        data = response.json()
        assert "all_passed" in data
        assert "required_fields" in data
        assert "ai_depth_alignment" in data


class TestFeatureCatalog:
    """Tests for unified feature catalog rendering."""

    def test_catalog_renders_header(self, client, feature_project):
        """Feature discovery page should render the catalog header."""
        response = client.get(f"/projects/{feature_project}/feature-discovery")
        assert response.status_code == 200
        assert "Select Features for Your Project" in response.text

    def test_has_layer_tabs(self, client, feature_project):
        """Feature discovery should have layer tabs for functional and architectural."""
        response = client.get(f"/projects/{feature_project}/feature-discovery")
        assert response.status_code == 200
        assert "switchLayer" in response.text
        assert "Product Features" in response.text
        assert "Architecture" in response.text

    def test_both_layer_panes_rendered(self, client, feature_project):
        """Both layer panes should be present in the HTML."""
        response = client.get(f"/projects/{feature_project}/feature-discovery")
        assert response.status_code == 200
        assert 'id="layer-functional"' in response.text
        assert 'id="layer-architectural"' in response.text

    def test_category_toggle_buttons(self, client, feature_project):
        """Category toggle buttons should be rendered."""
        response = client.get(f"/projects/{feature_project}/feature-discovery")
        assert response.status_code == 200
        assert "toggleCategory" in response.text


class TestMutualExclusionRoute:
    """Tests for mutual exclusion enforcement."""

    def test_conflicting_features_blocked(self, client, feature_project):
        """Selecting mutually exclusive features should redirect with error."""
        # First visit to generate catalog
        client.get(f"/projects/{feature_project}/feature-discovery")

        response = client.post(
            f"/projects/{feature_project}/feature-discovery/select",
            data={"features": ["microservices", "modular_monolith", "dashboard"]},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "feature-discovery" in response.headers["location"]
        assert "error" in response.headers["location"]

    def test_non_conflicting_features_accepted(self, client, feature_project):
        """Selecting non-conflicting features should advance to outline."""
        client.get(f"/projects/{feature_project}/feature-discovery")

        response = client.post(
            f"/projects/{feature_project}/feature-discovery/select",
            data={"features": ["microservices", "dashboard", "rbac"]},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "outline-generation" in response.headers["location"]

    def test_validate_exclusions_endpoint(self, client, feature_project):
        """Exclusion validation API should return JSON."""
        response = client.get(
            f"/projects/{feature_project}/api/features/validate-exclusions"
        )
        assert response.status_code == 200
        data = response.json()
        assert "passed" in data
        assert "violations" in data


class TestIntelligenceGoalsRoutes:
    """Tests for intelligence goals API endpoints."""

    def test_generate_goals_returns_json(self, client, feature_project):
        """Generate goals endpoint should return goals array."""
        response = client.get(
            f"/projects/{feature_project}/api/intelligence-goals/generate"
        )
        assert response.status_code == 200
        data = response.json()
        assert "goals" in data
        assert isinstance(data["goals"], list)

    def test_generate_goals_returns_at_least_4(self, client, feature_project):
        """Should return at least 4 goals (fallback minimum)."""
        response = client.get(
            f"/projects/{feature_project}/api/intelligence-goals/generate"
        )
        data = response.json()
        assert len(data["goals"]) >= 4

    def test_save_goals_stores_to_state_new_fields(self, client, feature_project):
        """Save goals with new field names should persist to project state."""
        goals = [
            {"id": "g1", "user_facing_label": "Predict churn", "description": "Forecast user churn", "goal_type": "prediction"},
            {"id": "g2", "user_facing_label": "Classify tickets", "description": "Auto-sort tickets", "goal_type": "classification"},
        ]
        response = client.post(
            f"/projects/{feature_project}/api/intelligence-goals/save",
            json={"goals": goals},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["saved"] == 2

        # Verify persisted with canonical field names
        state = load_state(feature_project)
        stored = state["project_profile"]["intelligence_goals"]
        assert len(stored) == 2
        assert stored[0]["id"] == "g1"
        assert stored[0]["user_facing_label"] == "Predict churn"
        assert stored[0]["goal_type"] == "prediction"

    def test_save_goals_accepts_old_field_names(self, client, feature_project):
        """Save endpoint should accept both old and new field names."""
        goals = [
            {"id": "g1", "label": "Predict churn", "description": "Forecast user churn", "type": "prediction"},
        ]
        response = client.post(
            f"/projects/{feature_project}/api/intelligence-goals/save",
            json={"goals": goals},
        )
        assert response.status_code == 200
        state = load_state(feature_project)
        stored = state["project_profile"]["intelligence_goals"]
        assert stored[0]["user_facing_label"] == "Predict churn"

    def test_save_goals_validates_structure(self, client, feature_project):
        """Save should handle malformed goal data gracefully."""
        response = client.post(
            f"/projects/{feature_project}/api/intelligence-goals/save",
            json={"goals": [{"id": "g1", "user_facing_label": "Goal", "goal_type": "prediction"}]},
        )
        assert response.status_code == 200

    def test_save_goals_truncates_long_labels(self, client, feature_project):
        """Labels should be truncated to 100 chars."""
        goals = [{"id": "g1", "user_facing_label": "A" * 200, "description": "D", "goal_type": "prediction"}]
        response = client.post(
            f"/projects/{feature_project}/api/intelligence-goals/save",
            json={"goals": goals},
        )
        assert response.status_code == 200
        state = load_state(feature_project)
        stored = state["project_profile"]["intelligence_goals"]
        assert len(stored[0]["user_facing_label"]) <= 100

    def test_feature_discovery_page_has_intelligence_goals_context(self, client, feature_project):
        """Feature discovery page should include intelligence goals section when AI depth triggers."""
        state = load_state(feature_project)
        record_idea(state, "Build an AI-powered recommendation engine")
        save_state(state, feature_project)
        response = client.get(f"/projects/{feature_project}/feature-discovery")
        assert response.status_code == 200
        assert "Intelligence Goals" in response.text or "intelligence-goals" in response.text

    def test_auto_generation_on_first_visit(self, client, feature_project):
        """Intelligence goals should auto-generate on first visit when AI triggers."""
        state = load_state(feature_project)
        record_idea(state, "Build an AI-powered recommendation engine")
        # Set ai_depth to trigger goals
        profile = get_project_profile(state)
        profile["ai_depth"]["selected"] = "predictive_ml"
        profile["ai_depth"]["confirmed"] = True
        save_state(state, feature_project)

        # First visit should auto-generate goals
        response = client.get(f"/projects/{feature_project}/feature-discovery")
        assert response.status_code == 200

        state = load_state(feature_project)
        goals = state["project_profile"].get("intelligence_goals", [])
        assert len(goals) >= 4  # Auto-generated at least 4 fallback goals
