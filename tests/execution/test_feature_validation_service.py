"""Unit tests for execution/feature_validation_service.py."""

from execution.feature_validation_service import (
    check_feature_dependencies,
    check_mcp_mapping,
    check_skill_coverage,
    derive_mcp_servers,
    run_all_feature_validation,
)


# ---------------------------------------------------------------------------
# check_feature_dependencies
# ---------------------------------------------------------------------------


class TestFeatureDependencies:
    def test_all_deps_satisfied(self):
        ids = ["user_registration", "role_management", "dashboard"]
        result = check_feature_dependencies(ids)
        assert result["passed"] is True
        assert result["issues"] == []

    def test_missing_single_dependency(self):
        ids = ["role_management"]  # missing user_registration
        result = check_feature_dependencies(ids)
        assert result["passed"] is False
        assert len(result["issues"]) == 1
        assert result["issues"][0]["feature"] == "role_management"
        assert "user_registration" in result["issues"][0]["missing"]

    def test_no_dependencies_needed(self):
        ids = ["responsive_design", "accessibility", "dark_mode"]
        result = check_feature_dependencies(ids)
        assert result["passed"] is True
        assert result["issues"] == []

    def test_multiple_missing_deps(self):
        # gamification requires user_registration AND progress_tracking
        ids = ["gamification"]
        result = check_feature_dependencies(ids)
        assert result["passed"] is False
        issue = result["issues"][0]
        assert "user_registration" in issue["missing"]
        assert "progress_tracking" in issue["missing"]

    def test_partial_deps_met(self):
        # gamification needs user_registration + progress_tracking
        ids = ["user_registration", "gamification"]
        result = check_feature_dependencies(ids)
        assert result["passed"] is False
        issue = result["issues"][0]
        assert "progress_tracking" in issue["missing"]
        assert "user_registration" not in issue["missing"]

    def test_empty_features(self):
        result = check_feature_dependencies([])
        assert result["passed"] is True


# ---------------------------------------------------------------------------
# check_skill_coverage
# ---------------------------------------------------------------------------


class TestSkillCoverage:
    def test_ai_features_with_ai_skills(self):
        features = [{"category": "AI & Intelligence"}]
        skills = [{"category": "AI Agent Frameworks"}]
        result = check_skill_coverage(features, skills)
        assert result["passed"] is True
        assert result["gaps"] == []

    def test_ai_features_without_ai_skills(self):
        features = [{"category": "AI & Intelligence"}]
        skills = [{"category": "Frontend & UI"}]
        result = check_skill_coverage(features, skills)
        assert result["passed"] is False
        assert len(result["gaps"]) == 1
        assert result["gaps"][0]["feature_category"] == "AI & Intelligence"

    def test_no_coverage_requirements(self):
        features = [{"category": "Core Functionality"}]
        skills = [{"category": "Code & Development"}]
        result = check_skill_coverage(features, skills)
        assert result["passed"] is True

    def test_multiple_gaps(self):
        features = [
            {"category": "AI & Intelligence"},
            {"category": "Security & Compliance"},
        ]
        skills = [{"category": "Frontend & UI"}]
        result = check_skill_coverage(features, skills)
        assert result["passed"] is False
        assert len(result["gaps"]) == 2

    def test_empty_features(self):
        result = check_skill_coverage([], [])
        assert result["passed"] is True


# ---------------------------------------------------------------------------
# check_mcp_mapping
# ---------------------------------------------------------------------------


class TestMCPMapping:
    def test_complete_coverage(self):
        feature_ids = ["notifications"]
        skills = [{"id": "mcp_slack", "category": "MCP Servers"}]
        result = check_mcp_mapping(feature_ids, skills)
        assert result["passed"] is True
        assert result["gaps"] == []

    def test_missing_mcp(self):
        feature_ids = ["notifications"]
        skills = [{"id": "some_other_skill", "category": "Other"}]
        result = check_mcp_mapping(feature_ids, skills)
        assert result["passed"] is False
        assert len(result["gaps"]) == 1
        assert result["gaps"][0]["feature_id"] == "notifications"
        assert "mcp_slack" in result["gaps"][0]["needed_mcp"]

    def test_no_mcp_needed(self):
        feature_ids = ["responsive_design", "dark_mode"]
        skills = []
        result = check_mcp_mapping(feature_ids, skills)
        assert result["passed"] is True

    def test_partial_mcp_coverage(self):
        # container_orchestration needs mcp_docker AND mcp_kubernetes
        feature_ids = ["container_orchestration"]
        skills = [{"id": "mcp_docker", "category": "MCP Servers"}]
        result = check_mcp_mapping(feature_ids, skills)
        assert result["passed"] is False
        gap = result["gaps"][0]
        assert "mcp_kubernetes" in gap["needed_mcp"]
        assert "mcp_docker" not in gap["needed_mcp"]


# ---------------------------------------------------------------------------
# derive_mcp_servers
# ---------------------------------------------------------------------------


class TestDeriveMCPServers:
    def test_filters_mcp_only(self):
        skills = [
            {"id": "mcp_slack", "name": "Slack MCP", "description": "Slack",
             "category": "MCP Servers", "source_url": "", "tags": []},
            {"id": "langchain", "name": "LangChain", "description": "Agent",
             "category": "AI Agent Frameworks", "source_url": "", "tags": []},
        ]
        result = derive_mcp_servers(skills, ["notifications"])
        assert len(result) == 1
        assert result[0]["id"] == "mcp_slack"

    def test_adds_purpose_from_features(self):
        skills = [
            {"id": "mcp_github", "name": "GitHub MCP", "description": "GH",
             "category": "MCP Servers", "source_url": "", "tags": []},
        ]
        result = derive_mcp_servers(skills, ["ci_cd_pipeline", "api_access"])
        assert len(result) == 1
        assert "ci_cd_pipeline" in result[0]["purpose"]
        assert "api_access" in result[0]["purpose"]

    def test_general_purpose_when_no_feature_mapping(self):
        skills = [
            {"id": "mcp_memory", "name": "Memory MCP", "description": "Mem",
             "category": "MCP Servers", "source_url": "", "tags": []},
        ]
        result = derive_mcp_servers(skills, ["dashboard"])
        assert len(result) == 1
        assert result[0]["purpose"] == "General MCP capability"

    def test_empty_skills(self):
        result = derive_mcp_servers([], ["notifications"])
        assert result == []


# ---------------------------------------------------------------------------
# run_all_feature_validation (confidence scoring)
# ---------------------------------------------------------------------------


class TestConfidenceScoring:
    def test_perfect_confidence(self):
        features = [
            {"id": "user_registration", "category": "Core Functionality"},
            {"id": "role_management", "category": "Core Functionality"},
        ]
        ids = ["user_registration", "role_management"]
        skills = [{"id": "any", "category": "Code & Development"}]
        result = run_all_feature_validation(features, ids, skills)
        assert result["is_valid"] is True
        assert result["confidence"] == 1.0
        assert result["issues"] == []

    def test_zero_dep_confidence_on_failure(self):
        features = [
            {"id": "role_management", "category": "Core Functionality"},
        ]
        ids = ["role_management"]  # missing user_registration
        skills = [{"id": "any", "category": "Code & Development"}]
        result = run_all_feature_validation(features, ids, skills)
        assert result["is_valid"] is False
        assert result["confidence"] < 1.0
        assert len(result["issues"]) > 0

    def test_warnings_dont_block_validity(self):
        features = [
            {"id": "user_registration", "category": "AI & Intelligence"},
        ]
        ids = ["user_registration"]
        skills = [{"id": "some_skill", "category": "Frontend & UI"}]
        result = run_all_feature_validation(features, ids, skills)
        # Dependencies are met (user_registration has no deps)
        assert result["is_valid"] is True
        # But skill coverage gap produces a warning
        assert len(result["warnings"]) > 0
        assert result["confidence"] < 1.0

    def test_no_features_full_confidence(self):
        result = run_all_feature_validation([], [], [])
        assert result["is_valid"] is True
        assert result["confidence"] == 1.0
