"""Tests for skill-related state management functions."""

import pytest

from execution.state_manager import (
    _ensure_skills,
    add_custom_skill,
    approve_skills,
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


class TestEnsureSkills:
    """Test backward compatibility for old states without skills key."""

    def test_adds_skills_key_to_old_state(self):
        old_state = {"project": {"name": "old"}, "features": {}}
        skills = _ensure_skills(old_state)
        assert "skills" in old_state
        assert skills["catalog"] == []
        assert skills["selected"] == []
        assert skills["custom"] == []
        assert skills["approved"] is False

    def test_returns_existing_skills(self, state):
        skills = _ensure_skills(state)
        assert isinstance(skills, dict)
        # Calling again returns same reference
        assert _ensure_skills(state) is skills


class TestSetSkillCatalog:
    """Test catalog snapshot storage."""

    def test_stores_catalog(self, state):
        catalog = [
            {"id": "web_search", "name": "Web Search", "description": "d", "category": "Data"},
            {"id": "mcp_github", "name": "GitHub", "description": "d", "category": "MCP"},
        ]
        set_skill_catalog(state, catalog)
        assert len(state["skills"]["catalog"]) == 2
        assert state["skills"]["catalog"][0]["id"] == "web_search"

    def test_sets_suggested_at(self, state):
        assert state["skills"]["suggested_at"] is None
        set_skill_catalog(state, [])
        assert state["skills"]["suggested_at"] is not None


class TestSetSelectedSkills:
    """Test skill selection storage."""

    def test_stores_ids(self, state):
        set_selected_skills(state, ["web_search", "mcp_github"])
        assert state["skills"]["selected"] == ["web_search", "mcp_github"]

    def test_replaces_previous_selections(self, state):
        set_selected_skills(state, ["a", "b"])
        set_selected_skills(state, ["c"])
        assert state["skills"]["selected"] == ["c"]

    def test_handles_empty_list(self, state):
        set_selected_skills(state, [])
        assert state["skills"]["selected"] == []


class TestAddCustomSkill:
    """Test custom skill addition."""

    def test_adds_new_skill(self, state):
        add_custom_skill(state, "my_skill", "My Skill", "Does something cool")
        assert len(state["skills"]["custom"]) == 1
        assert state["skills"]["custom"][0]["id"] == "my_skill"
        assert state["skills"]["custom"][0]["name"] == "My Skill"
        assert state["skills"]["custom"][0]["category"] == "Custom Skills"

    def test_deduplicates_by_id(self, state):
        add_custom_skill(state, "my_skill", "My Skill", "Version 1")
        add_custom_skill(state, "my_skill", "My Skill V2", "Version 2")
        assert len(state["skills"]["custom"]) == 1
        # Keeps first version
        assert state["skills"]["custom"][0]["description"] == "Version 1"

    def test_allows_multiple_different_skills(self, state):
        add_custom_skill(state, "skill_a", "Skill A", "Does A")
        add_custom_skill(state, "skill_b", "Skill B", "Does B")
        assert len(state["skills"]["custom"]) == 2


class TestGetSelectedSkills:
    """Test full skill dict retrieval for selected IDs."""

    def test_returns_matching_skills_from_catalog(self, state):
        catalog = [
            {"id": "web_search", "name": "Web Search", "description": "d", "category": "Data"},
            {"id": "mcp_github", "name": "GitHub", "description": "d", "category": "MCP"},
            {"id": "rag_pipeline", "name": "RAG", "description": "d", "category": "Data"},
        ]
        set_skill_catalog(state, catalog)
        set_selected_skills(state, ["web_search", "rag_pipeline"])
        result = get_selected_skills(state)
        assert len(result) == 2
        ids = {s["id"] for s in result}
        assert ids == {"web_search", "rag_pipeline"}

    def test_includes_custom_skills(self, state):
        set_skill_catalog(state, [
            {"id": "web_search", "name": "Web Search", "description": "d", "category": "Data"},
        ])
        add_custom_skill(state, "my_tool", "My Tool", "Custom tool")
        set_selected_skills(state, ["web_search", "my_tool"])
        result = get_selected_skills(state)
        assert len(result) == 2
        ids = {s["id"] for s in result}
        assert "my_tool" in ids

    def test_returns_empty_when_no_selection(self, state):
        assert get_selected_skills(state) == []


class TestApproveSkills:
    """Test skill approval."""

    def test_sets_approved_flag(self, state):
        assert state["skills"]["approved"] is False
        approve_skills(state)
        assert state["skills"]["approved"] is True


class TestInitializeStateHasSkills:
    """Verify new states include the skills key."""

    def test_new_state_has_skills(self, state):
        assert "skills" in state
        assert state["skills"]["catalog"] == []
        assert state["skills"]["selected"] == []
        assert state["skills"]["custom"] == []
        assert state["skills"]["approved"] is False
        assert state["skills"]["suggested_at"] is None
