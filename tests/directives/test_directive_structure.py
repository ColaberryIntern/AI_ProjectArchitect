"""Validate that all directive files have required sections and proper structure."""

import re
from pathlib import Path

import pytest

from config.settings import PROJECT_ROOT

DIRECTIVES_DIR = PROJECT_ROOT / "directives"
EXECUTION_DIR = PROJECT_ROOT / "execution"

# Required sections for each directive
REQUIRED_SECTIONS = ["Purpose", "Verification"]
# At least one of these must be present
CONTENT_SECTIONS = ["Steps", "Inputs"]


def get_directive_files():
    """Return all .md files in the directives directory."""
    return sorted(DIRECTIVES_DIR.glob("*.md"))


def read_directive(path: Path) -> str:
    """Read a directive file and return its content."""
    return path.read_text(encoding="utf-8")


def extract_headings(content: str) -> list[str]:
    """Extract all ## headings from Markdown content."""
    return re.findall(r"^##\s+(.+)$", content, re.MULTILINE)


class TestDirectiveFilesExist:
    def test_directives_directory_exists(self):
        assert DIRECTIVES_DIR.exists(), "directives/ directory must exist"

    def test_at_least_one_directive(self):
        files = get_directive_files()
        assert len(files) > 0, "At least one directive file must exist"

    def test_expected_directives_present(self):
        files = {f.name for f in get_directive_files()}
        expected = {
            "01-idea-intake.md",
            "03-feature-discovery.md",
            "04-outline-generation.md",
            "05-outline-approval.md",
            "06-chapter-build.md",
            "07-quality-gates.md",
            "08-final-assembly.md",
            "manual-workflow.md",
            "state-management.md",
        }
        missing = expected - files
        assert not missing, f"Missing directive files: {missing}"


class TestDirectiveRequiredSections:
    @pytest.fixture(params=[f.name for f in get_directive_files()])
    def directive(self, request):
        path = DIRECTIVES_DIR / request.param
        return {
            "name": request.param,
            "content": read_directive(path),
            "headings": extract_headings(read_directive(path)),
        }

    def test_has_purpose_section(self, directive):
        assert any(
            "Purpose" in h for h in directive["headings"]
        ), f"{directive['name']} must have a Purpose section"

    def test_has_verification_section(self, directive):
        assert any(
            "Verification" in h for h in directive["headings"]
        ), f"{directive['name']} must have a Verification section"

    def test_has_content_section(self, directive):
        has_steps = any("Steps" in h or "Step" in h for h in directive["headings"])
        has_inputs = any("Inputs" in h for h in directive["headings"])
        has_pipeline = any("Pipeline" in h for h in directive["headings"])
        has_what = any("What" in h for h in directive["headings"])
        assert (
            has_steps or has_inputs or has_pipeline or has_what
        ), f"{directive['name']} must have Steps, Inputs, Pipeline, or content sections"


class TestDirectiveMarkdownIntegrity:
    @pytest.fixture(params=[f.name for f in get_directive_files()])
    def directive_content(self, request):
        path = DIRECTIVES_DIR / request.param
        return {"name": request.param, "content": read_directive(path)}

    def test_starts_with_heading(self, directive_content):
        first_line = directive_content["content"].strip().split("\n")[0]
        assert first_line.startswith(
            "#"
        ), f"{directive_content['name']} must start with a heading"

    def test_no_empty_headings(self, directive_content):
        empty_headings = re.findall(
            r"^#{1,6}\s*$", directive_content["content"], re.MULTILINE
        )
        assert (
            not empty_headings
        ), f"{directive_content['name']} has empty headings"

    def test_non_trivial_content(self, directive_content):
        lines = [
            l.strip()
            for l in directive_content["content"].split("\n")
            if l.strip() and not l.strip().startswith("#")
        ]
        assert (
            len(lines) >= 5
        ), f"{directive_content['name']} has insufficient content ({len(lines)} lines)"


class TestDirectiveReferences:
    def test_referenced_scripts_exist(self):
        """Verify that execution scripts referenced in directives actually exist."""
        all_content = ""
        for f in get_directive_files():
            all_content += read_directive(f) + "\n"

        # Find references to execution/ scripts
        script_refs = re.findall(
            r"execution/(\w+\.py)", all_content
        )
        unique_scripts = set(script_refs)

        for script in unique_scripts:
            script_path = EXECUTION_DIR / script
            assert script_path.exists(), (
                f"Directive references execution/{script} but file does not exist. "
                f"Expected at: {script_path}"
            )
