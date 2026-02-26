"""Tests for execution/auto_builder.py."""

import copy
from pathlib import Path
from unittest.mock import patch

import pytest

from execution.auto_builder import (
    BuildEvent,
    BuildMetrics,
    _combine_all_chapters,
    _extract_gate_failures,
    clear_build_progress,
    get_build_progress,
    is_build_running,
    run_auto_build,
    _append_event,
)
from execution.outline_generator import ENHANCED_SECTIONS
from execution.state_manager import (
    add_feature,
    advance_phase,
    approve_features,
    confirm_all_profile_fields,
    get_current_phase,
    initialize_state,
    lock_outline,
    record_idea,
    save_state,
    set_build_depth_mode,
    set_outline_sections,
    set_profile_field,
)


def _setup_ready_state(tmp_output_dir) -> tuple[dict, str]:
    """Create a fully configured state ready for auto-build (in chapter_build phase)."""
    state = initialize_state("Auto Build Test")
    slug = state["project"]["slug"]

    # Record idea
    record_idea(state, "Build an AI-powered project planner")

    # Set up profile
    fields_data = {
        "problem_definition": {
            "options": [{"value": "slow_planning", "label": "Slow planning"}],
            "recommended": "slow_planning", "confidence": 0.9,
        },
        "target_user": {
            "options": [{"value": "pms", "label": "Project managers"}],
            "recommended": "pms", "confidence": 0.85,
        },
        "value_proposition": {
            "options": [{"value": "automate_reqs", "label": "Automate requirements"}],
            "recommended": "automate_reqs", "confidence": 0.8,
        },
        "deployment_type": {
            "options": [{"value": "saas", "label": "SaaS"}],
            "recommended": "saas", "confidence": 0.9,
        },
        "ai_depth": {
            "options": [{"value": "ai_assisted", "label": "AI-assisted"}],
            "recommended": "ai_assisted", "confidence": 0.85,
        },
        "monetization_model": {
            "options": [{"value": "freemium", "label": "Freemium"}],
            "recommended": "freemium", "confidence": 0.8,
        },
        "mvp_scope": {
            "options": [{"value": "core_only", "label": "Core only"}],
            "recommended": "core_only", "confidence": 0.85,
        },
    }
    for field, data in fields_data.items():
        set_profile_field(state, field, data["options"], data["recommended"], data["confidence"])

    selections = {field: data["recommended"] for field, data in fields_data.items()}
    confirm_all_profile_fields(state, selections)

    # Features
    advance_phase(state, "feature_discovery")
    add_feature(state, "core", "f1", "Feature One", "First feature", "Core", build_order=1)
    add_feature(state, "core", "f2", "Feature Two", "Second feature", "Core", build_order=2)
    approve_features(state)

    # Outline with 3 sections (faster tests)
    advance_phase(state, "outline_generation")
    sections = [
        {"index": 1, "title": "Executive Summary", "type": "required",
         "summary": "High-level overview of the project and its goals."},
        {"index": 2, "title": "Functional Requirements", "type": "required",
         "summary": "Detailed specifications of system capabilities."},
        {"index": 3, "title": "Technical Architecture & Data Model", "type": "required",
         "summary": "System design, components, and data flow."},
    ]
    set_outline_sections(state, sections)

    # Lock and advance to chapter_build
    advance_phase(state, "outline_approval")
    lock_outline(state)
    advance_phase(state, "chapter_build")
    save_state(state, slug)

    return state, slug


def _make_enterprise_content(section_title: str) -> dict:
    """Create enterprise chapter content that passes quality gates."""
    from execution.chapter_writer import _fallback_chapter_enterprise
    return _fallback_chapter_enterprise(section_title, f"Details about {section_title}", 1, "enterprise")


def _make_fallback_content(section_title: str) -> dict:
    """Create chapter content that passes quality gates (legacy format)."""
    from execution.chapter_writer import _fallback_chapter
    return _fallback_chapter(section_title, f"Details about {section_title}", 1)


class TestRunAutoBuild:
    """Tests for run_auto_build() with enterprise mode."""

    @patch("execution.auto_builder.generate_chapter_enterprise_with_usage")
    @patch("execution.auto_builder.generate_chapter_enterprise_with_retry_and_usage")
    def test_full_pipeline_yields_complete_event(
        self, mock_retry, mock_gen, tmp_output_dir
    ):
        mock_gen.side_effect = lambda *a, **kw: (_make_enterprise_content(a[2]), {})
        mock_retry.side_effect = lambda *a, **kw: (_make_enterprise_content(a[2]), {})
        state, slug = _setup_ready_state(tmp_output_dir)

        events = list(run_auto_build(state, slug))
        event_types = [e.event_type for e in events]

        assert "complete" in event_types
        assert events[-1].event_type == "complete"

    @patch("execution.auto_builder.generate_chapter_enterprise_with_usage")
    @patch("execution.auto_builder.generate_chapter_enterprise_with_retry_and_usage")
    def test_all_chapters_generated_and_approved(
        self, mock_retry, mock_gen, tmp_output_dir
    ):
        mock_gen.side_effect = lambda *a, **kw: (_make_enterprise_content(a[2]), {})
        mock_retry.side_effect = lambda *a, **kw: (_make_enterprise_content(a[2]), {})
        state, slug = _setup_ready_state(tmp_output_dir)

        list(run_auto_build(state, slug))

        for chapter in state["chapters"]:
            assert chapter["status"] == "approved"
            assert chapter.get("content_path") is not None
            assert Path(chapter["content_path"]).exists()

    @patch("execution.auto_builder.generate_chapter_enterprise_with_usage")
    @patch("execution.auto_builder.generate_chapter_enterprise_with_retry_and_usage")
    def test_state_advances_to_complete(self, mock_retry, mock_gen, tmp_output_dir):
        mock_gen.side_effect = lambda *a, **kw: (_make_enterprise_content(a[2]), {})
        mock_retry.side_effect = lambda *a, **kw: (_make_enterprise_content(a[2]), {})
        state, slug = _setup_ready_state(tmp_output_dir)

        list(run_auto_build(state, slug))

        assert get_current_phase(state) == "complete"

    @patch("execution.auto_builder.generate_chapter_enterprise_with_usage")
    @patch("execution.auto_builder.generate_chapter_enterprise_with_retry_and_usage")
    def test_document_file_created(self, mock_retry, mock_gen, tmp_output_dir):
        mock_gen.side_effect = lambda *a, **kw: (_make_enterprise_content(a[2]), {})
        mock_retry.side_effect = lambda *a, **kw: (_make_enterprise_content(a[2]), {})
        state, slug = _setup_ready_state(tmp_output_dir)

        list(run_auto_build(state, slug))

        assert state["document"]["output_path"] is not None
        assert Path(state["document"]["output_path"]).exists()
        assert state["document"]["filename"].endswith(".md")

    @patch("execution.auto_builder.generate_chapter_enterprise_with_usage")
    @patch("execution.auto_builder.generate_chapter_enterprise_with_retry_and_usage")
    def test_progress_events_emitted_in_order(self, mock_retry, mock_gen, tmp_output_dir):
        mock_gen.side_effect = lambda *a, **kw: (_make_enterprise_content(a[2]), {})
        mock_retry.side_effect = lambda *a, **kw: (_make_enterprise_content(a[2]), {})
        state, slug = _setup_ready_state(tmp_output_dir)

        events = list(run_auto_build(state, slug))

        chapter_events = [e for e in events if e.event_type == "chapter"]
        assert len(chapter_events) == 3

        # Should have scoring events for each chapter
        scoring_events = [e for e in events if e.event_type == "scoring"]
        assert len(scoring_events) >= 3

    @patch("execution.auto_builder.generate_chapter_enterprise_with_usage")
    @patch("execution.auto_builder.generate_chapter_enterprise_with_retry_and_usage")
    def test_percent_increases_monotonically(self, mock_retry, mock_gen, tmp_output_dir):
        mock_gen.side_effect = lambda *a, **kw: (_make_enterprise_content(a[2]), {})
        mock_retry.side_effect = lambda *a, **kw: (_make_enterprise_content(a[2]), {})
        state, slug = _setup_ready_state(tmp_output_dir)

        events = list(run_auto_build(state, slug))
        percents = [e.percent for e in events]

        for i in range(1, len(percents)):
            assert percents[i] >= percents[i - 1], (
                f"Percent decreased from {percents[i-1]} to {percents[i]} "
                f"at event {i}: {events[i].message}"
            )

    @patch("execution.auto_builder.generate_chapter_enterprise_with_usage")
    @patch("execution.auto_builder.generate_chapter_enterprise_with_retry_and_usage")
    def test_gate_failure_triggers_retry(self, mock_retry, mock_gen, tmp_output_dir):
        """If first attempt fails gates, retry should be called."""
        bad_content = {
            "content": "Handle edge cases and optimize later for this system. Use best practices."
        }

        call_count = [0]
        def mock_gen_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return bad_content, {}
            return _make_enterprise_content(args[2]), {}

        mock_gen.side_effect = mock_gen_side_effect
        mock_retry.side_effect = lambda *a, **kw: (_make_enterprise_content(a[2]), {})

        state, slug = _setup_ready_state(tmp_output_dir)
        events = list(run_auto_build(state, slug))

        retry_events = [e for e in events if e.event_type == "retry"]
        assert len(retry_events) >= 1
        assert mock_retry.called

    @patch("execution.auto_builder.generate_chapter_enterprise_with_usage")
    @patch("execution.auto_builder.generate_chapter_enterprise_with_retry_and_usage")
    def test_max_retries_force_approves(self, mock_retry, mock_gen, tmp_output_dir):
        """If all retries fail, chapter should be force-approved."""
        bad_content = {
            "content": "Handle edge cases and optimize later. Use best practices."
        }

        mock_gen.return_value = (bad_content, {})
        mock_retry.return_value = (bad_content, {})

        state, slug = _setup_ready_state(tmp_output_dir)
        events = list(run_auto_build(state, slug))

        error_events = [e for e in events if e.event_type == "error"]
        assert len(error_events) >= 1

        for chapter in state["chapters"]:
            assert chapter["status"] == "approved"

    def test_empty_chapters_yields_error(self, tmp_output_dir):
        state, slug = _setup_ready_state(tmp_output_dir)
        state["chapters"] = []

        events = list(run_auto_build(state, slug))
        assert events[0].event_type == "error"
        assert "No chapters" in events[0].message

    @patch("execution.auto_builder.generate_chapter_enterprise_with_usage")
    @patch("execution.auto_builder.generate_chapter_enterprise_with_retry_and_usage")
    def test_scoring_events_contain_data(self, mock_retry, mock_gen, tmp_output_dir):
        mock_gen.side_effect = lambda *a, **kw: (_make_enterprise_content(a[2]), {})
        mock_retry.side_effect = lambda *a, **kw: (_make_enterprise_content(a[2]), {})
        state, slug = _setup_ready_state(tmp_output_dir)

        events = list(run_auto_build(state, slug))
        scoring_events = [e for e in events if e.event_type == "scoring" and e.chapter_index > 0]

        assert len(scoring_events) >= 3
        for se in scoring_events:
            assert "score" in se.data
            assert "word_count" in se.data
            assert "status" in se.data

    @patch("execution.auto_builder.generate_chapter_enterprise_with_usage")
    @patch("execution.auto_builder.generate_chapter_enterprise_with_retry_and_usage")
    def test_complete_event_contains_page_estimate(self, mock_retry, mock_gen, tmp_output_dir):
        mock_gen.side_effect = lambda *a, **kw: (_make_enterprise_content(a[2]), {})
        mock_retry.side_effect = lambda *a, **kw: (_make_enterprise_content(a[2]), {})
        state, slug = _setup_ready_state(tmp_output_dir)

        events = list(run_auto_build(state, slug))
        complete_event = [e for e in events if e.event_type == "complete"][0]

        assert "estimated_pages" in complete_event.data
        assert "total_word_count" in complete_event.data
        assert "average_score" in complete_event.data

    @patch("execution.auto_builder.generate_chapter_enterprise_with_usage")
    @patch("execution.auto_builder.generate_chapter_enterprise_with_retry_and_usage")
    def test_chapter_scores_recorded_in_state(self, mock_retry, mock_gen, tmp_output_dir):
        mock_gen.side_effect = lambda *a, **kw: (_make_enterprise_content(a[2]), {})
        mock_retry.side_effect = lambda *a, **kw: (_make_enterprise_content(a[2]), {})
        state, slug = _setup_ready_state(tmp_output_dir)

        list(run_auto_build(state, slug))

        for chapter in state["chapters"]:
            assert "chapter_score" in chapter
            assert chapter["chapter_score"]["total_score"] >= 0

    @patch("execution.auto_builder.generate_chapter_enterprise_with_usage")
    @patch("execution.auto_builder.generate_chapter_enterprise_with_retry_and_usage")
    def test_validation_event_emitted(self, mock_retry, mock_gen, tmp_output_dir):
        mock_gen.side_effect = lambda *a, **kw: (_make_enterprise_content(a[2]), {})
        mock_retry.side_effect = lambda *a, **kw: (_make_enterprise_content(a[2]), {})
        state, slug = _setup_ready_state(tmp_output_dir)

        events = list(run_auto_build(state, slug))
        validation_events = [e for e in events if e.event_type == "validation"]
        assert len(validation_events) >= 1

    @patch("execution.auto_builder.generate_chapter_enterprise_with_usage")
    @patch("execution.auto_builder.generate_chapter_enterprise_with_retry_and_usage")
    def test_depth_mode_event_emitted(self, mock_retry, mock_gen, tmp_output_dir):
        mock_gen.side_effect = lambda *a, **kw: (_make_enterprise_content(a[2]), {})
        mock_retry.side_effect = lambda *a, **kw: (_make_enterprise_content(a[2]), {})
        state, slug = _setup_ready_state(tmp_output_dir)

        events = list(run_auto_build(state, slug))
        phase_events = [e for e in events if e.event_type == "phase"]
        assert any("Professional" in e.message for e in phase_events)


class TestRunAutoBuildLiteMode:
    """Tests for run_auto_build() with lite mode (legacy path)."""

    @patch("execution.auto_builder.generate_chapter_with_usage")
    @patch("execution.auto_builder.generate_chapter_with_retry_and_usage")
    def test_lite_mode_uses_legacy_functions(self, mock_retry, mock_gen, tmp_output_dir):
        mock_gen.side_effect = lambda *a, **kw: (_make_fallback_content(a[2]), {})
        mock_retry.side_effect = lambda *a, **kw: (_make_fallback_content(a[2]), {})
        state, slug = _setup_ready_state(tmp_output_dir)
        set_build_depth_mode(state, "lite")
        save_state(state, slug)

        events = list(run_auto_build(state, slug))
        event_types = [e.event_type for e in events]
        assert "complete" in event_types
        assert mock_gen.called

    @patch("execution.auto_builder.generate_chapter_with_usage")
    @patch("execution.auto_builder.generate_chapter_with_retry_and_usage")
    def test_lite_mode_advances_to_complete(self, mock_retry, mock_gen, tmp_output_dir):
        mock_gen.side_effect = lambda *a, **kw: (_make_fallback_content(a[2]), {})
        mock_retry.side_effect = lambda *a, **kw: (_make_fallback_content(a[2]), {})
        state, slug = _setup_ready_state(tmp_output_dir)
        set_build_depth_mode(state, "lite")
        save_state(state, slug)

        list(run_auto_build(state, slug))
        assert get_current_phase(state) == "complete"


class TestBuildProgress:
    """Tests for the in-memory progress store."""

    def test_get_progress_empty_for_new_slug(self):
        events = get_build_progress("nonexistent-slug-12345")
        assert events == []

    def test_append_and_get_events(self):
        slug = "test-progress-slug"
        clear_build_progress(slug)

        event = BuildEvent("chapter", "Writing chapter 1", 1, 3, 10)
        _append_event(slug, event)

        events = get_build_progress(slug)
        assert len(events) == 1
        assert events[0].message == "Writing chapter 1"

        clear_build_progress(slug)

    def test_clear_progress_removes_events(self):
        slug = "test-clear-slug"
        _append_event(slug, BuildEvent("chapter", "test", 1, 3, 10))
        clear_build_progress(slug)
        assert get_build_progress(slug) == []

    def test_is_build_running_false_when_empty(self):
        assert is_build_running("no-build-here") is False

    def test_is_build_running_true_during_build(self):
        slug = "running-build-test"
        clear_build_progress(slug)
        _append_event(slug, BuildEvent("chapter", "Working...", 1, 3, 10))
        assert is_build_running(slug) is True
        clear_build_progress(slug)

    def test_is_build_running_false_after_complete(self):
        slug = "completed-build-test"
        clear_build_progress(slug)
        _append_event(slug, BuildEvent("complete", "Done!", 0, 3, 100))
        assert is_build_running(slug) is False
        clear_build_progress(slug)


class TestBuildEvent:
    """Tests for BuildEvent dataclass."""

    def test_to_dict(self):
        event = BuildEvent("chapter", "Writing chapter 1", 1, 10, 15)
        d = event.to_dict()
        assert d["event_type"] == "chapter"
        assert d["message"] == "Writing chapter 1"
        assert d["chapter_index"] == 1
        assert d["percent"] == 15

    def test_timestamp_auto_generated(self):
        event = BuildEvent("phase", "Starting", 0, 10, 0)
        assert event.timestamp is not None
        assert len(event.timestamp) > 0

    def test_data_field_default_empty_dict(self):
        event = BuildEvent("phase", "test", 0, 10, 0)
        assert event.data == {}

    def test_data_field_in_to_dict(self):
        event = BuildEvent("scoring", "test", 1, 10, 50, data={"score": 85})
        d = event.to_dict()
        assert d["data"]["score"] == 85


class TestHelpers:
    """Tests for helper functions."""

    def test_extract_gate_failures(self):
        gate_results = {
            "completeness": {"passed": False, "issues": ["Missing purpose"]},
            "clarity": {"passed": True, "issues": []},
            "anti_vagueness": {"passed": False, "issues": ["Vague phrase found"]},
            "all_passed": False,
        }
        failures = _extract_gate_failures(gate_results)
        assert "Missing purpose" in failures
        assert "Vague phrase found" in failures
        assert len(failures) == 2

    def test_extract_gate_failures_all_passed(self):
        gate_results = {
            "completeness": {"passed": True, "issues": []},
            "all_passed": True,
        }
        failures = _extract_gate_failures(gate_results)
        assert failures == []

    def test_combine_all_chapters(self, tmp_output_dir):
        state, slug = _setup_ready_state(tmp_output_dir)

        chapter_dir = tmp_output_dir / slug / "chapters"
        chapter_dir.mkdir(parents=True, exist_ok=True)
        for ch in state["chapters"]:
            path = chapter_dir / f"ch{ch['index']}.md"
            path.write_text(f"Chapter {ch['index']} content", encoding="utf-8")
            ch["content_path"] = str(path)

        result = _combine_all_chapters(state, slug)
        assert "Chapter 1 content" in result
        assert "Chapter 2 content" in result
        assert "Chapter 3 content" in result


class TestBuildMetrics:
    """Tests for the BuildMetrics dataclass."""

    def test_initial_state(self):
        m = BuildMetrics()
        assert m.total_prompt_tokens == 0
        assert m.total_completion_tokens == 0
        assert m.total_llm_calls == 0
        assert m.total_retries == 0
        assert m.chapter_metrics == {}

    def test_add_chapter_call(self):
        m = BuildMetrics()
        m.add_chapter_call(1, {"prompt_tokens": 500, "completion_tokens": 200}, 1500, attempt=1)
        assert m.total_prompt_tokens == 500
        assert m.total_completion_tokens == 200
        assert m.total_llm_calls == 1
        assert m.total_retries == 0
        assert 1 in m.chapter_metrics
        assert m.chapter_metrics[1]["prompt_tokens"] == 500

    def test_add_retry_increments_retries(self):
        m = BuildMetrics()
        m.add_chapter_call(1, {"prompt_tokens": 500, "completion_tokens": 200}, 1500, attempt=1)
        m.add_chapter_call(1, {"prompt_tokens": 600, "completion_tokens": 300}, 2000, attempt=2)
        assert m.total_retries == 1
        assert m.total_llm_calls == 2
        assert m.chapter_metrics[1]["attempts"] == 2
        assert m.chapter_metrics[1]["prompt_tokens"] == 1100

    def test_multiple_chapters(self):
        m = BuildMetrics()
        m.add_chapter_call(1, {"prompt_tokens": 100, "completion_tokens": 50}, 1000, attempt=1)
        m.add_chapter_call(2, {"prompt_tokens": 200, "completion_tokens": 100}, 2000, attempt=1)
        assert m.total_prompt_tokens == 300
        assert m.total_completion_tokens == 150
        assert len(m.chapter_metrics) == 2

    def test_estimate_cost(self):
        m = BuildMetrics()
        m.add_chapter_call(1, {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}, 1000)
        cost = m.estimate_cost()
        assert cost == round(0.15 + 0.60, 4)

    def test_estimate_cost_zero(self):
        m = BuildMetrics()
        assert m.estimate_cost() == 0.0

    def test_to_summary_dict(self):
        m = BuildMetrics()
        m.add_chapter_call(1, {"prompt_tokens": 500, "completion_tokens": 200}, 1500)
        summary = m.to_summary_dict()
        assert summary["total_prompt_tokens"] == 500
        assert summary["total_completion_tokens"] == 200
        assert summary["total_tokens"] == 700
        assert summary["total_llm_calls"] == 1
        assert summary["total_retries"] == 0
        assert "estimated_cost_usd" in summary

    def test_latency_accumulates(self):
        m = BuildMetrics()
        m.add_chapter_call(1, {"prompt_tokens": 100, "completion_tokens": 50}, 1000, attempt=1)
        m.add_chapter_call(1, {"prompt_tokens": 100, "completion_tokens": 50}, 2000, attempt=2)
        assert m.chapter_metrics[1]["latency_ms"] == 3000


class TestScoreBasedApproval:
    """Tests for score-based chapter approval (gates OR score threshold)."""

    @patch("execution.auto_builder.generate_chapter_enterprise_with_usage")
    @patch("execution.auto_builder.generate_chapter_enterprise_with_retry_and_usage")
    def test_chapter_approved_when_score_above_threshold_despite_gate_failure(
        self, mock_retry, mock_gen, tmp_output_dir
    ):
        """A chapter scoring above the threshold should be approved even if gates fail."""
        # Enterprise content that scores well but has a gate failure (e.g. missing dependency signals)
        high_score_content = {
            "content": (
                "## Vision & Strategy\n\n"
                "This chapter defines the purpose and design intent for the system.\n"
                "The implementation guidance focuses on building a scalable platform.\n\n"
                "## Detailed Requirements\n\n"
                "The system must process user requests within 200ms.\n"
                "All API endpoints require authentication tokens.\n"
                "Database connections use connection pooling with max 50 connections.\n\n"
                "## Technical Specifications\n\n"
                "```python\nclass RequestHandler:\n    def process(self, request):\n        return validate(request)\n```\n\n"
                "File path: `src/handlers/request.py`\n"
                "Environment variable: `MAX_CONNECTIONS=50`\n\n"
                "| Component | Technology | Purpose |\n"
                "| --- | --- | --- |\n"
                "| API | FastAPI | Request handling |\n"
                "| DB | PostgreSQL | Data storage |\n"
                "| Cache | Redis | Session management |\n\n"
                "## Deployment Configuration\n\n"
                "Step 1: Configure the database schema.\n"
                "Step 2: Deploy the API service.\n"
                "The input is a raw HTTP request. The output is a JSON response.\n"
                "This depends on the authentication service being available.\n\n"
            ) * 3  # Repeat to hit word count
        }

        mock_gen.side_effect = lambda *a, **kw: (high_score_content, {})
        mock_retry.side_effect = lambda *a, **kw: (high_score_content, {})

        state, slug = _setup_ready_state(tmp_output_dir)
        events = list(run_auto_build(state, slug))

        # No retry events should have been emitted (all chapters approved first try)
        retry_events = [e for e in events if e.event_type == "retry"]
        assert len(retry_events) == 0, (
            f"Expected 0 retries but got {len(retry_events)}: "
            f"{[e.message for e in retry_events]}"
        )

        # All chapters should be approved
        for chapter in state["chapters"]:
            assert chapter["status"] == "approved"

    @patch("execution.auto_builder.generate_chapter_enterprise_with_usage")
    @patch("execution.auto_builder.generate_chapter_enterprise_with_retry_and_usage")
    def test_chapter_retried_when_score_below_threshold(
        self, mock_retry, mock_gen, tmp_output_dir
    ):
        """A chapter scoring below the threshold with failed gates should trigger retries."""
        bad_content = {
            "content": "Handle edge cases and optimize later. Use best practices."
        }

        mock_gen.return_value = (bad_content, {})
        mock_retry.return_value = (bad_content, {})

        state, slug = _setup_ready_state(tmp_output_dir)
        events = list(run_auto_build(state, slug))

        retry_events = [e for e in events if e.event_type == "retry"]
        assert len(retry_events) >= 1, "Expected retries for below-threshold chapters"
        assert mock_retry.called

    @patch("execution.auto_builder.generate_chapter_enterprise_with_usage")
    @patch("execution.auto_builder.generate_chapter_enterprise_with_retry_and_usage")
    def test_chapter_approved_when_gates_pass_regardless_of_score(
        self, mock_retry, mock_gen, tmp_output_dir
    ):
        """If all gates pass, the chapter should be approved even with a low score."""
        # Use the known-good enterprise content that passes all gates
        mock_gen.side_effect = lambda *a, **kw: (_make_enterprise_content(a[2]), {})
        mock_retry.side_effect = lambda *a, **kw: (_make_enterprise_content(a[2]), {})

        state, slug = _setup_ready_state(tmp_output_dir)
        events = list(run_auto_build(state, slug))

        # All chapters should be approved (gates pass)
        for chapter in state["chapters"]:
            assert chapter["status"] == "approved"

        # No retries needed when gates pass
        retry_events = [e for e in events if e.event_type == "retry"]
        assert len(retry_events) == 0
