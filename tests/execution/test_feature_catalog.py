"""Tests for the feature catalog generator."""

import json
from unittest.mock import patch

import pytest

from execution.feature_catalog import (
    CATEGORY_LAYERS,
    FALLBACK_CATALOG,
    LAYER_ARCHITECTURAL,
    LAYER_FUNCTIONAL,
    _parse_catalog_response,
    generate_catalog,
    generate_catalog_from_profile,
    get_catalog_by_category,
    get_catalog_by_layer,
    get_feature_layer,
    get_features_by_ids,
)


class TestFallbackCatalog:
    """Validate the generic fallback catalog structure."""

    def test_has_at_least_60_features(self):
        assert len(FALLBACK_CATALOG) >= 60

    def test_has_13_categories(self):
        categories = {f["category"] for f in FALLBACK_CATALOG}
        assert len(categories) == 13

    def test_all_features_have_required_fields(self):
        for feat in FALLBACK_CATALOG:
            assert "id" in feat, f"Feature missing 'id': {feat}"
            assert "name" in feat, f"Feature missing 'name': {feat}"
            assert "description" in feat, f"Feature missing 'description': {feat}"
            assert "category" in feat, f"Feature missing 'category': {feat}"

    def test_all_ids_unique(self):
        ids = [f["id"] for f in FALLBACK_CATALOG]
        assert len(ids) == len(set(ids))

    def test_each_category_has_multiple_features(self):
        by_cat = get_catalog_by_category(FALLBACK_CATALOG)
        for cat in by_cat:
            assert len(cat["features"]) >= 3, (
                f"Category '{cat['name']}' has only {len(cat['features'])} features"
            )


class TestGetFeaturesById:
    """Test the filter helper."""

    def test_selects_matching_ids(self):
        result = get_features_by_ids(FALLBACK_CATALOG, ["dashboard", "gamification"])
        assert len(result) == 2
        names = {f["name"] for f in result}
        assert "Dashboard" in names
        assert "Gamification" in names

    def test_preserves_catalog_order(self):
        ids = ["export_tools", "dashboard", "api_access"]
        result = get_features_by_ids(FALLBACK_CATALOG, ids)
        # Dashboard comes before API Access in fallback catalog, API Access before Export Tools
        result_ids = [f["id"] for f in result]
        assert result_ids.index("dashboard") < result_ids.index("api_access")
        assert result_ids.index("api_access") < result_ids.index("export_tools")

    def test_empty_ids_returns_empty(self):
        result = get_features_by_ids(FALLBACK_CATALOG, [])
        assert result == []

    def test_unknown_ids_ignored(self):
        result = get_features_by_ids(FALLBACK_CATALOG, ["nonexistent_id"])
        assert result == []


class TestGetCatalogByCategory:
    """Test the category grouping helper."""

    def test_groups_fallback_into_13_categories(self):
        result = get_catalog_by_category(FALLBACK_CATALOG)
        assert len(result) == 13

    def test_each_group_has_name_and_features(self):
        result = get_catalog_by_category(FALLBACK_CATALOG)
        for group in result:
            assert "name" in group
            assert "features" in group
            assert len(group["features"]) > 0


class TestGetFeatureLayer:
    """Test the layer classification helper."""

    def test_functional_categories(self):
        functional_cats = [
            "Core Functionality", "AI & Intelligence", "User Experience",
            "Assessment & Progress", "Engagement", "Integrations",
            "Analytics & Reporting",
        ]
        for cat in functional_cats:
            assert get_feature_layer(cat) == LAYER_FUNCTIONAL

    def test_architectural_categories(self):
        arch_cats = [
            "Architecture & Infrastructure", "Security & Compliance",
            "ML & Model Layer", "DevOps & Deployment",
            "Observability & Monitoring", "Testing & QA",
        ]
        for cat in arch_cats:
            assert get_feature_layer(cat) == LAYER_ARCHITECTURAL

    def test_unknown_category_defaults_to_functional(self):
        assert get_feature_layer("Unknown Category") == LAYER_FUNCTIONAL

    def test_all_fallback_categories_have_layers(self):
        """Every category in the fallback catalog should appear in CATEGORY_LAYERS."""
        categories = {f["category"] for f in FALLBACK_CATALOG}
        for cat in categories:
            assert cat in CATEGORY_LAYERS, f"Category '{cat}' not in CATEGORY_LAYERS"


class TestGetCatalogByLayer:
    """Test the layer grouping helper."""

    def test_returns_two_layers(self):
        result = get_catalog_by_layer(FALLBACK_CATALOG)
        assert LAYER_FUNCTIONAL in result
        assert LAYER_ARCHITECTURAL in result

    def test_functional_has_7_categories(self):
        result = get_catalog_by_layer(FALLBACK_CATALOG)
        assert len(result[LAYER_FUNCTIONAL]) == 7

    def test_architectural_has_6_categories(self):
        result = get_catalog_by_layer(FALLBACK_CATALOG)
        assert len(result[LAYER_ARCHITECTURAL]) == 6

    def test_all_features_accounted_for(self):
        result = get_catalog_by_layer(FALLBACK_CATALOG)
        total = sum(
            len(cat["features"])
            for layer in result.values()
            for cat in layer
        )
        assert total == len(FALLBACK_CATALOG)

    def test_each_category_has_name_and_features(self):
        result = get_catalog_by_layer(FALLBACK_CATALOG)
        for layer in result.values():
            for cat in layer:
                assert "name" in cat
                assert "features" in cat
                assert len(cat["features"]) > 0

    def test_empty_catalog_returns_empty_layers(self):
        result = get_catalog_by_layer([])
        assert result[LAYER_FUNCTIONAL] == []
        assert result[LAYER_ARCHITECTURAL] == []

    def test_old_catalog_falls_to_functional(self):
        """Old catalogs with unknown categories default to functional layer."""
        old_catalog = [
            {"id": "f1", "name": "Feature", "description": "Desc", "category": "Legacy Cat"},
        ]
        result = get_catalog_by_layer(old_catalog)
        assert len(result[LAYER_FUNCTIONAL]) == 1
        assert result[LAYER_ARCHITECTURAL] == []


class TestParseCatalogResponse:
    """Test parsing LLM JSON output into flat feature list."""

    def test_valid_json_parsed(self):
        # Build valid catalog with >= 20 unique features
        data = {"categories": []}
        for i in range(7):
            data["categories"].append({
                "name": f"Cat {i}",
                "features": [
                    {"id": f"f{i*3+j}", "name": f"Feature {i*3+j}", "description": f"Desc {i*3+j}"}
                    for j in range(3)
                ],
            })
        result = _parse_catalog_response(json.dumps(data))
        assert len(result) == 21
        assert result[0]["category"] == "Cat 0"

    def test_invalid_json_returns_fallback(self):
        result = _parse_catalog_response("not json")
        assert result == list(FALLBACK_CATALOG)

    def test_missing_categories_key_returns_fallback(self):
        result = _parse_catalog_response(json.dumps({"data": []}))
        assert result == list(FALLBACK_CATALOG)

    def test_too_few_features_returns_fallback(self):
        data = {
            "categories": [{
                "name": "Core",
                "features": [
                    {"id": "f1", "name": "Only Feature", "description": "Alone"},
                ],
            }],
        }
        result = _parse_catalog_response(json.dumps(data))
        assert result == list(FALLBACK_CATALOG)

    def test_duplicate_ids_deduplicated(self):
        data = {"categories": []}
        for i in range(4):
            data["categories"].append({
                "name": f"Cat {i}",
                "features": [
                    {"id": f"f{j}", "name": f"Feature {j}", "description": f"Desc"}
                    for j in range(5)
                ],
            })
        result = _parse_catalog_response(json.dumps(data))
        # Only 5 unique IDs (f0-f4), falls back since < 20
        assert result == list(FALLBACK_CATALOG)


class TestGenerateCatalog:
    """Test the main generate_catalog function."""

    def test_empty_idea_returns_fallback(self):
        result = generate_catalog("")
        assert result == list(FALLBACK_CATALOG)

    def test_no_idea_returns_fallback(self):
        result = generate_catalog("   ")
        assert result == list(FALLBACK_CATALOG)

    @patch("execution.feature_catalog.is_available", return_value=False)
    def test_llm_unavailable_returns_fallback(self, mock_avail):
        result = generate_catalog("Build an AI app")
        assert result == list(FALLBACK_CATALOG)

    @patch("execution.feature_catalog.chat")
    @patch("execution.feature_catalog.is_available", return_value=True)
    def test_llm_success_returns_parsed_catalog(self, mock_avail, mock_chat):
        """Valid LLM response returns project-specific features."""
        categories = []
        for i in range(7):
            features = [
                {"id": f"cat{i}_f{j}", "name": f"Feature {i}.{j}", "description": "A feature"}
                for j in range(4 if i < 4 else 3)
            ]
            categories.append({"name": f"Category {i}", "features": features})

        from execution.llm_client import LLMResponse
        mock_chat.return_value = LLMResponse(
            content=json.dumps({"categories": categories}),
            model="gpt-4o-mini",
            usage={"prompt_tokens": 100, "completion_tokens": 200},
            stop_reason="stop",
        )

        result = generate_catalog("Build an AI-powered learning platform")
        assert len(result) == 25
        assert result[0]["category"] == "Category 0"

    @patch("execution.feature_catalog.chat")
    @patch("execution.feature_catalog.is_available", return_value=True)
    def test_llm_error_returns_fallback(self, mock_avail, mock_chat):
        from execution.llm_client import LLMClientError
        mock_chat.side_effect = LLMClientError("API error")

        result = generate_catalog("Build an AI app")
        assert result == list(FALLBACK_CATALOG)


class TestGenerateCatalogFromProfile:
    """Test the profile-driven catalog generation."""

    def _make_profile(self):
        """Build a minimal profile with confirmed fields."""
        from execution.state_manager import PROFILE_REQUIRED_FIELDS
        profile = {}
        for field in PROFILE_REQUIRED_FIELDS:
            profile[field] = {
                "selected": f"value_{field}",
                "confidence": 0.85,
                "confirmed": True,
                "options": [],
            }
        return profile

    def test_empty_profile_returns_fallback(self):
        result = generate_catalog_from_profile({})
        assert result == list(FALLBACK_CATALOG)

    def test_no_selected_values_returns_fallback(self):
        profile = self._make_profile()
        for field in profile:
            if isinstance(profile[field], dict) and "selected" in profile[field]:
                profile[field]["selected"] = ""
        result = generate_catalog_from_profile(profile)
        assert result == list(FALLBACK_CATALOG)

    @patch("execution.feature_catalog.is_available", return_value=False)
    def test_llm_unavailable_returns_fallback(self, mock_avail):
        profile = self._make_profile()
        result = generate_catalog_from_profile(profile)
        assert result == list(FALLBACK_CATALOG)

    @patch("execution.feature_catalog.chat")
    @patch("execution.feature_catalog.is_available", return_value=True)
    def test_llm_success_returns_parsed_catalog(self, mock_avail, mock_chat):
        categories = []
        for i in range(4):
            features = [
                {"id": f"cat{i}_f{j}", "name": f"Feature {i}.{j}", "description": "A feature"}
                for j in range(7 if i < 1 else 6)
            ]
            categories.append({"name": f"Category {i}", "features": features})

        from execution.llm_client import LLMResponse
        mock_chat.return_value = LLMResponse(
            content=json.dumps({"categories": categories}),
            model="gpt-4o-mini",
            usage={"prompt_tokens": 100, "completion_tokens": 200},
            stop_reason="stop",
        )

        profile = self._make_profile()
        result = generate_catalog_from_profile(profile)
        assert len(result) == 25
        assert result[0]["category"] == "Category 0"

    @patch("execution.feature_catalog.chat")
    @patch("execution.feature_catalog.is_available", return_value=True)
    def test_llm_error_returns_fallback(self, mock_avail, mock_chat):
        from execution.llm_client import LLMClientError
        mock_chat.side_effect = LLMClientError("API error")

        profile = self._make_profile()
        result = generate_catalog_from_profile(profile)
        assert result == list(FALLBACK_CATALOG)
