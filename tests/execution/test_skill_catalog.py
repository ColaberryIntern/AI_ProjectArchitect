"""Tests for the skill catalog and suggestion engine."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from execution.skill_catalog import (
    FALLBACK_SKILLS,
    SKILL_CATEGORIES,
    build_skill_chapter_context,
    get_skills_by_category,
    get_skills_by_ids,
    load_registry,
    suggest_skills,
    _match_skills_by_tags,
)


class TestFallbackSkills:
    """Validate the hardcoded fallback skill list."""

    def test_has_at_least_40_skills(self):
        assert len(FALLBACK_SKILLS) >= 200

    def test_all_skills_have_required_fields(self):
        for skill in FALLBACK_SKILLS:
            assert "id" in skill, f"Skill missing 'id': {skill}"
            assert "name" in skill, f"Skill missing 'name': {skill}"
            assert "description" in skill, f"Skill missing 'description': {skill}"
            assert "category" in skill, f"Skill missing 'category': {skill}"

    def test_all_ids_unique(self):
        ids = [s["id"] for s in FALLBACK_SKILLS]
        assert len(ids) == len(set(ids))

    def test_has_multiple_categories(self):
        categories = {s["category"] for s in FALLBACK_SKILLS}
        assert len(categories) >= 6

    def test_all_categories_in_known_list(self):
        for skill in FALLBACK_SKILLS:
            assert skill["category"] in SKILL_CATEGORIES, (
                f"Unknown category '{skill['category']}' for skill '{skill['id']}'"
            )

    def test_skills_have_tags(self):
        for skill in FALLBACK_SKILLS:
            assert "tags" in skill, f"Skill missing 'tags': {skill['id']}"
            assert len(skill["tags"]) >= 1


class TestLoadRegistry:
    """Test the registry loader with fallback behavior."""

    def test_fallback_when_file_missing(self, tmp_path):
        with patch("execution.skill_catalog.REGISTRY_PATH", tmp_path / "nonexistent.json"):
            result = load_registry()
        assert len(result) >= 40
        assert result[0]["id"] == FALLBACK_SKILLS[0]["id"]

    def test_loads_valid_registry(self, tmp_path):
        registry_file = tmp_path / "registry.json"
        skills = [{"id": f"skill_{i}", "name": f"Skill {i}", "description": "desc", "category": "MCP Servers", "tags": ["test"]} for i in range(20)]
        registry_file.write_text(json.dumps({"version": 1, "skills": skills}))
        with patch("execution.skill_catalog.REGISTRY_PATH", registry_file):
            result = load_registry()
        assert len(result) == 20
        assert result[0]["id"] == "skill_0"

    def test_fallback_when_too_few_skills(self, tmp_path):
        registry_file = tmp_path / "registry.json"
        registry_file.write_text(json.dumps({"version": 1, "skills": [{"id": "a"}]}))
        with patch("execution.skill_catalog.REGISTRY_PATH", registry_file):
            result = load_registry()
        assert len(result) >= 40  # falls back

    def test_fallback_on_corrupt_json(self, tmp_path):
        registry_file = tmp_path / "registry.json"
        registry_file.write_text("{not valid json")
        with patch("execution.skill_catalog.REGISTRY_PATH", registry_file):
            result = load_registry()
        assert len(result) >= 40


class TestGetSkillsByCategory:
    """Test category grouping."""

    def test_groups_correctly(self):
        skills = [
            {"id": "a", "name": "A", "description": "d", "category": "MCP Servers"},
            {"id": "b", "name": "B", "description": "d", "category": "Data & RAG"},
            {"id": "c", "name": "C", "description": "d", "category": "MCP Servers"},
        ]
        result = get_skills_by_category(skills)
        assert len(result) == 2
        assert result[0]["name"] == "MCP Servers"
        assert len(result[0]["skills"]) == 2
        assert result[1]["name"] == "Data & RAG"
        assert len(result[1]["skills"]) == 1

    def test_preserves_category_order(self):
        skills = [
            {"id": "a", "category": "B"},
            {"id": "b", "category": "A"},
            {"id": "c", "category": "B"},
        ]
        result = get_skills_by_category(skills)
        assert [r["name"] for r in result] == ["B", "A"]

    def test_defaults_to_custom_skills(self):
        skills = [{"id": "a"}]
        result = get_skills_by_category(skills)
        assert result[0]["name"] == "Custom Skills"


class TestGetSkillsByIds:
    """Test ID-based filtering."""

    def test_selects_matching_ids(self):
        skills = FALLBACK_SKILLS
        result = get_skills_by_ids(skills, ["web_search", "mcp_github"])
        assert len(result) == 2
        names = {s["name"] for s in result}
        assert "Web Search" in names
        assert "MCP GitHub Server" in names

    def test_returns_empty_for_no_matches(self):
        result = get_skills_by_ids(FALLBACK_SKILLS, ["nonexistent_skill"])
        assert result == []

    def test_preserves_original_order(self):
        result = get_skills_by_ids(FALLBACK_SKILLS, ["mcp_github", "web_search"])
        # web_search comes before mcp_github in FALLBACK_SKILLS
        assert result[0]["id"] == "web_search"
        assert result[1]["id"] == "mcp_github"


class TestMatchSkillsByTags:
    """Test deterministic tag-based matching."""

    def test_returns_dict_with_suggested_and_available(self):
        profile = {"problem_definition": {"selected": "database management tool"}}
        features = [{"name": "SQL Query", "description": "run sql queries"}]
        result = _match_skills_by_tags(profile, features, FALLBACK_SKILLS)
        assert "suggested" in result
        assert "available" in result
        assert len(result["suggested"]) == 15
        assert isinstance(result["suggested"], list)

    def test_custom_max_and_default(self):
        profile = {"problem_definition": {"selected": "web app"}}
        features = []
        result = _match_skills_by_tags(
            profile, features, FALLBACK_SKILLS, max_results=10, default_selected=5,
        )
        assert len(result["suggested"]) == 5
        assert len(result["suggested"]) + len(result["available"]) == 10

    def test_database_keywords_rank_sql_higher(self):
        profile = {"problem_definition": {"selected": "database query management"}}
        features = [{"name": "SQL Interface", "description": "query databases"}]
        result = _match_skills_by_tags(profile, features, FALLBACK_SKILLS)
        # SQL-related skills should be near the top
        assert "sql_query_tool" in result["suggested"]


class TestSuggestSkills:
    """Test the full suggestion function with LLM mocking."""

    def test_falls_back_to_tags_when_llm_unavailable(self):
        with patch("execution.skill_catalog.is_available", return_value=False):
            profile = {"problem_definition": {"selected": "test app"}}
            result = suggest_skills(profile, [], FALLBACK_SKILLS)
        assert len(result["suggested"]) == 15

    def test_falls_back_when_no_profile_data(self):
        with patch("execution.skill_catalog.is_available", return_value=True):
            result = suggest_skills({}, [], FALLBACK_SKILLS)
        assert len(result["suggested"]) == 15

    def test_calls_llm_when_available(self):
        mock_response = type("R", (), {"content": json.dumps({
            "suggested": ["web_search", "rag_pipeline", "sql_query_tool", "code_interpreter", "mcp_github",
                         "claude_tool_use", "langchain_agents", "email_sender", "test_generator",
                         "git_operations", "api_connector", "error_tracker", "oauth_provider",
                         "workflow_automation", "prometheus_monitoring"],
            "available": ["mcp_slack", "mcp_postgres"],
        })})()
        with patch("execution.skill_catalog.is_available", return_value=True), \
             patch("execution.skill_catalog.chat", return_value=mock_response):
            profile = {"problem_definition": {"selected": "web app"}}
            result = suggest_skills(profile, [], FALLBACK_SKILLS)
        assert "web_search" in result["suggested"]
        assert len(result["suggested"]) == 15


class TestBuildSkillChapterContext:
    """Test the chapter context builder."""

    def test_empty_skills_returns_empty_string(self):
        assert build_skill_chapter_context([]) == ""

    def test_formats_skills_by_category(self):
        skills = [
            {"id": "a", "name": "Skill A", "description": "Does A", "category": "MCP Servers"},
            {"id": "b", "name": "Skill B", "description": "Does B", "category": "Data & RAG"},
        ]
        result = build_skill_chapter_context(skills)
        assert "### MCP Servers" in result
        assert "### Data & RAG" in result
        assert "**Skill A**" in result
        assert "Does A" in result

    def test_multiple_skills_per_category(self):
        skills = [
            {"id": "a", "name": "A", "description": "d", "category": "MCP Servers"},
            {"id": "b", "name": "B", "description": "d", "category": "MCP Servers"},
        ]
        result = build_skill_chapter_context(skills)
        assert result.count("**") == 4  # 2 skills × 2 asterisks each
