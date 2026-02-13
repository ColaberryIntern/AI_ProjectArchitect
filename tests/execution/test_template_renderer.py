"""Unit tests for execution/template_renderer.py."""

import pytest

from execution.template_renderer import (
    load_template,
    render_chapter,
    render_chapter_enterprise,
    render_final_document,
    render_list_sections,
    render_outline,
    render_quality_report,
    render_template,
)


class TestRenderTemplate:
    def test_simple_substitution(self):
        result = render_template("Hello {{name}}!", {"name": "World"})
        assert result == "Hello World!"

    def test_multiple_placeholders(self):
        result = render_template(
            "{{a}} and {{b}}", {"a": "Alpha", "b": "Beta"}
        )
        assert result == "Alpha and Beta"

    def test_integer_value(self):
        result = render_template("Version {{version}}", {"version": 1})
        assert result == "Version 1"

    def test_missing_key_left_as_is(self):
        result = render_template("{{exists}} {{missing}}", {"exists": "here"})
        assert result == "here {{missing}}"

    def test_empty_context(self):
        result = render_template("No {{changes}}", {})
        assert result == "No {{changes}}"


class TestRenderListSections:
    def test_list_rendering(self):
        template = "Before\n{{#items}}Item: {{name}}\n{{/items}}After"
        items = [{"name": "A"}, {"name": "B"}]
        result = render_list_sections(template, "items", items)
        assert "Item: A" in result
        assert "Item: B" in result
        assert "Before" in result
        assert "After" in result

    def test_no_matching_key(self):
        template = "No list here"
        result = render_list_sections(template, "items", [])
        assert result == "No list here"

    def test_empty_list(self):
        template = "{{#items}}Item: {{name}}\n{{/items}}"
        result = render_list_sections(template, "items", [])
        assert "Item:" not in result


class TestLoadTemplate:
    def test_load_existing_template(self):
        content = load_template("outline_template.md")
        assert "{{project_name}}" in content

    def test_load_missing_template(self):
        with pytest.raises(FileNotFoundError):
            load_template("nonexistent_template.md")


class TestRenderOutline:
    def test_renders_outline(self, sample_outline_sections):
        result = render_outline("Test Project", 1, sample_outline_sections)
        assert "Test Project" in result
        assert "v1" in result
        assert "System Purpose" in result

    def test_includes_all_sections(self, sample_outline_sections):
        result = render_outline("My Project", 1, sample_outline_sections)
        for section in sample_outline_sections:
            assert section["title"] in result


class TestRenderChapter:
    def test_renders_chapter(self):
        result = render_chapter(
            index=1,
            title="System Purpose",
            purpose="This chapter defines the system purpose.",
            design_intent="We chose this approach for clarity.",
            implementation_guidance="Start by creating the config file.",
        )
        assert "Chapter 1" in result
        assert "System Purpose" in result
        assert "system purpose" in result.lower()
        assert "clarity" in result.lower()
        assert "config file" in result.lower()


class TestRenderFinalDocument:
    def test_renders_final(self):
        result = render_final_document(
            project_name="Test Project",
            version="v1",
            date="2025-01-01",
            status="Final",
            chapters_content="Chapter content here.",
        )
        assert "Test Project" in result
        assert "v1" in result
        assert "2025-01-01" in result
        assert "Chapter content here." in result


class TestRenderQualityReport:
    def test_renders_report(self):
        result = render_quality_report(
            project_name="Test Project",
            date="2025-01-01",
            overall_result="PASS",
            gate_results="All gates passed.",
        )
        assert "Test Project" in result
        assert "PASS" in result
        assert "All gates passed." in result


class TestRenderChapterEnterprise:
    def test_renders_title_and_content(self):
        content = "## Vision & Strategy\n\nDetailed vision content here."
        result = render_chapter_enterprise(1, "Executive Summary", content)
        assert "# Chapter 1: Executive Summary" in result
        assert "## Vision & Strategy" in result
        assert "Detailed vision content here." in result

    def test_preserves_markdown_formatting(self):
        content = "## Sub1\n\n- item1\n- item2\n\n```python\nprint('hello')\n```"
        result = render_chapter_enterprise(3, "Architecture", content)
        assert "```python" in result
        assert "- item1" in result

    def test_handles_empty_content(self):
        result = render_chapter_enterprise(1, "Empty Chapter", "")
        assert "# Chapter 1: Empty Chapter" in result
