"""Unit tests for execution/quality_gate_runner.py."""

import pytest

from execution.quality_gate_runner import (
    check_anti_vagueness,
    check_build_readiness,
    check_clarity,
    check_completeness,
    check_intern_test,
    generate_quality_report,
    run_chapter_gates,
    run_final_gates,
)


GOOD_CHAPTER = """# System Purpose & Context

## Purpose

This chapter exists to define why the project exists and what problem it solves.
The system is designed to transform raw ideas into execution-ready documentation.

## Design Intent

This approach was chosen because structured questioning reduces ambiguity.
The tradeoff is speed vs completeness — we prioritize completeness.
The constraint is that interns must be able to follow the output on their own.

## Implementation Guidance

First, create the project state file using the state_manager.
Then, capture the user's idea verbatim — do not rephrase.
Next, confirm the idea was captured as intended before proceeding.
After confirmation, advance the phase to feature discovery.
The input is a raw text idea from the user.
The output is a state file with the idea recorded.
This step depends on the state_manager being initialized.
The execution order is: initialize, capture, confirm, advance.
Step 1 is initialization. Step 2 is capture.
"""

BAD_CHAPTER = """# Short Chapter

Some text here.
"""


class TestCheckCompleteness:
    def test_complete_chapter_passes(self):
        result = check_completeness(GOOD_CHAPTER)
        assert result["passed"] is True

    def test_missing_purpose(self):
        text = "# Chapter\n## Design Intent\nSome intent.\n## Implementation Guidance\nSome guidance.\n" + "\n".join(["Line"] * 10)
        result = check_completeness(text)
        assert result["passed"] is False
        assert any("purpose" in i.lower() for i in result["issues"])

    def test_placeholder_detected(self):
        text = GOOD_CHAPTER + "\nThis section is TBD."
        result = check_completeness(text)
        assert result["passed"] is False
        assert any("placeholder" in i.lower() for i in result["issues"])

    def test_too_short(self):
        result = check_completeness(BAD_CHAPTER)
        assert result["passed"] is False
        assert any("content lines" in i for i in result["issues"])


class TestCheckClarity:
    def test_clear_chapter(self):
        result = check_clarity(GOOD_CHAPTER)
        assert result["passed"] is True

    def test_no_outcome_statement(self):
        text = "# Chapter\n\n## Section A\nSome text.\n## Section B\nMore text."
        result = check_clarity(text)
        assert result["passed"] is False

    def test_insufficient_structure(self):
        text = "# Only One Heading\nLots of text without subheadings."
        result = check_clarity(text)
        assert result["passed"] is False
        assert any("heading" in i.lower() for i in result["issues"])


class TestCheckBuildReadiness:
    def test_ready_chapter(self):
        result = check_build_readiness(GOOD_CHAPTER)
        assert result["passed"] is True

    def test_missing_execution_order(self):
        text = "The input is a file. The output is a report. This depends on the config."
        result = check_build_readiness(text)
        assert result["passed"] is False
        assert any("execution order" in i for i in result["issues"])

    def test_missing_dependencies(self):
        text = "First do step 1. Then step 2. The input is data. The output is a file."
        result = check_build_readiness(text)
        assert result["passed"] is False
        assert any("dependencies" in i for i in result["issues"])


class TestCheckAntiVagueness:
    def test_clean_text(self):
        result = check_anti_vagueness(
            "Validate that the state file contains all required fields."
        )
        assert result["passed"] is True
        assert result["flagged_phrases"] == []

    def test_handle_edge_cases(self):
        result = check_anti_vagueness("Handle edge cases carefully.")
        assert result["passed"] is False
        flagged_lower = [p.lower() for p in result["flagged_phrases"]]
        assert "handle edge cases" in flagged_lower

    def test_optimize_later(self):
        result = check_anti_vagueness("We can optimize later.")
        assert result["passed"] is False

    def test_make_it_scalable(self):
        result = check_anti_vagueness("Make it scalable for future needs.")
        assert result["passed"] is False

    def test_ensure_good_ux(self):
        result = check_anti_vagueness("Ensure good UX throughout.")
        assert result["passed"] is False

    def test_use_best_practices(self):
        result = check_anti_vagueness("Use best practices for security.")
        assert result["passed"] is False


class TestCheckInternTest:
    def test_document_passes(self):
        text = (
            "This system exists to transform project ideas into documentation. "
            "The purpose is clear project planning. "
            "Start with phase 1: idea intake. First, capture the raw idea. "
            "Success criteria: all quality gates pass. "
            "The definition of done is a complete build guide document."
        )
        result = check_intern_test(text)
        assert result["passed"] is True

    def test_missing_what_building(self):
        text = (
            "Start with phase 1. First do this. "
            "Definition of done is a complete document."
        )
        result = check_intern_test(text)
        if not result["questions_answered"]["what_building"]:
            assert result["passed"] is False

    def test_missing_done_criteria(self):
        text = (
            "This system handles project planning. "
            "The purpose is documentation. "
            "Start with step 1 first."
        )
        result = check_intern_test(text)
        if not result["questions_answered"]["what_done_looks_like"]:
            assert "what done looks like" in " ".join(result["issues"]).lower()


class TestRunChapterGates:
    def test_good_chapter_passes(self):
        result = run_chapter_gates(GOOD_CHAPTER)
        assert result["all_passed"] is True

    def test_bad_chapter_fails(self):
        result = run_chapter_gates(BAD_CHAPTER)
        assert result["all_passed"] is False


class TestRunFinalGates:
    def test_good_document_passes(self):
        doc = (
            GOOD_CHAPTER
            + "\nThis system exists to create build-ready documentation. "
            + "The purpose is structured project planning. "
            + "Start with phase 1 first. "
            + "Success criteria: all quality gates pass and intern test passes. "
            + "The definition of done is a versioned build guide."
        )
        result = run_final_gates(doc)
        assert result["all_passed"] is True


class TestBuildReadinessWithTemplate:
    """Tests that the enterprise template preamble passes the build readiness gate."""

    def test_build_readiness_passes_with_enterprise_template_preamble(self):
        """The enterprise template preamble alone must pass the build readiness gate."""
        from execution.template_renderer import render_chapter_enterprise

        rendered = render_chapter_enterprise(1, "Problem & Market Context", "Some content.")
        result = check_build_readiness(rendered)
        assert result["passed"] is True, (
            f"Build readiness gate should pass on enterprise template preamble, "
            f"but got issues: {result['issues']}"
        )

    def test_build_readiness_passes_on_conceptual_chapter(self):
        """Even a conceptual chapter (no implementation detail) should pass via preamble."""
        from execution.template_renderer import render_chapter_enterprise

        conceptual_content = (
            "## Market Overview\n\n"
            "The market for AI-powered project management tools is growing.\n"
            "Key competitors include Jira, Asana, and Monday.com.\n\n"
            "## Target Segment\n\n"
            "Small to medium businesses with 10-200 employees.\n"
        )
        rendered = render_chapter_enterprise(2, "Market Analysis", conceptual_content)
        result = check_build_readiness(rendered)
        assert result["passed"] is True, (
            f"Conceptual chapter should pass build readiness via template preamble, "
            f"but got issues: {result['issues']}"
        )


class TestGenerateQualityReport:
    def test_report_format(self):
        results = run_chapter_gates(GOOD_CHAPTER)
        report = generate_quality_report(results)
        assert "Quality Gate Report" in report
        assert "PASS" in report

    def test_failing_report(self):
        results = run_chapter_gates(BAD_CHAPTER)
        report = generate_quality_report(results)
        assert "FAIL" in report
