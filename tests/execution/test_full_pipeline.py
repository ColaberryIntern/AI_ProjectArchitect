"""Tests for execution/full_pipeline.py."""

from pathlib import Path
from unittest.mock import patch

import pytest

from execution.auto_builder import BuildEvent
from execution.full_pipeline import (
    _append_pipeline_event,
    _slugify,
    clear_pipeline_progress,
    get_pipeline_progress,
    is_pipeline_running,
    run_full_pipeline,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_profile():
    """Return a deterministic profile response matching generate_profile() output."""
    fields = {}
    for field_name in [
        "problem_definition", "target_user", "value_proposition",
        "deployment_type", "ai_depth", "monetization_model", "mvp_scope",
    ]:
        fields[field_name] = {
            "options": [
                {"value": f"{field_name}_val", "label": field_name, "description": field_name},
                {"value": f"{field_name}_alt", "label": "alt", "description": "alt"},
            ],
            "recommended": f"{field_name}_val",
            "confidence": 0.9,
        }
    return {
        "fields": fields,
        "derived": {
            "technical_constraints": ["constraint_1"],
            "non_functional_requirements": ["nfr_1"],
            "success_metrics": ["metric_1"],
            "risk_assessment": ["risk_1"],
            "core_use_cases": ["use_case_1"],
        },
    }


def _fake_catalog():
    """Return a minimal feature catalog."""
    return [
        {"id": "feat_1", "name": "Feature One", "description": "First feature", "category": "Core"},
        {"id": "feat_2", "name": "Feature Two", "description": "Second feature", "category": "Core"},
        {"id": "feat_3", "name": "Feature Three", "description": "Third feature", "category": "AI"},
    ]


def _fake_sections():
    """Return a minimal outline sections list."""
    return [
        {"index": 1, "title": "Executive Summary", "type": "required", "summary": "Overview of the project."},
        {"index": 2, "title": "Functional Requirements", "type": "required", "summary": "System capabilities."},
        {"index": 3, "title": "Technical Architecture", "type": "required", "summary": "System design."},
    ]


# ---------------------------------------------------------------------------
# Progress store tests
# ---------------------------------------------------------------------------


class TestPipelineProgressStore:
    """Tests for the in-memory pipeline progress store."""

    def test_get_progress_empty_for_unknown_job(self):
        events = get_pipeline_progress("nonexistent-job-id")
        assert events == []

    def test_append_and_get_events(self):
        job_id = "test-pipeline-progress"
        clear_pipeline_progress(job_id)
        try:
            event = BuildEvent("phase", "Starting...", 0, 0, 5)
            _append_pipeline_event(job_id, event)

            events = get_pipeline_progress(job_id)
            assert len(events) == 1
            assert events[0].message == "Starting..."
        finally:
            clear_pipeline_progress(job_id)

    def test_clear_progress_removes_events(self):
        job_id = "test-pipeline-clear"
        _append_pipeline_event(job_id, BuildEvent("phase", "test", 0, 0, 5))
        clear_pipeline_progress(job_id)
        assert get_pipeline_progress(job_id) == []

    def test_is_pipeline_running_false_when_empty(self):
        assert is_pipeline_running("no-pipeline-here") is False

    def test_is_pipeline_running_true_during_build(self):
        job_id = "running-pipeline-test"
        clear_pipeline_progress(job_id)
        try:
            _append_pipeline_event(job_id, BuildEvent("phase", "Working...", 0, 0, 10))
            assert is_pipeline_running(job_id) is True
        finally:
            clear_pipeline_progress(job_id)

    def test_is_pipeline_running_false_after_complete(self):
        job_id = "completed-pipeline-test"
        clear_pipeline_progress(job_id)
        try:
            _append_pipeline_event(job_id, BuildEvent("complete", "Done!", 0, 0, 100))
            assert is_pipeline_running(job_id) is False
        finally:
            clear_pipeline_progress(job_id)

    def test_is_pipeline_running_false_after_error(self):
        job_id = "error-pipeline-test"
        clear_pipeline_progress(job_id)
        try:
            _append_pipeline_event(job_id, BuildEvent("error", "Failed!", 0, 0, 0))
            assert is_pipeline_running(job_id) is False
        finally:
            clear_pipeline_progress(job_id)


# ---------------------------------------------------------------------------
# Slugify tests
# ---------------------------------------------------------------------------


class TestSlugify:
    """Tests for the _slugify helper."""

    def test_basic_slug(self):
        assert _slugify("My Project") == "my-project"

    def test_special_characters(self):
        assert _slugify("AI Market Research Q1 2026") == "ai-market-research-q1-2026"

    def test_leading_trailing_dashes(self):
        assert _slugify("  --Test--  ") == "test"

    def test_consecutive_special_chars(self):
        assert _slugify("Project & Build!!!") == "project-build"


# ---------------------------------------------------------------------------
# Pipeline generator tests (with mocked LLM calls)
# ---------------------------------------------------------------------------


class TestRunFullPipeline:
    """Tests for run_full_pipeline() generator."""

    @patch("execution.full_pipeline.run_auto_build")
    @patch("execution.full_pipeline.generate_outline_from_profile")
    @patch("execution.full_pipeline.generate_catalog_from_profile")
    @patch("execution.full_pipeline.generate_profile")
    def test_complete_pipeline_yields_events(
        self, mock_profile, mock_catalog, mock_outline, mock_auto_build,
        tmp_output_dir,
    ):
        """Full pipeline should yield phase events and end with complete."""
        mock_profile.return_value = _fake_profile()
        mock_catalog.return_value = _fake_catalog()
        mock_outline.return_value = _fake_sections()
        mock_auto_build.return_value = iter([
            BuildEvent("phase", "Starting build...", 0, 3, 0),
            BuildEvent("complete", "Done", 0, 3, 100),
        ])

        events = list(run_full_pipeline("Test Project", "Build an AI tool for testing"))

        event_types = [e.event_type for e in events]
        assert "phase" in event_types
        assert events[-1].event_type == "complete"

    @patch("execution.full_pipeline.run_auto_build")
    @patch("execution.full_pipeline.generate_outline_from_profile")
    @patch("execution.full_pipeline.generate_catalog_from_profile")
    @patch("execution.full_pipeline.generate_profile")
    def test_profile_is_populated_from_llm(
        self, mock_profile, mock_catalog, mock_outline, mock_auto_build,
        tmp_output_dir,
    ):
        """Profile should be generated from the raw idea via LLM."""
        mock_profile.return_value = _fake_profile()
        mock_catalog.return_value = _fake_catalog()
        mock_outline.return_value = _fake_sections()
        mock_auto_build.return_value = iter([
            BuildEvent("complete", "Done", 0, 3, 100),
        ])

        list(run_full_pipeline("Test Project", "Build an AI marketing platform"))

        mock_profile.assert_called_once_with("Build an AI marketing platform")

    @patch("execution.full_pipeline.run_auto_build")
    @patch("execution.full_pipeline.generate_outline_from_profile")
    @patch("execution.full_pipeline.generate_catalog_from_profile")
    @patch("execution.full_pipeline.generate_profile")
    def test_features_auto_selected_from_catalog(
        self, mock_profile, mock_catalog, mock_outline, mock_auto_build,
        tmp_output_dir,
    ):
        """All features from catalog should be auto-selected."""
        mock_profile.return_value = _fake_profile()
        mock_catalog.return_value = _fake_catalog()
        mock_outline.return_value = _fake_sections()
        mock_auto_build.return_value = iter([
            BuildEvent("complete", "Done", 0, 3, 100),
        ])

        events = list(run_full_pipeline("Test Features", "Build an AI tool"))

        # Verify catalog was called
        mock_catalog.assert_called_once()

        # Check that a phase event mentions the feature count
        feature_events = [e for e in events if "features selected" in e.message]
        assert len(feature_events) == 1
        assert "3 features selected" in feature_events[0].message

    @patch("execution.full_pipeline.run_auto_build")
    @patch("execution.full_pipeline.generate_outline_from_profile")
    @patch("execution.full_pipeline.generate_catalog_from_profile")
    @patch("execution.full_pipeline.generate_profile")
    def test_outline_generated_from_profile(
        self, mock_profile, mock_catalog, mock_outline, mock_auto_build,
        tmp_output_dir,
    ):
        """Outline should be generated using profile and features."""
        mock_profile.return_value = _fake_profile()
        mock_catalog.return_value = _fake_catalog()
        mock_outline.return_value = _fake_sections()
        mock_auto_build.return_value = iter([
            BuildEvent("complete", "Done", 0, 3, 100),
        ])

        list(run_full_pipeline("Test Outline", "Build an AI tool"))

        mock_outline.assert_called_once()
        # Verify it was called with professional depth mode
        call_kwargs = mock_outline.call_args
        assert call_kwargs.kwargs.get("depth_mode") == "professional"

    @patch("execution.full_pipeline.run_auto_build")
    @patch("execution.full_pipeline.generate_outline_from_profile")
    @patch("execution.full_pipeline.generate_catalog_from_profile")
    @patch("execution.full_pipeline.generate_profile")
    def test_depth_mode_passed_through(
        self, mock_profile, mock_catalog, mock_outline, mock_auto_build,
        tmp_output_dir,
    ):
        """Custom depth mode should be passed to outline and build."""
        mock_profile.return_value = _fake_profile()
        mock_catalog.return_value = _fake_catalog()
        mock_outline.return_value = _fake_sections()
        mock_auto_build.return_value = iter([
            BuildEvent("complete", "Done", 0, 3, 100),
        ])

        list(run_full_pipeline("Test Depth", "Build an AI tool", depth_mode="standard"))

        call_kwargs = mock_outline.call_args
        assert call_kwargs.kwargs.get("depth_mode") == "standard"

    def test_invalid_depth_mode_yields_error(self, tmp_output_dir):
        """Invalid depth mode should yield an error event."""
        events = list(run_full_pipeline("Test Invalid", "Build something", depth_mode="mega"))
        assert events[-1].event_type == "error"
        assert "depth mode" in events[-1].message.lower() or "Invalid" in events[-1].message

    @patch("execution.full_pipeline.run_auto_build")
    @patch("execution.full_pipeline.generate_outline_from_profile")
    @patch("execution.full_pipeline.generate_catalog_from_profile")
    @patch("execution.full_pipeline.generate_profile")
    def test_percent_increases_monotonically(
        self, mock_profile, mock_catalog, mock_outline, mock_auto_build,
        tmp_output_dir,
    ):
        """Progress percentage should never decrease."""
        mock_profile.return_value = _fake_profile()
        mock_catalog.return_value = _fake_catalog()
        mock_outline.return_value = _fake_sections()
        mock_auto_build.return_value = iter([
            BuildEvent("chapter", "Writing ch 1", 1, 3, 10),
            BuildEvent("chapter", "Writing ch 2", 2, 3, 40),
            BuildEvent("chapter", "Writing ch 3", 3, 3, 70),
            BuildEvent("complete", "Done", 0, 3, 100),
        ])

        events = list(run_full_pipeline("Test Monotonic", "Build an AI tool"))
        percents = [e.percent for e in events]

        for i in range(1, len(percents)):
            assert percents[i] >= percents[i - 1], (
                f"Percent decreased from {percents[i-1]} to {percents[i]} "
                f"at event {i}: {events[i].message}"
            )

    @patch("execution.full_pipeline.run_auto_build")
    @patch("execution.full_pipeline.generate_outline_from_profile")
    @patch("execution.full_pipeline.generate_catalog_from_profile")
    @patch("execution.full_pipeline.generate_profile")
    def test_auto_build_events_are_remapped(
        self, mock_profile, mock_catalog, mock_outline, mock_auto_build,
        tmp_output_dir,
    ):
        """Auto-build event percentages should be remapped to 28-100 range."""
        mock_profile.return_value = _fake_profile()
        mock_catalog.return_value = _fake_catalog()
        mock_outline.return_value = _fake_sections()
        mock_auto_build.return_value = iter([
            BuildEvent("phase", "Starting build", 0, 3, 0),
            BuildEvent("complete", "Done", 0, 3, 100),
        ])

        events = list(run_full_pipeline("Test Remap", "Build an AI tool"))

        # The "Starting build" event at 0% should be remapped to ~28%
        build_start = [e for e in events if e.message == "Starting build"]
        assert len(build_start) == 1
        assert build_start[0].percent >= 28

        # The "Done" event at 100% should be remapped to 100%
        assert events[-1].percent == 100

    @patch("execution.full_pipeline.generate_profile")
    def test_exception_yields_error_event(self, mock_profile, tmp_output_dir):
        """An exception during pipeline should yield an error event, not crash."""
        mock_profile.side_effect = RuntimeError("LLM exploded")

        events = list(run_full_pipeline("Test Error", "Build something"))
        assert events[-1].event_type == "error"
        assert "LLM exploded" in events[-1].message
