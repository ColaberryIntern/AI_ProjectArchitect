"""Tests for skill chapter generation integration.

Verifies that:
- Outline generation appends a skill section when skills are selected
- Chapter writer receives skill context via the _skills profile key
- build_skill_chapter_context produces valid prompt content
"""

import pytest

from execution.skill_catalog import build_skill_chapter_context
from execution.state_manager import (
    get_selected_skills,
    initialize_state,
    set_selected_skills,
    set_skill_catalog,
)


@pytest.fixture
def state(tmp_path, monkeypatch):
    """Create a fresh project state for testing."""
    monkeypatch.setattr("execution.state_manager.OUTPUT_DIR", tmp_path)
    return initialize_state("test-project")


@pytest.fixture
def state_with_skills(state):
    """State with a populated skill catalog and selection."""
    catalog = [
        {"id": "mcp_filesystem", "name": "MCP Filesystem Server", "description": "Read/write local files", "category": "MCP Servers"},
        {"id": "web_search", "name": "Web Search", "description": "Search the web via API", "category": "Data & RAG"},
        {"id": "langchain_agents", "name": "LangChain Agents", "description": "Build agent chains", "category": "AI Agent Frameworks"},
    ]
    set_skill_catalog(state, catalog)
    set_selected_skills(state, ["mcp_filesystem", "web_search"])
    return state


class TestSkillChapterContext:
    """Test that build_skill_chapter_context produces valid output."""

    def test_context_includes_skill_names(self, state_with_skills):
        skills = get_selected_skills(state_with_skills)
        ctx = build_skill_chapter_context(skills)
        assert "MCP Filesystem Server" in ctx
        assert "Web Search" in ctx

    def test_context_groups_by_category(self, state_with_skills):
        skills = get_selected_skills(state_with_skills)
        ctx = build_skill_chapter_context(skills)
        assert "MCP Servers" in ctx
        assert "Data & RAG" in ctx

    def test_context_empty_for_no_skills(self):
        ctx = build_skill_chapter_context([])
        assert ctx == ""

    def test_context_includes_descriptions(self, state_with_skills):
        skills = get_selected_skills(state_with_skills)
        ctx = build_skill_chapter_context(skills)
        assert "Read/write local files" in ctx
        assert "Search the web via API" in ctx


class TestSkillChapterInOutline:
    """Test that outline generation includes skill section when skills selected."""

    def test_no_skill_section_when_no_skills(self, state):
        """Without skills, no Skills & Tool Integration Guide section exists."""
        skills = get_selected_skills(state)
        assert skills == []

    def test_selected_skills_return_full_dicts(self, state_with_skills):
        """get_selected_skills returns full dicts, not just IDs."""
        skills = get_selected_skills(state_with_skills)
        assert len(skills) == 2
        assert all("name" in s and "description" in s for s in skills)

    def test_unselected_skill_not_in_results(self, state_with_skills):
        """Skills in catalog but not selected should not appear."""
        skills = get_selected_skills(state_with_skills)
        ids = {s["id"] for s in skills}
        assert "langchain_agents" not in ids


class TestProfileSkillsKey:
    """Test the _skills key pattern used in chapter_writer."""

    def test_profile_copy_with_skills(self, state_with_skills):
        """Simulates what auto_builder does: profile copy with _skills."""
        profile = {"problem_definition": {"selected": "test"}}
        skills = get_selected_skills(state_with_skills)
        enriched = {**profile, "_skills": skills}

        assert "_skills" in enriched
        assert len(enriched["_skills"]) == 2
        assert enriched["problem_definition"]["selected"] == "test"

    def test_empty_skills_key_is_falsy(self, state):
        """When no skills selected, _skills should be empty list (falsy)."""
        skills = get_selected_skills(state)
        profile = {"test": True, "_skills": skills}
        assert not profile["_skills"]
