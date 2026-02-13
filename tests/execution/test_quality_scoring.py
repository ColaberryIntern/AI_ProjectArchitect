"""Tests for enterprise quality scoring in execution/quality_gate_runner.py."""

import pytest

from execution.quality_gate_runner import (
    _score_implementation_specificity,
    _score_subsections,
    _score_technical_density,
    _score_word_count,
    score_chapter,
    score_document,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_rich_chapter(subsections=None, word_target=3000):
    """Build a realistic enterprise-quality chapter text."""
    subs = subsections or [
        "Vision & Strategy", "Business Model", "Competitive Landscape",
        "Market Size Context", "Risk Summary", "Technical High-Level Architecture",
        "Deployment Model", "Assumptions & Constraints",
    ]
    parts = ["# Chapter 1: Executive Summary\n"]
    words_per_sub = max(200, word_target // len(subs))

    for sub in subs:
        filler = (
            f"This section covers {sub.lower()} for the project. "
            f"First, review the requirements. Then, implement the core logic. "
            f"Next, add tests. Finally, deploy to production. "
            f"The input is the project profile. The output is a working component. "
            f"This depends on the base configuration. "
            f"The DATABASE_URL environment variable must be set. "
            f"Use `pytest tests/` to run the test suite. "
            f"Deploy with `docker compose up -d`. "
        )
        # Repeat to hit word count
        repeats = max(1, words_per_sub // len(filler.split()))
        body = (filler + "\n\n") * repeats
        parts.append(f"## {sub}\n\n{body}")

    # Add technical artifacts
    parts.append("\n```python\ndef example():\n    return True\n```\n")
    parts.append("\n| Column | Type | Description |\n|--------|------|-------------|\n| id | int | Primary key |\n")
    parts.append("\n- File: `src/main.py`\n- File: `config/settings.yaml`\n")

    return "\n\n".join(parts)


def _make_sparse_chapter():
    """Build a minimal chapter that should score low."""
    return (
        "# Chapter 1: Executive Summary\n\n"
        "## Purpose\n\n"
        "This chapter covers the purpose of the system. The system exists to help users."
    )


# ---------------------------------------------------------------------------
# _score_word_count
# ---------------------------------------------------------------------------

class TestScoreWordCount:
    def test_meets_target_gets_25(self):
        text = " ".join(["word"] * 2500)
        count, score = _score_word_count(text, 2500)
        assert count == 2500
        assert score == 25

    def test_exceeds_target_caps_at_25(self):
        text = " ".join(["word"] * 5000)
        count, score = _score_word_count(text, 2500)
        assert score == 25

    def test_half_target_gets_12(self):
        text = " ".join(["word"] * 1250)
        count, score = _score_word_count(text, 2500)
        assert score == 12

    def test_empty_text_gets_0(self):
        count, score = _score_word_count("", 2500)
        assert count == 0
        assert score == 0

    def test_zero_min_words_gets_25(self):
        count, score = _score_word_count("some text", 0)
        assert score == 25


# ---------------------------------------------------------------------------
# _score_subsections
# ---------------------------------------------------------------------------

class TestScoreSubsections:
    def test_all_present_gets_25(self):
        text = "## Vision & Strategy\ncontent\n## Business Model\ncontent"
        found, missing, score = _score_subsections(text, ["Vision & Strategy", "Business Model"])
        assert len(found) == 2
        assert len(missing) == 0
        assert score == 25

    def test_half_present_gets_12(self):
        text = "## Vision & Strategy\ncontent"
        found, missing, score = _score_subsections(text, ["Vision & Strategy", "Business Model"])
        assert len(found) == 1
        assert len(missing) == 1
        assert score == 12

    def test_none_present_gets_0(self):
        text = "## Unrelated Heading\ncontent"
        found, missing, score = _score_subsections(text, ["Vision & Strategy", "Business Model"])
        assert len(found) == 0
        assert score == 0

    def test_empty_required_gets_25(self):
        found, missing, score = _score_subsections("any text", [])
        assert score == 25

    def test_finds_subsection_in_body_text(self):
        text = "The vision & strategy section discusses the business model approach."
        found, missing, score = _score_subsections(text, ["Vision & Strategy", "Business Model"])
        assert "Vision & Strategy" in found
        assert "Business Model" in found


# ---------------------------------------------------------------------------
# _score_technical_density
# ---------------------------------------------------------------------------

class TestScoreTechnicalDensity:
    def test_rich_content_scores_high(self):
        text = _make_rich_chapter()
        score = _score_technical_density(text)
        assert score >= 15

    def test_no_technical_content_scores_0(self):
        text = "This is a plain text chapter with no technical content at all."
        score = _score_technical_density(text)
        assert score == 0

    def test_code_blocks_counted(self):
        text = "```python\nprint('hello')\n```\n\n```bash\nnpm install\n```"
        score = _score_technical_density(text)
        assert score >= 5

    def test_file_paths_counted(self):
        text = "Edit `src/main.py` and `config/settings.yaml` and `tests/test_main.py`"
        score = _score_technical_density(text)
        assert score >= 5

    def test_cli_commands_counted(self):
        text = "Run `pip install flask` then `python manage.py` and `docker compose up`"
        score = _score_technical_density(text)
        assert score >= 5

    def test_tables_counted(self):
        text = "| Col1 | Col2 |\n|------|------|\n| val1 | val2 |\n| val3 | val4 |"
        score = _score_technical_density(text)
        assert score >= 5

    def test_env_vars_counted(self):
        text = "DATABASE_URL= SECRET_KEY= API_TOKEN= REDIS_HOST="
        score = _score_technical_density(text)
        assert score >= 5


# ---------------------------------------------------------------------------
# _score_implementation_specificity
# ---------------------------------------------------------------------------

class TestScoreImplementationSpecificity:
    def test_rich_content_scores_high(self):
        text = _make_rich_chapter()
        score = _score_implementation_specificity(text)
        assert score >= 15

    def test_no_specificity_scores_0(self):
        text = "Generic content without any implementation details."
        score = _score_implementation_specificity(text)
        assert score == 0

    def test_execution_order_detected(self):
        text = "Step 1: Do this. Step 2: Do that. First, set up. Then, implement."
        score = _score_implementation_specificity(text)
        assert score > 0

    def test_deployment_detected(self):
        text = "Deploy to production using Docker. The CI/CD pipeline runs tests."
        score = _score_implementation_specificity(text)
        assert score > 0

    def test_testing_detected(self):
        text = "Run pytest to execute unit tests. Integration test suite validates."
        score = _score_implementation_specificity(text)
        assert score > 0


# ---------------------------------------------------------------------------
# score_chapter (integration)
# ---------------------------------------------------------------------------

class TestScoreChapter:
    def test_rich_chapter_scores_complete(self):
        text = _make_rich_chapter()
        result = score_chapter(text, "Executive Summary", "enterprise")
        assert result["total_score"] >= 75
        assert result["status"] == "complete"

    def test_sparse_chapter_scores_incomplete(self):
        text = _make_sparse_chapter()
        result = score_chapter(text, "Executive Summary", "enterprise")
        assert result["total_score"] < 40
        assert result["status"] == "incomplete"

    def test_contains_all_score_fields(self):
        text = _make_rich_chapter()
        result = score_chapter(text, "Executive Summary", "enterprise")
        assert "total_score" in result
        assert "word_count" in result
        assert "word_count_score" in result
        assert "subsections_found" in result
        assert "subsections_missing" in result
        assert "subsection_score" in result
        assert "technical_density_score" in result
        assert "implementation_specificity_score" in result
        assert "status" in result
        assert "gate_results" in result

    def test_gate_results_included(self):
        text = _make_rich_chapter()
        result = score_chapter(text, "Executive Summary", "enterprise")
        assert "all_passed" in result["gate_results"]

    def test_lite_mode_has_lower_threshold(self):
        # Same text should score higher in lite mode (lower min_words)
        text = " ".join(["word"] * 1000) + "\n## Vision & Strategy\nContent."
        lite_result = score_chapter(text, "Executive Summary", "lite")
        enterprise_result = score_chapter(text, "Executive Summary", "enterprise")
        assert lite_result["word_count_score"] >= enterprise_result["word_count_score"]

    def test_needs_expansion_status(self):
        # Build a chapter that should land in the 40-74 range
        subs = ["Vision & Strategy", "Business Model", "Competitive Landscape",
                "Market Size Context", "Risk Summary", "Technical High-Level Architecture"]
        parts = ["# Chapter 1: Exec Summary\n"]
        for sub in subs:
            parts.append(f"## {sub}\n\nThis section covers {sub.lower()}. Some details here.\n")
        # Add moderate word count
        parts.append(" ".join(["filler"] * 1500))
        text = "\n".join(parts)
        result = score_chapter(text, "Executive Summary", "enterprise")
        # With minimal technical content and moderate words, should be needs_expansion
        assert result["status"] in ("needs_expansion", "incomplete")

    def test_total_score_is_sum_of_dimensions(self):
        text = _make_rich_chapter()
        result = score_chapter(text, "Executive Summary", "enterprise")
        expected = (
            result["word_count_score"]
            + result["subsection_score"]
            + result["technical_density_score"]
            + result["implementation_specificity_score"]
        )
        assert result["total_score"] == expected


# ---------------------------------------------------------------------------
# score_document
# ---------------------------------------------------------------------------

class TestScoreDocument:
    def test_empty_list_returns_incomplete(self):
        result = score_document([])
        assert result["status"] == "incomplete"
        assert result["chapter_count"] == 0

    def test_all_complete_returns_complete(self):
        scores = [
            {"total_score": 85, "word_count": 3000, "status": "complete"},
            {"total_score": 90, "word_count": 3500, "status": "complete"},
        ]
        result = score_document(scores)
        assert result["status"] == "complete"
        assert result["chapters_complete"] == 2
        assert result["chapters_incomplete"] == 0

    def test_aggregates_word_count(self):
        scores = [
            {"total_score": 80, "word_count": 3000, "status": "complete"},
            {"total_score": 80, "word_count": 2000, "status": "complete"},
        ]
        result = score_document(scores)
        assert result["total_word_count"] == 5000

    def test_calculates_estimated_pages(self):
        scores = [
            {"total_score": 80, "word_count": 5000, "status": "complete"},
        ]
        result = score_document(scores)
        assert result["estimated_pages"] == 10

    def test_calculates_average_score(self):
        scores = [
            {"total_score": 80, "word_count": 3000, "status": "complete"},
            {"total_score": 60, "word_count": 2000, "status": "needs_expansion"},
        ]
        result = score_document(scores)
        assert result["average_score"] == 70

    def test_counts_statuses(self):
        scores = [
            {"total_score": 85, "word_count": 3000, "status": "complete"},
            {"total_score": 50, "word_count": 1500, "status": "needs_expansion"},
            {"total_score": 30, "word_count": 500, "status": "incomplete"},
        ]
        result = score_document(scores)
        assert result["chapters_complete"] == 1
        assert result["chapters_needs_expansion"] == 1
        assert result["chapters_incomplete"] == 1

    def test_incomplete_chapters_prevent_complete_status(self):
        scores = [
            {"total_score": 85, "word_count": 3000, "status": "complete"},
            {"total_score": 30, "word_count": 500, "status": "incomplete"},
        ]
        result = score_document(scores)
        assert result["status"] != "complete"
