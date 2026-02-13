"""Unit tests for execution/outline_validator.py."""

import pytest

from execution.outline_validator import (
    ENHANCED_SECTION_ORDER,
    REQUIRED_SECTION_ORDER,
    _get_section_order,
    check_naming_conventions,
    check_no_placeholders,
    check_required_sections,
    check_section_order,
    check_section_overlap,
    run_all_checks,
)


class TestCheckRequiredSections:
    def test_all_present(self, sample_outline_sections):
        result = check_required_sections(sample_outline_sections)
        assert result["passed"] is True
        assert result["missing"] == []

    def test_missing_section(self):
        incomplete = [
            {"index": 1, "title": "System Purpose", "type": "required", "summary": "Why"},
            {"index": 2, "title": "Target Users", "type": "required", "summary": "Who"},
            # Missing: capabilities, non-goals, architecture, phases, risks
        ]
        result = check_required_sections(incomplete)
        assert result["passed"] is False
        assert len(result["missing"]) > 0

    def test_alternative_titles_match(self):
        sections = [
            {"index": 1, "title": "Why This Exists", "type": "required", "summary": "Context"},
            {"index": 2, "title": "Who Uses This", "type": "required", "summary": "Users"},
            {"index": 3, "title": "What It Does - Features", "type": "required", "summary": "Features"},
            {"index": 4, "title": "What We Will Not Build", "type": "required", "summary": "Exclusions"},
            {"index": 5, "title": "How It Works - Architecture", "type": "required", "summary": "Flow"},
            {"index": 6, "title": "Build Phases", "type": "required", "summary": "Phases"},
            {"index": 7, "title": "Risk Assessment", "type": "required", "summary": "Risks"},
        ]
        result = check_required_sections(sections)
        assert result["passed"] is True


class TestCheckSectionOrder:
    def test_correct_order(self, sample_outline_sections):
        result = check_section_order(sample_outline_sections)
        assert result["passed"] is True

    def test_wrong_order(self):
        wrong_order = [
            {"index": 1, "title": "Core Capabilities", "type": "required", "summary": "What"},
            {"index": 2, "title": "System Purpose", "type": "required", "summary": "Why"},
            {"index": 3, "title": "Target Users", "type": "required", "summary": "Who"},
            {"index": 4, "title": "Non-Goals", "type": "required", "summary": "Not"},
            {"index": 5, "title": "Architecture", "type": "required", "summary": "How"},
            {"index": 6, "title": "Execution Phases", "type": "required", "summary": "Build"},
            {"index": 7, "title": "Risks and Constraints", "type": "required", "summary": "Risks"},
        ]
        result = check_section_order(wrong_order)
        assert result["passed"] is False


class TestCheckNamingConventions:
    def test_good_names(self, sample_outline_sections):
        result = check_naming_conventions(sample_outline_sections)
        assert result["passed"] is True
        assert result["violations"] == []

    def test_marketing_language(self):
        bad_sections = [
            {"index": 1, "title": "Magic Layer", "type": "required", "summary": "Something"},
        ]
        result = check_naming_conventions(bad_sections)
        assert result["passed"] is False
        assert len(result["violations"]) > 0

    def test_secret_sauce(self):
        bad_sections = [
            {"index": 1, "title": "Secret Sauce", "type": "required", "summary": "Hidden"},
        ]
        result = check_naming_conventions(bad_sections)
        assert result["passed"] is False

    def test_too_short(self):
        bad_sections = [
            {"index": 1, "title": "X", "type": "required", "summary": "Too short"},
        ]
        result = check_naming_conventions(bad_sections)
        assert result["passed"] is False


class TestCheckNoPlaceholders:
    def test_clean_content(self, sample_outline_sections):
        result = check_no_placeholders(sample_outline_sections)
        assert result["passed"] is True

    def test_tbd_detected(self):
        sections = [
            {"index": 1, "title": "System Purpose", "type": "required", "summary": "TBD"},
        ]
        result = check_no_placeholders(sections)
        assert result["passed"] is False
        assert len(result["found"]) > 0

    def test_decide_later_detected(self):
        sections = [
            {"index": 1, "title": "Architecture", "type": "required", "summary": "We'll decide later"},
        ]
        result = check_no_placeholders(sections)
        assert result["passed"] is False

    def test_todo_detected(self):
        sections = [
            {"index": 1, "title": "TODO Section", "type": "required", "summary": "Needs work"},
        ]
        result = check_no_placeholders(sections)
        assert result["passed"] is False


class TestCheckSectionOverlap:
    def test_no_overlap(self, sample_outline_sections):
        result = check_section_overlap(sample_outline_sections)
        assert result["passed"] is True

    def test_overlapping_summaries(self):
        sections = [
            {"index": 1, "title": "Section A", "type": "required", "summary": "This handles user authentication and login flows"},
            {"index": 2, "title": "Section B", "type": "required", "summary": "This handles user authentication and login security"},
        ]
        result = check_section_overlap(sections)
        assert result["passed"] is False
        assert len(result["warnings"]) > 0


class TestRunAllChecks:
    def test_valid_outline_passes_all(self, sample_outline_sections):
        result = run_all_checks(sample_outline_sections)
        assert result["all_passed"] is True

    def test_invalid_outline_fails(self):
        bad_outline = [
            {"index": 1, "title": "TBD", "type": "required", "summary": "TBD"},
        ]
        result = run_all_checks(bad_outline)
        assert result["all_passed"] is False


# --- 10-section enhanced outline tests ---


class TestGetSectionOrder:
    def test_7_sections_returns_legacy(self, sample_outline_sections):
        result = _get_section_order(sample_outline_sections)
        assert result is REQUIRED_SECTION_ORDER

    def test_10_sections_returns_enhanced(self, sample_enhanced_outline_sections):
        result = _get_section_order(sample_enhanced_outline_sections)
        assert result is ENHANCED_SECTION_ORDER

    def test_fewer_than_7_returns_legacy(self):
        sections = [{"index": 1, "title": "Foo", "type": "required", "summary": "Bar"}]
        result = _get_section_order(sections)
        assert result is REQUIRED_SECTION_ORDER

    def test_12_sections_returns_enhanced(self):
        sections = [{"index": i, "title": f"Sec {i}", "type": "required", "summary": "x"} for i in range(12)]
        result = _get_section_order(sections)
        assert result is ENHANCED_SECTION_ORDER


class TestEnhancedRequiredSections:
    def test_all_10_present(self, sample_enhanced_outline_sections):
        result = check_required_sections(sample_enhanced_outline_sections)
        assert result["passed"] is True
        assert result["missing"] == []
        assert len(result["matched"]) == 10

    def test_missing_enhanced_section(self):
        """9 sections with AI Architecture missing."""
        sections = [
            {"index": 1, "title": "Executive Summary", "type": "required", "summary": "Overview."},
            {"index": 2, "title": "Problem & Market Context", "type": "required", "summary": "Problem."},
            {"index": 3, "title": "User Personas & Core Use Cases", "type": "required", "summary": "Users."},
            {"index": 4, "title": "Functional Requirements", "type": "required", "summary": "Features."},
            # Missing: AI & Intelligence Architecture
            {"index": 5, "title": "Non-Functional Requirements", "type": "required", "summary": "Perf."},
            {"index": 6, "title": "Technical Architecture & Data Model", "type": "required", "summary": "Tech."},
            {"index": 7, "title": "Security & Compliance", "type": "required", "summary": "Security."},
            {"index": 8, "title": "Success Metrics & KPIs", "type": "required", "summary": "Metrics."},
            {"index": 9, "title": "Roadmap & Phased Delivery", "type": "required", "summary": "Roadmap."},
            {"index": 10, "title": "Extra Padding Section", "type": "required", "summary": "Padding."},
        ]
        result = check_required_sections(sections)
        assert result["passed"] is False
        assert "AI Architecture" in result["missing"]

    def test_alternative_titles_match_enhanced(self):
        """Enhanced sections with alternative title wording."""
        sections = [
            {"index": 1, "title": "Product Overview and Summary", "type": "required", "summary": "Overview."},
            {"index": 2, "title": "Market Analysis and Problem Space", "type": "required", "summary": "Market."},
            {"index": 3, "title": "User Research and Use Cases", "type": "required", "summary": "Users."},
            {"index": 4, "title": "System Capabilities and Requirements", "type": "required", "summary": "Caps."},
            {"index": 5, "title": "ML and AI Design", "type": "required", "summary": "ML."},
            {"index": 6, "title": "Scalability and Performance", "type": "required", "summary": "Scalability."},
            {"index": 7, "title": "Data Model and Technical Design", "type": "required", "summary": "Data."},
            {"index": 8, "title": "Privacy and Compliance", "type": "required", "summary": "Privacy."},
            {"index": 9, "title": "KPI Framework", "type": "required", "summary": "KPIs."},
            {"index": 10, "title": "Delivery Roadmap", "type": "required", "summary": "Delivery."},
        ]
        result = check_required_sections(sections)
        assert result["passed"] is True


class TestEnhancedSectionOrder:
    def test_correct_order(self, sample_enhanced_outline_sections):
        result = check_section_order(sample_enhanced_outline_sections)
        assert result["passed"] is True

    def test_wrong_order_enhanced(self):
        """Roadmap before Executive Summary."""
        sections = [
            {"index": 1, "title": "Roadmap & Phased Delivery", "type": "required", "summary": "Roadmap."},
            {"index": 2, "title": "Executive Summary", "type": "required", "summary": "Overview."},
            {"index": 3, "title": "Problem & Market Context", "type": "required", "summary": "Problem."},
            {"index": 4, "title": "User Personas & Core Use Cases", "type": "required", "summary": "Users."},
            {"index": 5, "title": "Functional Requirements", "type": "required", "summary": "Features."},
            {"index": 6, "title": "AI & Intelligence Architecture", "type": "required", "summary": "AI."},
            {"index": 7, "title": "Non-Functional Requirements", "type": "required", "summary": "Perf."},
            {"index": 8, "title": "Technical Architecture & Data Model", "type": "required", "summary": "Tech."},
            {"index": 9, "title": "Security & Compliance", "type": "required", "summary": "Security."},
            {"index": 10, "title": "Success Metrics & KPIs", "type": "required", "summary": "Metrics."},
        ]
        result = check_section_order(sections)
        assert result["passed"] is False


class TestEnhancedRunAllChecks:
    def test_valid_enhanced_outline_passes(self, sample_enhanced_outline_sections):
        result = run_all_checks(sample_enhanced_outline_sections)
        assert result["all_passed"] is True

    def test_enhanced_with_placeholder_fails(self, sample_enhanced_outline_sections):
        sample_enhanced_outline_sections[3]["summary"] = "TBD"
        result = run_all_checks(sample_enhanced_outline_sections)
        assert result["all_passed"] is False
        assert result["no_placeholders"]["passed"] is False
