"""Tests for the profile validator."""

import pytest

from execution.profile_validator import (
    check_ai_depth_alignment,
    check_deployment_alignment,
    check_field_confidence,
    check_monetization_alignment,
    check_mvp_scope_alignment,
    check_required_fields,
    check_success_metrics_exist,
    run_all_profile_checks,
)
from execution.state_manager import PROFILE_REQUIRED_FIELDS


def _make_confirmed_profile(overrides=None):
    """Build a fully confirmed profile for testing."""
    profile = {}
    for field in PROFILE_REQUIRED_FIELDS:
        profile[field] = {
            "selected": f"value_{field}",
            "confidence": 0.85,
            "confirmed": True,
            "options": [],
        }
    profile["technical_constraints"] = ["REST API"]
    profile["non_functional_requirements"] = ["99.9% uptime"]
    profile["success_metrics"] = ["50% faster planning"]
    profile["risk_assessment"] = ["LLM dependency"]
    profile["core_use_cases"] = ["Submit idea"]
    profile["selected_features"] = []
    profile["generated_at"] = "2025-01-01T00:00:00+00:00"
    profile["confirmed_at"] = "2025-01-01T00:01:00+00:00"

    if overrides:
        for key, value in overrides.items():
            if isinstance(value, dict) and key in profile and isinstance(profile[key], dict):
                profile[key].update(value)
            else:
                profile[key] = value

    return profile


class TestCheckRequiredFields:
    def test_all_confirmed_passes(self):
        profile = _make_confirmed_profile()
        result = check_required_fields(profile)
        assert result["passed"] is True
        assert result["missing"] == []

    def test_one_unconfirmed_fails(self):
        profile = _make_confirmed_profile()
        profile["ai_depth"]["confirmed"] = False
        result = check_required_fields(profile)
        assert result["passed"] is False
        assert "ai_depth" in result["missing"]

    def test_all_unconfirmed_fails(self):
        profile = _make_confirmed_profile()
        for field in PROFILE_REQUIRED_FIELDS:
            profile[field]["confirmed"] = False
        result = check_required_fields(profile)
        assert result["passed"] is False
        assert len(result["missing"]) == 7

    def test_missing_field_key_fails(self):
        profile = _make_confirmed_profile()
        del profile["mvp_scope"]
        result = check_required_fields(profile)
        assert result["passed"] is False
        assert "mvp_scope" in result["missing"]

    def test_empty_profile_fails(self):
        result = check_required_fields({})
        assert result["passed"] is False
        assert len(result["missing"]) == 7


class TestCheckFieldConfidence:
    def test_all_above_threshold_passes(self):
        profile = _make_confirmed_profile()
        result = check_field_confidence(profile, min_confidence=0.7)
        assert result["passed"] is True
        assert result["low_confidence"] == []

    def test_one_below_threshold_fails(self):
        profile = _make_confirmed_profile()
        profile["target_user"]["confidence"] = 0.5
        result = check_field_confidence(profile, min_confidence=0.7)
        assert result["passed"] is False
        assert len(result["low_confidence"]) == 1
        assert result["low_confidence"][0]["field"] == "target_user"

    def test_none_confidence_fails(self):
        profile = _make_confirmed_profile()
        profile["deployment_type"]["confidence"] = None
        result = check_field_confidence(profile, min_confidence=0.7)
        assert result["passed"] is False

    def test_custom_threshold(self):
        profile = _make_confirmed_profile()
        for field in PROFILE_REQUIRED_FIELDS:
            profile[field]["confidence"] = 0.5
        result = check_field_confidence(profile, min_confidence=0.3)
        assert result["passed"] is True

    def test_exact_threshold_passes(self):
        profile = _make_confirmed_profile()
        for field in PROFILE_REQUIRED_FIELDS:
            profile[field]["confidence"] = 0.7
        result = check_field_confidence(profile, min_confidence=0.7)
        assert result["passed"] is True


class TestCheckAiDepthAlignment:
    def test_no_features_passes(self):
        profile = _make_confirmed_profile()
        result = check_ai_depth_alignment(profile, features=[])
        assert result["passed"] is True

    def test_no_ai_with_ai_features_warns(self):
        profile = _make_confirmed_profile({"ai_depth": {"selected": "no_ai"}})
        features = [{"name": "AI Recommendations", "description": "ML-powered suggestions", "category": "AI"}]
        result = check_ai_depth_alignment(profile, features)
        assert result["passed"] is False
        assert len(result["warnings"]) == 1

    def test_light_automation_with_ai_features_warns(self):
        profile = _make_confirmed_profile({"ai_depth": {"selected": "light_automation"}})
        features = [{"name": "NLP Search", "description": "Natural language processing", "category": "Core"}]
        result = check_ai_depth_alignment(profile, features)
        assert result["passed"] is False

    def test_autonomous_ai_without_ai_features_warns(self):
        profile = _make_confirmed_profile({"ai_depth": {"selected": "autonomous_ai"}})
        features = [{"name": "Dashboard", "description": "Main overview page", "category": "Core"}]
        result = check_ai_depth_alignment(profile, features)
        assert result["passed"] is False

    def test_predictive_ml_with_ai_features_passes(self):
        profile = _make_confirmed_profile({"ai_depth": {"selected": "predictive_ml"}})
        features = [{"name": "ML Predictions", "description": "Predictive analytics", "category": "AI"}]
        result = check_ai_depth_alignment(profile, features)
        assert result["passed"] is True

    def test_ai_assisted_with_mixed_features_passes(self):
        profile = _make_confirmed_profile({"ai_depth": {"selected": "ai_assisted"}})
        features = [
            {"name": "Dashboard", "description": "Overview", "category": "Core"},
            {"name": "AI Suggestions", "description": "Intelligent tips", "category": "AI"},
        ]
        result = check_ai_depth_alignment(profile, features)
        assert result["passed"] is True

    def test_none_features_treated_as_empty(self):
        profile = _make_confirmed_profile()
        result = check_ai_depth_alignment(profile, features=None)
        assert result["passed"] is True


class TestCheckDeploymentAlignment:
    def test_saas_with_any_features_passes(self):
        profile = _make_confirmed_profile({"deployment_type": {"selected": "saas_multi"}})
        features = [{"name": "Cloud Storage", "description": "SaaS cloud storage"}]
        result = check_deployment_alignment(profile, features)
        assert result["passed"] is True

    def test_on_premise_with_cloud_feature_warns(self):
        profile = _make_confirmed_profile({"deployment_type": {"selected": "on_premise"}})
        features = [{"name": "Multi-tenant SaaS", "description": "Cloud-based multi-tenant hosting"}]
        result = check_deployment_alignment(profile, features)
        assert result["passed"] is False
        assert len(result["warnings"]) == 1

    def test_on_premise_with_normal_features_passes(self):
        profile = _make_confirmed_profile({"deployment_type": {"selected": "on_premise"}})
        features = [{"name": "Dashboard", "description": "Main overview page"}]
        result = check_deployment_alignment(profile, features)
        assert result["passed"] is True

    def test_no_features_passes(self):
        profile = _make_confirmed_profile({"deployment_type": {"selected": "on_premise"}})
        result = check_deployment_alignment(profile, features=[])
        assert result["passed"] is True


class TestCheckMonetizationAlignment:
    def test_freemium_without_billing_warns(self):
        profile = _make_confirmed_profile({"monetization_model": {"selected": "freemium"}})
        features = [{"name": "Dashboard", "description": "Main page"}]
        result = check_monetization_alignment(profile, features)
        assert result["passed"] is False

    def test_freemium_with_billing_passes(self):
        profile = _make_confirmed_profile({"monetization_model": {"selected": "freemium"}})
        features = [
            {"name": "Dashboard", "description": "Main page"},
            {"name": "Billing Portal", "description": "Manage subscription and payment"},
        ]
        result = check_monetization_alignment(profile, features)
        assert result["passed"] is True

    def test_open_core_without_billing_passes(self):
        profile = _make_confirmed_profile({"monetization_model": {"selected": "open_core"}})
        features = [{"name": "Dashboard", "description": "Main page"}]
        result = check_monetization_alignment(profile, features)
        assert result["passed"] is True

    def test_no_features_passes(self):
        profile = _make_confirmed_profile({"monetization_model": {"selected": "subscription"}})
        result = check_monetization_alignment(profile, features=[])
        assert result["passed"] is True


class TestCheckMvpScopeAlignment:
    def test_core_only_with_5_features_passes(self):
        profile = _make_confirmed_profile({"mvp_scope": {"selected": "core_only"}})
        features = [{"name": f"Feature {i}", "description": f"Desc {i}"} for i in range(5)]
        result = check_mvp_scope_alignment(profile, features)
        assert result["passed"] is True

    def test_core_only_with_too_many_features_warns(self):
        profile = _make_confirmed_profile({"mvp_scope": {"selected": "core_only"}})
        features = [{"name": f"Feature {i}", "description": f"Desc {i}"} for i in range(25)]
        result = check_mvp_scope_alignment(profile, features)
        assert result["passed"] is False
        assert "25 features" in result["warnings"][0]

    def test_proof_of_concept_with_too_many_warns(self):
        profile = _make_confirmed_profile({"mvp_scope": {"selected": "proof_of_concept"}})
        features = [{"name": f"Feature {i}", "description": f"Desc {i}"} for i in range(20)]
        result = check_mvp_scope_alignment(profile, features)
        assert result["passed"] is False

    def test_platform_foundation_with_15_passes(self):
        profile = _make_confirmed_profile({"mvp_scope": {"selected": "platform_foundation"}})
        features = [{"name": f"Feature {i}", "description": f"Desc {i}"} for i in range(15)]
        result = check_mvp_scope_alignment(profile, features)
        assert result["passed"] is True

    def test_unknown_scope_passes(self):
        profile = _make_confirmed_profile({"mvp_scope": {"selected": "custom_scope"}})
        features = [{"name": f"Feature {i}", "description": f"Desc {i}"} for i in range(50)]
        result = check_mvp_scope_alignment(profile, features)
        assert result["passed"] is True

    def test_no_features_passes(self):
        profile = _make_confirmed_profile({"mvp_scope": {"selected": "core_only"}})
        result = check_mvp_scope_alignment(profile, features=[])
        assert result["passed"] is True

    def test_too_few_features_warns(self):
        profile = _make_confirmed_profile({"mvp_scope": {"selected": "full_vertical"}})
        features = [{"name": "Only One", "description": "Single feature"}]
        result = check_mvp_scope_alignment(profile, features)
        assert result["passed"] is False
        assert "at least 8" in result["warnings"][0]


class TestCheckSuccessMetrics:
    def test_with_metrics_passes(self):
        profile = _make_confirmed_profile()
        result = check_success_metrics_exist(profile)
        assert result["passed"] is True
        assert "1 success metric" in result["details"]

    def test_without_metrics_fails(self):
        profile = _make_confirmed_profile({"success_metrics": []})
        result = check_success_metrics_exist(profile)
        assert result["passed"] is False

    def test_missing_key_fails(self):
        profile = _make_confirmed_profile()
        del profile["success_metrics"]
        result = check_success_metrics_exist(profile)
        assert result["passed"] is False

    def test_non_list_fails(self):
        profile = _make_confirmed_profile({"success_metrics": "not a list"})
        result = check_success_metrics_exist(profile)
        assert result["passed"] is False


class TestRunAllProfileChecks:
    def test_complete_profile_passes(self):
        profile = _make_confirmed_profile()
        result = run_all_profile_checks(profile)
        assert result["all_passed"] is True
        assert result["required_fields"]["passed"] is True

    def test_incomplete_profile_fails(self):
        profile = _make_confirmed_profile()
        profile["ai_depth"]["confirmed"] = False
        result = run_all_profile_checks(profile)
        assert result["all_passed"] is False
        assert result["required_fields"]["passed"] is False

    def test_with_features_runs_alignment_checks(self):
        profile = _make_confirmed_profile({"ai_depth": {"selected": "no_ai"}})
        features = [{"name": "AI Recs", "description": "ML recommendations", "category": "AI"}]
        result = run_all_profile_checks(profile, features)
        assert result["ai_depth_alignment"]["passed"] is False

    def test_all_checks_present(self):
        profile = _make_confirmed_profile()
        result = run_all_profile_checks(profile)
        expected_checks = [
            "required_fields", "field_confidence", "ai_depth_alignment",
            "deployment_alignment", "monetization_alignment",
            "mvp_scope_alignment", "success_metrics",
            "intelligence_goals_alignment", "all_passed",
        ]
        for check in expected_checks:
            assert check in result, f"Missing check: {check}"

    def test_intelligence_goals_alignment_included(self):
        profile = _make_confirmed_profile()
        result = run_all_profile_checks(profile)
        assert "intelligence_goals_alignment" in result

    def test_intelligence_goals_alignment_passes_no_goals(self):
        profile = _make_confirmed_profile()
        result = run_all_profile_checks(profile)
        assert result["intelligence_goals_alignment"]["passed"] is True

    def test_intelligence_goals_alignment_warns_no_ai_features(self):
        profile = _make_confirmed_profile({
            "intelligence_goals": [
                {"id": "g1", "label": "Predict churn", "type": "prediction"},
            ],
        })
        features = [{"name": "User Login", "description": "Auth system"}]
        result = run_all_profile_checks(profile, features)
        assert result["intelligence_goals_alignment"]["passed"] is False

    def test_intelligence_goals_alignment_passes_with_ai_features(self):
        profile = _make_confirmed_profile({
            "intelligence_goals": [
                {"id": "g1", "label": "Predict churn", "type": "prediction"},
            ],
        })
        features = [{"name": "AI Recommendation Engine", "description": "ML-based recs"}]
        result = run_all_profile_checks(profile, features)
        assert result["intelligence_goals_alignment"]["passed"] is True
