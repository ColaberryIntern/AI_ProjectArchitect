"""Tests for the skill scanner module."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from execution.skill_scanner import (
    MAX_SKILLS,
    _deduplicate_skills,
    _extract_tags,
    _load_existing_skills,
    _parse_awesome_list,
    _save_registry,
    run_full_scan,
)


class TestParseAwesomeList:
    """Test awesome-list markdown parsing."""

    def test_parses_standard_format(self):
        md = "- [Tool Name](https://example.com) - A great tool for things"
        skills = _parse_awesome_list(md, "test-source")
        assert len(skills) == 1
        assert skills[0]["name"] == "Tool Name"
        assert skills[0]["source_url"] == "https://example.com"
        assert "A great tool for things" in skills[0]["description"]

    def test_parses_bold_format(self):
        md = "- **[Bold Tool](https://example.com)** - Bold description"
        skills = _parse_awesome_list(md, "test-source")
        assert len(skills) == 1
        assert skills[0]["name"] == "Bold Tool"

    def test_detects_category_headings(self):
        md = """## Data Tools
- [DataTool](https://example.com) - Works with data

## AI Tools
- [AITool](https://example.com) - AI stuff
"""
        skills = _parse_awesome_list(md, "test-source", category_prefix="Test")
        assert len(skills) == 2
        assert "Data Tools" in skills[0]["category"]
        assert "AI Tools" in skills[1]["category"]

    def test_empty_markdown(self):
        skills = _parse_awesome_list("", "test-source")
        assert skills == []

    def test_no_matches_in_plain_text(self):
        skills = _parse_awesome_list("Just some text without links.", "test-source")
        assert skills == []

    def test_generates_stable_ids(self):
        md = "- [My Cool Tool](https://example.com) - Description"
        skills = _parse_awesome_list(md, "test-source")
        assert skills[0]["id"] == "my_cool_tool"

    def test_truncates_long_descriptions(self):
        long_desc = "A" * 500
        md = f"- [Tool](https://example.com) - {long_desc}"
        skills = _parse_awesome_list(md, "test-source")
        assert len(skills[0]["description"]) <= 300


class TestExtractTags:
    """Test tag extraction from text."""

    def test_extracts_known_keywords(self):
        tags = _extract_tags("MCP Server", "Database integration for file search")
        assert "mcp" in tags
        assert "database" in tags
        assert "file" in tags
        assert "search" in tags

    def test_limits_tag_count(self):
        tags = _extract_tags(
            "MCP API database file search AI agent LLM RAG",
            "vector embedding chat auth security monitor log deploy docker",
        )
        assert len(tags) <= 8

    def test_empty_text(self):
        tags = _extract_tags("", "")
        assert tags == []


class TestDeduplicateSkills:
    """Test skill deduplication."""

    def test_removes_duplicates(self):
        skills = [
            {"id": "a", "name": "A"},
            {"id": "b", "name": "B"},
            {"id": "a", "name": "A duplicate"},
        ]
        result = _deduplicate_skills(skills)
        assert len(result) == 2
        assert result[0]["name"] == "A"  # First occurrence kept

    def test_preserves_order(self):
        skills = [{"id": "c"}, {"id": "a"}, {"id": "b"}]
        result = _deduplicate_skills(skills)
        assert [s["id"] for s in result] == ["c", "a", "b"]

    def test_empty_list(self):
        assert _deduplicate_skills([]) == []

    def test_skips_empty_ids(self):
        skills = [{"id": ""}, {"id": "valid"}]
        result = _deduplicate_skills(skills)
        assert len(result) == 1
        assert result[0]["id"] == "valid"


class TestSaveRegistry:
    """Test atomic registry saving."""

    def test_saves_valid_json(self, tmp_path, monkeypatch):
        monkeypatch.setattr("execution.skill_scanner.REGISTRY_PATH", tmp_path / "registry.json")
        skills = [{"id": "test", "name": "Test", "description": "d", "category": "c"}]
        _save_registry(skills, "success", 3)

        data = json.loads((tmp_path / "registry.json").read_text())
        assert data["version"] == 1
        assert data["last_scan_status"] == "success"
        assert data["sources_scanned"] == 3
        assert len(data["skills"]) == 1

    def test_caps_at_max_skills(self, tmp_path, monkeypatch):
        monkeypatch.setattr("execution.skill_scanner.REGISTRY_PATH", tmp_path / "registry.json")
        skills = [{"id": f"s{i}", "name": f"S{i}"} for i in range(MAX_SKILLS + 100)]
        _save_registry(skills, "success", 1)

        data = json.loads((tmp_path / "registry.json").read_text())
        assert len(data["skills"]) == MAX_SKILLS


class TestLoadExistingSkills:
    """Test loading existing skills from registry."""

    def test_returns_empty_on_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("execution.skill_scanner.REGISTRY_PATH", tmp_path / "missing.json")
        assert _load_existing_skills() == []

    def test_loads_valid_registry(self, tmp_path, monkeypatch):
        reg_path = tmp_path / "registry.json"
        reg_path.write_text(json.dumps({"skills": [{"id": "a"}]}))
        monkeypatch.setattr("execution.skill_scanner.REGISTRY_PATH", reg_path)
        skills = _load_existing_skills()
        assert len(skills) == 1
        assert skills[0]["id"] == "a"


class TestRunFullScan:
    """Test the full scan orchestrator."""

    @pytest.mark.asyncio
    async def test_handles_all_scanners_failing_gracefully(self, tmp_path, monkeypatch):
        """When all HTTP calls fail, scanners catch errors and return empty.

        Individual scanners handle their own exceptions, so run_full_scan
        still considers them 'scanned' (0 results). Existing skills are preserved.
        """
        monkeypatch.setattr("execution.skill_scanner.REGISTRY_PATH", tmp_path / "registry.json")
        # Seed an existing registry
        (tmp_path / "registry.json").write_text(
            json.dumps({"skills": [{"id": "seed", "name": "Seed"}]})
        )

        # Mock httpx to always fail
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("Network error"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("execution.skill_scanner.httpx.AsyncClient", return_value=mock_client):
            result = await run_full_scan()

        # Scanners catch their own errors, so orchestrator sees success with 0 new skills
        assert result["skills_found"] == 0
        # Existing seed skills should be preserved
        data = json.loads((tmp_path / "registry.json").read_text())
        assert len(data["skills"]) >= 1
        assert data["skills"][0]["id"] == "seed"

    @pytest.mark.asyncio
    async def test_returns_summary_dict(self, tmp_path, monkeypatch):
        """Result should have expected keys."""
        monkeypatch.setattr("execution.skill_scanner.REGISTRY_PATH", tmp_path / "registry.json")
        (tmp_path / "registry.json").write_text(json.dumps({"skills": []}))

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("fail"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("execution.skill_scanner.httpx.AsyncClient", return_value=mock_client):
            result = await run_full_scan()

        assert "status" in result
        assert "sources_scanned" in result
        assert "skills_found" in result
        assert "total_skills" in result
        assert "timestamp" in result
