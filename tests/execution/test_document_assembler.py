"""Unit tests for execution/document_assembler.py."""

import pytest
from pathlib import Path

from execution.document_assembler import (
    add_version_header,
    apply_formatting,
    assemble_full_document,
    compile_document,
    export_markdown,
    generate_filename,
)


@pytest.fixture
def chapter_files(tmp_path):
    """Create temporary chapter files for testing."""
    ch1 = tmp_path / "ch1.md"
    ch1.write_text("# Chapter 1: Purpose\n\nThis is the purpose chapter.\n", encoding="utf-8")

    ch2 = tmp_path / "ch2.md"
    ch2.write_text("# Chapter 2: Users\n\nThis is the users chapter.\n", encoding="utf-8")

    ch3 = tmp_path / "ch3.md"
    ch3.write_text("# Chapter 3: Features\n\nThis is the features chapter.\n", encoding="utf-8")

    return {
        "paths": [str(ch1), str(ch2), str(ch3)],
        "titles": ["Purpose", "Users", "Features"],
    }


class TestCompileDocument:
    def test_compiles_in_order(self, chapter_files):
        result = compile_document(
            chapter_files["paths"], chapter_files["titles"]
        )
        assert "Chapter 1" in result
        assert "Chapter 2" in result
        assert "Chapter 3" in result
        # Verify order
        assert result.index("Chapter 1") < result.index("Chapter 2")
        assert result.index("Chapter 2") < result.index("Chapter 3")

    def test_adds_separators(self, chapter_files):
        result = compile_document(
            chapter_files["paths"], chapter_files["titles"]
        )
        assert "---" in result

    def test_missing_file_raises(self, chapter_files):
        bad_paths = chapter_files["paths"] + ["/nonexistent/ch4.md"]
        with pytest.raises(FileNotFoundError):
            compile_document(bad_paths, chapter_files["titles"] + ["Missing"])


class TestApplyFormatting:
    def test_removes_extra_blank_lines(self):
        doc = "Line 1\n\n\n\n\nLine 2"
        result = apply_formatting(doc)
        assert "\n\n\n\n" not in result
        assert "Line 1" in result
        assert "Line 2" in result

    def test_removes_trailing_whitespace(self):
        doc = "Line 1   \nLine 2  "
        result = apply_formatting(doc)
        assert "   " not in result.split("\n")[0]

    def test_ends_with_newline(self):
        doc = "Content"
        result = apply_formatting(doc)
        assert result.endswith("\n")


class TestGenerateFilename:
    def test_basic_name(self):
        result = generate_filename("Test Project", "v1")
        assert result == "Test_Project_Build_Guide_v1.md"

    def test_special_characters(self):
        result = generate_filename("My Cool Project!", "v2")
        assert result == "My_Cool_Project_Build_Guide_v2.md"

    def test_spaces(self):
        result = generate_filename("AI Project Architect", "v1")
        assert result == "AI_Project_Architect_Build_Guide_v1.md"


class TestAddVersionHeader:
    def test_adds_header(self):
        result = add_version_header("Content here.", "My Project", "v1", "2025-01-01")
        assert "My Project" in result
        assert "v1" in result
        assert "2025-01-01" in result
        assert "Content here." in result

    def test_default_date(self):
        result = add_version_header("Content.", "My Project", "v1")
        assert "My Project" in result
        # Should have a date (today's date)
        assert "**Date:**" in result


class TestExportMarkdown:
    def test_writes_file(self, tmp_output_dir):
        output_path = export_markdown(
            "# Test Document\nContent.", "test-project", "test.md"
        )
        assert Path(output_path).exists()
        content = Path(output_path).read_text(encoding="utf-8")
        assert "Test Document" in content

    def test_creates_directory(self, tmp_output_dir):
        output_path = export_markdown(
            "Content.", "new-project", "doc.md"
        )
        assert Path(output_path).exists()


class TestAssembleFullDocument:
    def test_full_assembly(self, tmp_output_dir, chapter_files):
        result = assemble_full_document(
            chapter_paths=chapter_files["paths"],
            chapter_titles=chapter_files["titles"],
            project_name="Test Project",
            project_slug="test-project",
            version="v1",
        )
        assert result["filename"] == "Test_Project_Build_Guide_v1.md"
        assert Path(result["output_path"]).exists()
        assert "Chapter 1" in result["content"]
        assert "Test Project" in result["content"]
