"""Unit tests for execution/feature_classifier.py."""

import pytest

from execution.feature_classifier import (
    check_feature_problem_mapping,
    check_intern_explainability,
    check_mutual_exclusions,
    classify_feature,
    flag_deferred,
    order_by_priority,
)


class TestClassifyFeature:
    def test_core_feature(self):
        result = classify_feature(
            "Guided Questioning",
            "Ask structured questions",
            is_blocking=True,
            has_problem_mapping=True,
        )
        assert result == "core"

    def test_optional_not_blocking(self):
        result = classify_feature(
            "PDF Export",
            "Export as PDF",
            is_blocking=False,
            has_problem_mapping=True,
        )
        assert result == "optional"

    def test_optional_no_mapping(self):
        result = classify_feature(
            "Dark Mode",
            "Dark UI theme",
            is_blocking=True,
            has_problem_mapping=False,
        )
        assert result == "optional"

    def test_optional_neither(self):
        result = classify_feature(
            "Analytics Dashboard",
            "Usage stats",
            is_blocking=False,
            has_problem_mapping=False,
        )
        assert result == "optional"


class TestCheckFeatureProblemMapping:
    def test_all_mapped(self):
        features = [
            {"name": "Questioning", "problem_mapped_to": "idea_vagueness"},
            {"name": "Quality Gates", "problem_mapped_to": "quality_control"},
        ]
        result = check_feature_problem_mapping(
            features, ["idea_vagueness", "quality_control"]
        )
        assert result["passed"] is True
        assert result["unmapped"] == []

    def test_unmapped_feature(self):
        features = [
            {"name": "Questioning", "problem_mapped_to": "idea_vagueness"},
            {"name": "Dark Mode", "problem_mapped_to": ""},
        ]
        result = check_feature_problem_mapping(features, ["idea_vagueness"])
        assert result["passed"] is False
        assert "Dark Mode" in result["unmapped"]

    def test_wrong_problem_reference(self):
        features = [
            {"name": "Feature A", "problem_mapped_to": "nonexistent_problem"},
        ]
        result = check_feature_problem_mapping(features, ["real_problem"])
        assert result["passed"] is False


class TestCheckInternExplainability:
    def test_clear_rationale(self):
        features = [
            {
                "name": "Questioning",
                "rationale": "Eliminates idea vagueness through systematic inquiry",
            },
        ]
        result = check_intern_explainability(features)
        assert result["passed"] is True

    def test_short_rationale(self):
        features = [
            {"name": "Feature X", "rationale": "Good"},
        ]
        result = check_intern_explainability(features)
        assert result["passed"] is False
        assert "Feature X" in result["unclear"]

    def test_missing_rationale(self):
        features = [
            {"name": "Feature Y", "rationale": ""},
        ]
        result = check_intern_explainability(features)
        assert result["passed"] is False


class TestOrderByPriority:
    def test_sorts_by_build_order(self):
        features = [
            {"name": "C", "build_order": 3},
            {"name": "A", "build_order": 1},
            {"name": "B", "build_order": 2},
        ]
        result = order_by_priority(features)
        assert result[0]["name"] == "A"
        assert result[1]["name"] == "B"
        assert result[2]["name"] == "C"

    def test_missing_order_goes_last(self):
        features = [
            {"name": "No Order"},
            {"name": "First", "build_order": 1},
        ]
        result = order_by_priority(features)
        assert result[0]["name"] == "First"
        assert result[1]["name"] == "No Order"


class TestFlagDeferred:
    def test_deferred_separated(self):
        features = [
            {"name": "Active", "deferred": False},
            {"name": "Deferred", "deferred": True},
        ]
        result = flag_deferred(features)
        assert result["deferred_count"] == 1
        assert result["active_count"] == 1
        assert result["deferred"][0]["name"] == "Deferred"
        assert result["active"][0]["name"] == "Active"

    def test_no_deferred(self):
        features = [
            {"name": "A", "deferred": False},
            {"name": "B", "deferred": False},
        ]
        result = flag_deferred(features)
        assert result["deferred_count"] == 0
        assert result["active_count"] == 2

    def test_missing_deferred_field(self):
        features = [{"name": "A"}]
        result = flag_deferred(features)
        assert result["active_count"] == 1


class TestCheckMutualExclusions:
    """Test mutual exclusion constraint checking."""

    def test_no_conflict_passes(self):
        groups = [
            {"group": "arch", "feature_ids": ["microservices", "monolith"], "label": "Architecture"},
        ]
        result = check_mutual_exclusions(["microservices", "dashboard"], groups)
        assert result["passed"] is True
        assert result["violations"] == []

    def test_conflict_detected(self):
        groups = [
            {"group": "arch", "feature_ids": ["microservices", "monolith"], "label": "Architecture"},
        ]
        result = check_mutual_exclusions(["microservices", "monolith"], groups)
        assert result["passed"] is False
        assert len(result["violations"]) == 1
        assert result["violations"][0]["group"] == "arch"
        assert set(result["violations"][0]["conflicting_ids"]) == {"microservices", "monolith"}

    def test_empty_selection_passes(self):
        groups = [
            {"group": "arch", "feature_ids": ["microservices", "monolith"], "label": "Architecture"},
        ]
        result = check_mutual_exclusions([], groups)
        assert result["passed"] is True

    def test_single_from_group_passes(self):
        groups = [
            {"group": "arch", "feature_ids": ["microservices", "monolith"], "label": "Architecture"},
        ]
        result = check_mutual_exclusions(["monolith"], groups)
        assert result["passed"] is True

    def test_multiple_groups_checked(self):
        groups = [
            {"group": "arch", "feature_ids": ["microservices", "monolith"], "label": "Architecture"},
            {"group": "deploy", "feature_ids": ["blue_green", "canary"], "label": "Deployment"},
        ]
        result = check_mutual_exclusions(
            ["microservices", "monolith", "blue_green", "canary"], groups
        )
        assert result["passed"] is False
        assert len(result["violations"]) == 2
