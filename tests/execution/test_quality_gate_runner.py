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


# ---------------------------------------------------------------------------
# Spec-driven gates (Phase B)
# ---------------------------------------------------------------------------


from execution import semantic_judge
from execution.quality_gate_runner import (
    check_ac_testability,
    check_chapter_intern_semantic,
    check_requirement_coverage,
    run_spec_gates,
)


@pytest.fixture(autouse=True)
def _clear_judge_cache():
    semantic_judge.clear_cache()
    yield
    semantic_judge.clear_cache()


class TestCheckRequirementCoverage:
    def test_all_must_traced_passes(self):
        reqs = [
            {"id": "REQ-001", "priority": "must", "traces_to": {"chapter_ids": ["1"]}},
            {"id": "REQ-002", "priority": "should", "traces_to": {"chapter_ids": []}},
        ]
        result = check_requirement_coverage(reqs)
        assert result["passed"] is True
        assert result["orphaned"] == []

    def test_orphaned_must_fails(self):
        reqs = [
            {"id": "REQ-001", "priority": "must", "traces_to": {"chapter_ids": []}},
            {"id": "REQ-002", "priority": "must", "traces_to": {"chapter_ids": ["1"]}},
        ]
        result = check_requirement_coverage(reqs)
        assert result["passed"] is False
        assert result["orphaned"] == ["REQ-001"]

    def test_should_priority_not_required(self):
        reqs = [
            {"id": "REQ-001", "priority": "should", "traces_to": {"chapter_ids": []}},
        ]
        result = check_requirement_coverage(reqs)
        assert result["passed"] is True


class TestCheckAcTestability:
    def test_no_must_acs_passes(self):
        reqs = [{"id": "R1", "priority": "should", "acceptance_criteria": []}]
        result = check_ac_testability(reqs)
        assert result["passed"] is True
        assert result["status"] == "ok"

    def test_skipped_when_llm_unavailable(self, monkeypatch):
        monkeypatch.setattr(semantic_judge.llm_client, "is_available", lambda: False)
        reqs = [{
            "id": "R1",
            "priority": "must",
            "acceptance_criteria": [{"id": "AC-1", "given": "g", "when": "w", "then": "t"}],
        }]
        result = check_ac_testability(reqs)
        assert result["passed"] is True
        assert result["status"] == "skipped"

    def test_failing_score_fails_gate(self, monkeypatch):
        from execution.llm_client import LLMResponse
        import json as _json

        def fake_chat(**kwargs):
            return LLMResponse(
                content=_json.dumps({
                    "results": [
                        {"id": "AC-1", "score": 1, "reason": "vague then"},
                        {"id": "AC-2", "score": 3, "reason": "fully testable"},
                    ]
                }),
                model="gpt-4o-mini",
                usage={"prompt_tokens": 1, "completion_tokens": 1},
                stop_reason="stop",
            )

        monkeypatch.setattr(semantic_judge.llm_client, "is_available", lambda: True)
        monkeypatch.setattr(semantic_judge.llm_client, "chat", fake_chat)

        reqs = [{
            "id": "R1",
            "priority": "must",
            "acceptance_criteria": [
                {"id": "AC-1", "given": "g", "when": "w", "then": "t"},
                {"id": "AC-2", "given": "g", "when": "w", "then": "t"},
            ],
        }]
        result = check_ac_testability(reqs)
        assert result["passed"] is False
        assert len(result["failing"]) == 1
        assert result["failing"][0]["ac_id"] == "AC-1"
        assert result["failing"][0]["requirement_id"] == "R1"


class TestCheckChapterInternSemantic:
    def test_passes_when_all_answered(self, monkeypatch):
        from execution.llm_client import LLMResponse
        import json as _json

        def fake_chat(**kwargs):
            return LLMResponse(
                content=_json.dumps({
                    "inputs": {"answered": True, "evidence": "x"},
                    "outputs": {"answered": True, "evidence": "y"},
                    "test_scenario": {"answered": True, "evidence": "z"},
                    "definition_of_done": {"answered": True, "evidence": "w"},
                }),
                model="gpt-4o-mini",
                usage={"prompt_tokens": 1, "completion_tokens": 1},
                stop_reason="stop",
            )

        monkeypatch.setattr(semantic_judge.llm_client, "is_available", lambda: True)
        monkeypatch.setattr(semantic_judge.llm_client, "chat", fake_chat)

        result = check_chapter_intern_semantic("chapter", [])
        assert result["passed"] is True
        assert result["answers"]["inputs"]["answered"] is True

    def test_fails_when_any_unanswered(self, monkeypatch):
        from execution.llm_client import LLMResponse
        import json as _json

        def fake_chat(**kwargs):
            return LLMResponse(
                content=_json.dumps({
                    "inputs": {"answered": True, "evidence": "x"},
                    "outputs": {"answered": False, "evidence": None},
                    "test_scenario": {"answered": True, "evidence": "z"},
                    "definition_of_done": {"answered": True, "evidence": "w"},
                }),
                model="gpt-4o-mini",
                usage={"prompt_tokens": 1, "completion_tokens": 1},
                stop_reason="stop",
            )

        monkeypatch.setattr(semantic_judge.llm_client, "is_available", lambda: True)
        monkeypatch.setattr(semantic_judge.llm_client, "chat", fake_chat)

        result = check_chapter_intern_semantic("chapter", [])
        assert result["passed"] is False
        assert any("outputs" in i for i in result["issues"])


class TestRunSpecGates:
    def test_all_pass_no_chapters(self, monkeypatch):
        monkeypatch.setattr(semantic_judge.llm_client, "is_available", lambda: False)
        reqs = [
            {"id": "R1", "priority": "must", "traces_to": {"chapter_ids": ["c1"]}},
        ]
        result = run_spec_gates(reqs, chapters=None)
        assert result["all_passed"] is True
        assert result["requirement_coverage"]["passed"] is True
        assert result["chapter_intern_semantic"]["per_chapter"] == []

    def test_failing_coverage_drops_all_passed(self, monkeypatch):
        monkeypatch.setattr(semantic_judge.llm_client, "is_available", lambda: False)
        reqs = [{"id": "R1", "priority": "must", "traces_to": {"chapter_ids": []}}]
        result = run_spec_gates(reqs)
        assert result["all_passed"] is False


# ---------------------------------------------------------------------------
# Citation-presence (Phase C.3)
# ---------------------------------------------------------------------------


from execution.quality_gate_runner import check_requirement_citations


class TestCheckRequirementCitations:
    def test_no_linked_requirements_passes(self):
        result = check_requirement_citations("any text", linked_requirements=None)
        assert result["passed"] is True

    def test_empty_list_passes(self):
        result = check_requirement_citations("any text", [])
        assert result["passed"] is True

    def test_missing_citation_fails(self):
        text = "This chapter has no citations."
        reqs = [{"id": "REQ-001"}, {"id": "REQ-002"}]
        result = check_requirement_citations(text, reqs)
        assert result["passed"] is False
        assert sorted(result["missing_citations"]) == ["REQ-001", "REQ-002"]

    def test_partial_citation_reports_remaining(self):
        text = "Implementation cites [REQ-001] and [REQ-002] but not the third."
        reqs = [{"id": "REQ-001"}, {"id": "REQ-002"}, {"id": "REQ-003"}]
        result = check_requirement_citations(text, reqs)
        assert result["passed"] is False
        assert result["missing_citations"] == ["REQ-003"]

    def test_all_cited_passes(self):
        text = "Cites [REQ-001] and [REQ-002]."
        reqs = [{"id": "REQ-001"}, {"id": "REQ-002"}]
        result = check_requirement_citations(text, reqs)
        assert result["passed"] is True


class TestCheckCompletenessWithLinkedRequirements:
    def test_completeness_fails_when_citation_missing(self):
        # Use the existing GOOD_CHAPTER fixture which passes all other checks.
        result = check_completeness(
            GOOD_CHAPTER, linked_requirements=[{"id": "REQ-001"}]
        )
        assert result["passed"] is False
        assert any("REQ-001" in i for i in result["issues"])

    def test_completeness_passes_with_no_linked_requirements(self):
        # Backward-compat: when no linked_requirements, citation check is skipped.
        result = check_completeness(GOOD_CHAPTER)
        assert result["passed"] is True

    def test_completeness_passes_when_citation_present(self):
        chapter = GOOD_CHAPTER + "\n\nThis section ties back to [REQ-001].\n"
        result = check_completeness(chapter, linked_requirements=[{"id": "REQ-001"}])
        assert result["passed"] is True
