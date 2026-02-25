"""Unit tests for execution/ambiguity_detector.py."""

import pytest

from execution.ambiguity_detector import (
    detect_forbidden_phrases,
    detect_missing_criteria,
    detect_overloaded_goals,
    detect_undefined_users,
    detect_vague_nouns,
    run_all_detectors,
)


class TestDetectVagueNouns:
    def test_catches_platform(self):
        findings = detect_vague_nouns("We need a platform for this.")
        assert len(findings) > 0
        assert any(f["term"].lower() == "platform" for f in findings)

    def test_catches_tool(self):
        findings = detect_vague_nouns("Build a tool for teams.")
        assert len(findings) > 0

    def test_catches_solution(self):
        findings = detect_vague_nouns("This solution handles everything.")
        assert len(findings) > 0

    def test_passes_specific_terms(self):
        findings = detect_vague_nouns(
            "The REST API accepts JSON requests and returns status codes."
        )
        assert len(findings) == 0

    def test_provides_suggestion(self):
        findings = detect_vague_nouns("We need a framework.")
        assert findings[0]["suggestion"] is not None


class TestDetectUndefinedUsers:
    def test_catches_businesses(self):
        findings = detect_undefined_users("This is for businesses.")
        assert len(findings) > 0

    def test_catches_people(self):
        findings = detect_undefined_users("People will use this daily.")
        assert len(findings) > 0

    def test_catches_teams(self):
        findings = detect_undefined_users("Teams need better collaboration.")
        assert len(findings) > 0

    def test_passes_specific_users(self):
        findings = detect_undefined_users(
            "Junior developers at Colaberry will execute the build plan."
        )
        assert len(findings) == 0


class TestDetectOverloadedGoals:
    def test_catches_end_to_end(self):
        findings = detect_overloaded_goals("Build an end-to-end solution.")
        assert len(findings) > 0

    def test_catches_do_everything(self):
        findings = detect_overloaded_goals("This should do everything.")
        assert len(findings) > 0

    def test_catches_comprehensive(self):
        findings = detect_overloaded_goals("Build a comprehensive system.")
        assert len(findings) > 0

    def test_passes_bounded_goals(self):
        findings = detect_overloaded_goals(
            "Accept a project name and create a state file with initial fields."
        )
        assert len(findings) == 0


class TestDetectForbiddenPhrases:
    def test_catches_handle_edge_cases(self):
        findings = detect_forbidden_phrases("Handle edge cases appropriately.")
        assert len(findings) >= 1
        phrases = [f["phrase"].lower() for f in findings]
        assert "handle edge cases" in phrases

    def test_catches_optimize_later(self):
        findings = detect_forbidden_phrases("We can optimize later.")
        assert any("optimize later" in f["phrase"].lower() for f in findings)

    def test_catches_make_it_scalable(self):
        findings = detect_forbidden_phrases("Make it scalable.")
        assert any("make it scalable" in f["phrase"].lower() for f in findings)

    def test_catches_ensure_good_ux(self):
        findings = detect_forbidden_phrases("Ensure good UX.")
        assert any("ensure good ux" in f["phrase"].lower() for f in findings)

    def test_catches_use_best_practices(self):
        findings = detect_forbidden_phrases("Use best practices.")
        assert any("use best practices" in f["phrase"].lower() for f in findings)

    def test_passes_specific_text(self):
        findings = detect_forbidden_phrases(
            "Validate that the outline has exactly 7 required sections in the correct order."
        )
        assert len(findings) == 0

    def test_allows_various_in_specific_context(self):
        findings = detect_forbidden_phrases(
            "The system supports various authentication providers: OAuth2, SAML, and LDAP."
        )
        assert len(findings) == 0

    def test_allows_efficiently_in_specific_context(self):
        findings = detect_forbidden_phrases(
            "Configure the database to efficiently process batch queries using connection pooling."
        )
        assert len(findings) == 0

    def test_allows_appropriate_in_specific_context(self):
        findings = detect_forbidden_phrases(
            "Return the appropriate HTTP status code: 201 for created, 400 for bad input."
        )
        assert len(findings) == 0


class TestDetectMissingCriteria:
    def test_criteria_present(self):
        result = detect_missing_criteria("Success is when all tests pass.")
        assert result["has_criteria"] is True

    def test_criteria_missing(self):
        result = detect_missing_criteria("Build a great product.")
        assert result["has_criteria"] is False
        assert result["suggestion"] is not None

    def test_criteria_with_measurable(self):
        result = detect_missing_criteria("The measurable outcome is 100% test coverage.")
        assert result["has_criteria"] is True


class TestRunAllDetectors:
    def test_clean_text(self):
        result = run_all_detectors(
            "Junior developers will implement the REST API endpoint. "
            "The endpoint accepts a project name and returns a JSON state object. "
            "Success criteria: all validation tests pass."
        )
        assert result["total_findings"] == 0
        assert result["has_issues"] is False

    def test_problematic_text(self):
        result = run_all_detectors(
            "Build a platform for businesses. "
            "This comprehensive tool should do everything end-to-end. "
            "Handle edge cases and optimize later."
        )
        assert result["total_findings"] > 0
        assert result["has_issues"] is True
        assert len(result["vague_nouns"]) > 0
        assert len(result["undefined_users"]) > 0
        assert len(result["overloaded_goals"]) > 0
        assert len(result["forbidden_phrases"]) > 0

    def test_returns_all_categories(self):
        result = run_all_detectors("Some text about a project.")
        assert "vague_nouns" in result
        assert "undefined_users" in result
        assert "overloaded_goals" in result
        assert "forbidden_phrases" in result
        assert "missing_criteria" in result
        assert "total_findings" in result
        assert "has_issues" in result
