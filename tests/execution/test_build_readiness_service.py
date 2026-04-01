"""Unit tests for execution/build_readiness_service.py."""

from execution.build_readiness_service import (
    check_architecture_completeness,
    check_missing_core_features,
    compute_build_readiness,
)


# ---------------------------------------------------------------------------
# check_missing_core_features
# ---------------------------------------------------------------------------


class TestMissingCoreFeatures:
    def test_paid_monetization_needs_payment(self):
        features = [{"id": "user_registration"}, {"id": "ci_cd_pipeline"}]
        profile = {"monetization_model": {"selected": "subscription"}}
        result = check_missing_core_features(features, profile)
        assert result["passed"] is False
        assert "payment_gateway" in result["missing"]

    def test_paid_monetization_with_payment(self):
        features = [
            {"id": "user_registration"},
            {"id": "ci_cd_pipeline"},
            {"id": "payment_gateway"},
        ]
        profile = {"monetization_model": {"selected": "subscription"}}
        result = check_missing_core_features(features, profile)
        assert "payment_gateway" not in result["missing"]

    def test_free_model_no_payment_needed(self):
        features = [{"id": "ci_cd_pipeline"}]
        profile = {"monetization_model": {"selected": "open_source"}}
        result = check_missing_core_features(features, profile)
        assert "payment_gateway" not in result["missing"]

    def test_ai_depth_needs_ai_feature(self):
        features = [{"id": "ci_cd_pipeline"}]
        profile = {"ai_depth": {"selected": "predictive_ml"}}
        result = check_missing_core_features(features, profile)
        assert result["passed"] is False
        assert any("ai_feature" in m.lower() for m in result["missing"])

    def test_ai_depth_with_ai_feature(self):
        features = [
            {"id": "ci_cd_pipeline"},
            {"id": "ai_recommendations"},
        ]
        profile = {"ai_depth": {"selected": "predictive_ml"}}
        result = check_missing_core_features(features, profile)
        assert not any("ai_feature" in m.lower() for m in result["missing"])

    def test_saas_needs_registration_and_dashboard(self):
        features = [{"id": "ci_cd_pipeline"}]
        profile = {"deployment_type": {"selected": "saas"}}
        result = check_missing_core_features(features, profile)
        assert "user_registration" in result["missing"]
        assert "dashboard" in result["missing"]

    def test_always_needs_ci_cd(self):
        features = [{"id": "user_registration"}]
        profile = {}
        result = check_missing_core_features(features, profile)
        assert "ci_cd_pipeline" in result["missing"]

    def test_fully_stocked(self):
        features = [
            {"id": "user_registration"},
            {"id": "dashboard"},
            {"id": "ci_cd_pipeline"},
            {"id": "payment_gateway"},
            {"id": "ai_recommendations"},
        ]
        profile = {
            "monetization_model": {"selected": "subscription"},
            "ai_depth": {"selected": "predictive_ml"},
            "deployment_type": {"selected": "saas"},
        }
        result = check_missing_core_features(features, profile)
        assert result["passed"] is True
        assert result["missing"] == []


# ---------------------------------------------------------------------------
# check_architecture_completeness
# ---------------------------------------------------------------------------


class TestArchitectureCompleteness:
    def test_microservices_needs_support(self):
        features = [{"id": "microservices"}]
        profile = {}
        result = check_architecture_completeness(features, profile)
        assert result["passed"] is False
        assert "api_gateway" in result["missing"]
        assert "distributed_tracing" in result["missing"]
        assert "message_queue" in result["missing"]

    def test_monolith_no_gateway_needed(self):
        features = [{"id": "modular_monolith"}]
        profile = {}
        result = check_architecture_completeness(features, profile)
        assert "api_gateway" not in result["missing"]

    def test_large_project_needs_observability(self):
        features = [{"id": f"f{i}"} for i in range(6)]
        profile = {}
        result = check_architecture_completeness(features, profile)
        assert "app_logging" in result["missing"]
        assert "health_checks" in result["missing"]

    def test_small_project_no_observability_needed(self):
        features = [{"id": f"f{i}"} for i in range(3)]
        profile = {}
        result = check_architecture_completeness(features, profile)
        assert "app_logging" not in result["missing"]
        assert "health_checks" not in result["missing"]

    def test_ai_depth_needs_monitoring(self):
        features = [{"id": "recommender_system"}]
        profile = {"ai_depth": {"selected": "autonomous_ai"}}
        result = check_architecture_completeness(features, profile)
        assert any("monitoring" in m for m in result["missing"])


# ---------------------------------------------------------------------------
# compute_build_readiness
# ---------------------------------------------------------------------------


class TestBuildReadiness:
    def test_valid_project_ready(self):
        features = [
            {"id": "user_registration"},
            {"id": "ci_cd_pipeline"},
            {"id": "app_logging"},
            {"id": "health_checks"},
            {"id": "dashboard"},
            {"id": "search_filtering"},
            {"id": "dark_mode"},
            {"id": "responsive_design"},
        ]
        profile = {
            "monetization_model": {"selected": "open_source"},
            "ai_depth": {"selected": "no_ai"},
        }
        result = compute_build_readiness(features, [], profile)
        assert result["ready"] is True
        assert result["risk_level"] == "low"
        assert result["missing_components"] == []

    def test_medium_risk(self):
        features = [{"id": "user_registration"}]
        profile = {"monetization_model": {"selected": "subscription"}}
        result = compute_build_readiness(features, [], profile)
        # Missing: payment_gateway, ci_cd_pipeline (2 items = medium)
        assert result["risk_level"] == "medium"
        assert result["ready"] is False

    def test_high_risk(self):
        features = [{"id": f"f{i}"} for i in range(6)]
        features.append({"id": "microservices"})
        profile = {
            "monetization_model": {"selected": "subscription"},
            "ai_depth": {"selected": "predictive_ml"},
            "deployment_type": {"selected": "saas"},
        }
        result = compute_build_readiness(features, [], profile)
        assert result["risk_level"] == "high"
        assert result["ready"] is False
        assert len(result["missing_components"]) >= 4

    def test_details_included(self):
        features = [{"id": "ci_cd_pipeline"}]
        profile = {}
        result = compute_build_readiness(features, [], profile)
        assert "core_features" in result["details"]
        assert "architecture" in result["details"]
