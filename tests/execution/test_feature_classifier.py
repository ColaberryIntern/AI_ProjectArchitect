"""Unit tests for execution/feature_classifier.py."""

import pytest

from execution.feature_classifier import (
    check_acceptance_criteria_present,
    check_feature_problem_mapping,
    check_intern_explainability,
    check_mutual_exclusions,
    classify_feature,
    detect_dependency_cycles,
    find_dangling_dependencies,
    flag_deferred,
    order_by_priority,
    promote_to_requirement,
    promote_features_to_requirements,
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


# ---------------------------------------------------------------------------
# Requirement promotion + dependency-graph tests
# ---------------------------------------------------------------------------


class TestPromoteToRequirement:
    def test_seeds_defaults_for_core(self):
        feature = {
            "id": "REQ-001",
            "name": "Login",
            "description": "x",
            "rationale": "x",
            "type": "core",
            "problem_mapped_to": "auth",
            "build_order": 1,
        }
        result = promote_to_requirement(feature)
        assert result["requirement_type"] == "functional"
        assert result["priority"] == "must"
        assert result["dependencies"] == []
        assert result["acceptance_criteria"] == []
        assert result["nfr"] == []
        assert result["traces_to"]["problem_id"] == "auth"
        assert result["traces_to"]["chapter_ids"] == []

    def test_seeds_defaults_for_optional(self):
        feature = {
            "id": "REQ-002",
            "name": "Dark Mode",
            "description": "x",
            "rationale": "x",
            "type": "optional",
        }
        result = promote_to_requirement(feature)
        assert result["priority"] == "should"

    def test_preserves_existing_fields(self):
        feature = {
            "id": "REQ-003",
            "name": "Search",
            "description": "x",
            "rationale": "x",
            "type": "core",
            "priority": "could",
            "actor": "broker",
            "acceptance_criteria": [{"id": "AC-003-1", "given": "g", "when": "w", "then": "t"}],
        }
        result = promote_to_requirement(feature)
        assert result["priority"] == "could"  # not overwritten
        assert result["actor"] == "broker"
        assert len(result["acceptance_criteria"]) == 1

    def test_idempotent(self):
        feature = {"id": "x", "name": "n", "description": "d", "rationale": "r", "type": "core"}
        once = promote_to_requirement(feature)
        twice = promote_to_requirement(once)
        assert once == twice

    def test_vectorized(self):
        features = [
            {"id": "A", "name": "n", "description": "d", "rationale": "r", "type": "core"},
            {"id": "B", "name": "n", "description": "d", "rationale": "r", "type": "optional"},
        ]
        result = promote_features_to_requirements(features)
        assert len(result) == 2
        assert result[0]["priority"] == "must"
        assert result[1]["priority"] == "should"


class TestDetectDependencyCycles:
    def test_acyclic(self):
        features = [
            {"id": "A", "dependencies": ["B"]},
            {"id": "B", "dependencies": ["C"]},
            {"id": "C", "dependencies": []},
        ]
        assert detect_dependency_cycles(features) == []

    def test_self_loop(self):
        features = [{"id": "A", "dependencies": ["A"]}]
        cycles = detect_dependency_cycles(features)
        assert len(cycles) == 1
        assert "A" in cycles[0]

    def test_two_node_cycle(self):
        features = [
            {"id": "A", "dependencies": ["B"]},
            {"id": "B", "dependencies": ["A"]},
        ]
        cycles = detect_dependency_cycles(features)
        assert len(cycles) >= 1
        flat = {n for cyc in cycles for n in cyc}
        assert flat == {"A", "B"}

    def test_dangling_does_not_count_as_cycle(self):
        features = [{"id": "A", "dependencies": ["X"]}]
        assert detect_dependency_cycles(features) == []

    def test_empty_input(self):
        assert detect_dependency_cycles([]) == []


class TestFindDanglingDependencies:
    def test_all_resolve(self):
        features = [{"id": "A", "dependencies": ["B"]}, {"id": "B"}]
        assert find_dangling_dependencies(features) == []

    def test_dangling_reported(self):
        features = [{"id": "A", "dependencies": ["B", "X", "Y"]}, {"id": "B"}]
        result = find_dangling_dependencies(features)
        assert result == [{"feature_id": "A", "missing": ["X", "Y"]}]

    def test_no_deps(self):
        features = [{"id": "A"}, {"id": "B"}]
        assert find_dangling_dependencies(features) == []


class TestCheckAcceptanceCriteriaPresent:
    def test_must_with_ac_passes(self):
        features = [
            {"id": "A", "priority": "must", "acceptance_criteria": [{"id": "AC-A-1"}]},
        ]
        result = check_acceptance_criteria_present(features)
        assert result["passed"] is True

    def test_must_without_ac_fails(self):
        features = [{"id": "A", "priority": "must"}]
        result = check_acceptance_criteria_present(features)
        assert result["passed"] is False
        assert "A" in result["missing_ac"]

    def test_should_without_ac_passes(self):
        features = [{"id": "A", "priority": "should"}]
        result = check_acceptance_criteria_present(features)
        assert result["passed"] is True

    def test_must_with_empty_ac_list_fails(self):
        features = [{"id": "A", "priority": "must", "acceptance_criteria": []}]
        result = check_acceptance_criteria_present(features)
        assert result["passed"] is False
