"""Integration tests for the full project pipeline.

Tests the complete lifecycle from initialization through final assembly,
verifying state transitions, outline locking, chapter management,
quality gates, and document export.
"""

from pathlib import Path

import pytest

from unittest.mock import patch

from execution.ambiguity_detector import run_all_detectors
from execution.document_assembler import assemble_full_document
from execution.feature_classifier import (
    check_feature_problem_mapping,
    check_intern_explainability,
    order_by_priority,
)
from execution.outline_generator import ENHANCED_SECTIONS
from execution.outline_validator import run_all_checks
from execution.quality_gate_runner import run_chapter_gates, run_final_gates
from execution.schema_validator import is_valid_project_state
from execution.state_manager import (
    add_feature,
    advance_phase,
    all_chapters_approved,
    approve_features,
    confirm_all_profile_fields,
    get_current_phase,
    get_project_profile,
    initialize_state,
    is_outline_locked,
    is_profile_complete,
    load_state,
    lock_outline,
    record_chapter_status,
    record_document_assembly,
    record_final_quality,
    record_idea,
    save_state,
    set_build_depth_mode,
    set_outline_sections,
    set_profile_field,
    verify_outline_integrity,
)
from execution.version_manager import get_active_version


class TestFullPipelineLifecycle:
    """Test the complete state lifecycle from init to final assembly."""

    def test_full_lifecycle(self, tmp_output_dir):
        # Phase 1: Initialize
        state = initialize_state("Integration Test Project")
        slug = state["project"]["slug"]
        assert get_current_phase(state) == "idea_intake"
        assert is_valid_project_state(state)

        # Phase 2: Idea Intake → Feature Discovery
        record_idea(state, "Build an AI tool that helps plan projects for interns")
        assert state["idea"]["original_raw"] != ""
        advance_phase(state, "feature_discovery")
        save_state(state, slug)

        # Phase 3: Feature Discovery
        add_feature(
            state, "core", "feat-001", "Guided Questioning",
            "Structured questioning across 4 dimensions",
            "Eliminates idea vagueness",
            problem_mapped_to="slow_planning", build_order=1,
        )
        add_feature(
            state, "core", "feat-002", "Outline Generation",
            "Generate structured outlines with required sections",
            "Creates scope contract before building",
            problem_mapped_to="scope_drift", build_order=2,
        )
        add_feature(
            state, "optional", "feat-opt-001", "PDF Export",
            "Export final document as PDF",
            "Enables distribution to non-technical stakeholders",
            deferred=True, defer_reason="Not needed for MVP",
        )

        # Verify feature classification
        problems = ["slow_planning", "scope_drift"]
        mapping = check_feature_problem_mapping(state["features"]["core"], problems)
        assert mapping["passed"] is True
        explainability = check_intern_explainability(state["features"]["core"])
        assert explainability["passed"] is True
        ordered = order_by_priority(state["features"]["core"])
        assert ordered[0]["name"] == "Guided Questioning"

        approve_features(state)
        advance_phase(state, "outline_generation")
        save_state(state, slug)

        # Phase 5: Outline Generation
        sections = [
            {"index": 1, "title": "System Purpose & Context", "type": "required",
             "summary": "Why this project exists and what problem it solves."},
            {"index": 2, "title": "Target Users & Roles", "type": "required",
             "summary": "Who interacts with the system and in what capacity."},
            {"index": 3, "title": "Core Capabilities", "type": "required",
             "summary": "What the system must do to deliver value."},
            {"index": 4, "title": "Non-Goals & Explicit Exclusions", "type": "required",
             "summary": "What the system intentionally does not attempt."},
            {"index": 5, "title": "High-Level Architecture", "type": "required",
             "summary": "How major components interact at a conceptual level."},
            {"index": 6, "title": "Execution Phases", "type": "required",
             "summary": "How the build is broken into logical chunks."},
            {"index": 7, "title": "Risks, Constraints, and Assumptions", "type": "required",
             "summary": "What could go wrong and what is being assumed."},
        ]
        set_outline_sections(state, sections)

        # Validate outline
        validation = run_all_checks(sections)
        assert validation["all_passed"] is True
        advance_phase(state, "outline_approval")
        save_state(state, slug)

        # Phase 6: Outline Approval & Lock
        lock_outline(state)
        assert is_outline_locked(state) is True
        assert verify_outline_integrity(state) is True
        assert len(state["chapters"]) == 7
        advance_phase(state, "chapter_build")
        save_state(state, slug)

        # Phase 7: Chapter Build
        chapter_dir = tmp_output_dir / slug / "chapters"
        chapter_dir.mkdir(parents=True, exist_ok=True)

        for i in range(1, 8):
            chapter_content = (
                f"# Chapter {i}: {sections[i-1]['title']}\n\n"
                f"## Purpose\n\n"
                f"This chapter exists to define {sections[i-1]['summary'].lower()}\n"
                f"The system requires this for complete project documentation.\n\n"
                f"## Design Intent\n\n"
                f"This approach was chosen to ensure clarity and reduce ambiguity.\n"
                f"The tradeoff is detail vs brevity - we chose detail for intern readability.\n\n"
                f"## Implementation Guidance\n\n"
                f"First, review the previous chapter for context.\n"
                f"Then, implement the logic described below.\n"
                f"Next, validate the output against the acceptance criteria.\n"
                f"The input is the approved ideation data from the state file.\n"
                f"The output is a structured section of the build guide.\n"
                f"This step depends on the outline being locked.\n"
                f"The execution order is: review, implement, validate.\n"
                f"Step 1 is review. Step 2 is implementation.\n"
            )
            chapter_path = chapter_dir / f"ch{i}.md"
            chapter_path.write_text(chapter_content, encoding="utf-8")
            record_chapter_status(state, i, "draft", str(chapter_path))

            # Run quality gates on the chapter
            gate_results = run_chapter_gates(chapter_content)
            assert gate_results["all_passed"] is True, (
                f"Chapter {i} failed quality gates: "
                f"{[g for g, r in gate_results.items() if isinstance(r, dict) and not r.get('passed')]}"
            )

            record_chapter_status(state, i, "approved")

        assert all_chapters_approved(state) is True
        advance_phase(state, "quality_gates")
        save_state(state, slug)

        # Phase 8: Final Quality Gates
        all_chapter_text = ""
        for i in range(1, 8):
            ch_path = chapter_dir / f"ch{i}.md"
            all_chapter_text += ch_path.read_text(encoding="utf-8") + "\n\n"

        # Add intern-test-passing content
        all_chapter_text += (
            "This system exists to transform project ideas into build guides. "
            "Start with phase 1 first. "
            "The definition of done is a complete, versioned build guide document. "
            "Success criteria: all quality gates pass."
        )

        final_gates = run_final_gates(all_chapter_text)
        assert final_gates["all_passed"] is True

        record_final_quality(state, {"all_passed": True, "details": []})
        advance_phase(state, "final_assembly")
        save_state(state, slug)

        # Phase 9: Final Assembly
        chapter_paths = [str(chapter_dir / f"ch{i}.md") for i in range(1, 8)]
        chapter_titles = [s["title"] for s in sections]

        result = assemble_full_document(
            chapter_paths=chapter_paths,
            chapter_titles=chapter_titles,
            project_name="Integration Test Project",
            project_slug=slug,
            version="v1",
        )

        assert Path(result["output_path"]).exists()
        assert "Integration_Test_Project" in result["filename"]
        assert "v1" in result["filename"]

        record_document_assembly(state, result["filename"], result["output_path"])
        advance_phase(state, "complete")
        save_state(state, slug)

        # Final verification
        assert get_current_phase(state) == "complete"
        assert is_valid_project_state(state)
        assert state["document"]["filename"] is not None
        assert state["document"]["output_path"] is not None

        # Verify we can reload the state
        reloaded = load_state(slug)
        assert reloaded["current_phase"] == "complete"
        assert reloaded["document"]["filename"] == result["filename"]


class TestOutlineLockUnlockCycle:
    """Test the outline lock, unlock, and re-lock cycle."""

    def test_lock_unlock_relock(self, tmp_output_dir):
        state = initialize_state("Lock Test Project")
        slug = state["project"]["slug"]

        sections = [
            {"index": 1, "title": "System Purpose", "type": "required", "summary": "Why it exists."},
            {"index": 2, "title": "Target Users", "type": "required", "summary": "Who uses it."},
            {"index": 3, "title": "Core Capabilities", "type": "required", "summary": "What it does."},
            {"index": 4, "title": "Non-Goals", "type": "required", "summary": "What it does not."},
            {"index": 5, "title": "Architecture", "type": "required", "summary": "How it works."},
            {"index": 6, "title": "Execution Phases", "type": "required", "summary": "Build order."},
            {"index": 7, "title": "Risks and Constraints", "type": "required", "summary": "Known risks."},
        ]
        set_outline_sections(state, sections)

        # Lock
        lock_outline(state)
        assert is_outline_locked(state) is True
        assert get_active_version(state) == 1

        # Unlock
        from execution.state_manager import unlock_outline
        unlock_outline(state, "Need to add security section")
        assert is_outline_locked(state) is False
        assert get_active_version(state) == 2

        # Re-lock
        lock_outline(state)
        assert is_outline_locked(state) is True
        assert verify_outline_integrity(state) is True
        assert len(state["outline"]["approval_history"]) == 3  # approve, unlock, re-approve


class TestQualityGateEnforcement:
    """Test that quality gates block progression when failing."""

    def test_vague_chapter_fails(self):
        vague_text = (
            "# Chapter\n\n"
            "## Purpose\n\nThis chapter handles edge cases.\n\n"
            "## Design Intent\n\nUse best practices and optimize later.\n\n"
            "## Implementation Guidance\n\n"
            "Make it scalable and ensure good UX.\n"
            "Handle various scenarios as needed.\n"
            "Use appropriate tools where applicable.\n"
            "First do this, then do that.\n"
            "The input is data. The output is a report.\n"
            "This depends on the config being ready.\n"
            "Step 1 is setup.\n"
        )
        results = run_chapter_gates(vague_text)
        assert results["all_passed"] is False
        assert results["anti_vagueness"]["passed"] is False


class TestAmbiguityDetectionIntegration:
    """Test ambiguity detection on realistic text."""

    def test_good_project_description(self):
        text = (
            "Junior developers at Colaberry will use this REST API endpoint "
            "to submit project ideas. The endpoint accepts a JSON payload with "
            "a project name and description, validates the input against a "
            "JSON Schema, creates a state file, and returns a 201 response "
            "with the project slug. Success is measured by the state file "
            "passing schema validation."
        )
        result = run_all_detectors(text)
        # This specific text should have very few or no findings
        assert result["forbidden_phrases"] == []
        assert result["overloaded_goals"] == []

    def test_bad_project_description(self):
        text = (
            "Build a comprehensive platform for businesses. "
            "People will use this end-to-end solution to do everything. "
            "Handle edge cases and optimize later. "
            "Use best practices and ensure good UX."
        )
        result = run_all_detectors(text)
        assert result["has_issues"] is True
        assert len(result["vague_nouns"]) > 0
        assert len(result["forbidden_phrases"]) > 0


class TestEnhancedPipelineWithProfile:
    """Test 10-section pipeline using project_profile flow."""

    def test_profile_driven_10_section_lifecycle(self, tmp_output_dir):
        # Phase 1: Initialize
        state = initialize_state("Profile-Driven Test Project")
        slug = state["project"]["slug"]
        assert get_current_phase(state) == "idea_intake"

        # Record idea
        record_idea(state, "Build an AI-powered project planning tool")

        # Simulate profile generation: set options for all 7 fields
        fields_data = {
            "problem_definition": {"options": [
                {"value": "Manual planning is slow", "label": "Manual planning is slow"},
                {"value": "Requirements drift", "label": "Requirements drift"},
            ], "recommended": "Manual planning is slow", "confidence": 0.9},
            "target_user": {"options": [
                {"value": "Non-technical PMs", "label": "Non-technical PMs"},
                {"value": "Engineers", "label": "Engineers"},
            ], "recommended": "Non-technical PMs", "confidence": 0.85},
            "value_proposition": {"options": [
                {"value": "Automate requirements", "label": "Automate requirements"},
                {"value": "Speed up planning", "label": "Speed up planning"},
            ], "recommended": "Automate requirements", "confidence": 0.8},
            "deployment_type": {"options": [
                {"value": "SaaS multi-tenant", "label": "SaaS multi-tenant"},
                {"value": "On-premise", "label": "On-premise"},
            ], "recommended": "SaaS multi-tenant", "confidence": 0.9},
            "ai_depth": {"options": [
                {"value": "AI-assisted", "label": "AI-assisted"},
                {"value": "Light automation", "label": "Light automation"},
            ], "recommended": "AI-assisted", "confidence": 0.85},
            "monetization_model": {"options": [
                {"value": "Freemium SaaS", "label": "Freemium SaaS"},
                {"value": "Enterprise license", "label": "Enterprise license"},
            ], "recommended": "Freemium SaaS", "confidence": 0.8},
            "mvp_scope": {"options": [
                {"value": "Core features only", "label": "Core features only"},
                {"value": "Full vertical", "label": "Full vertical"},
            ], "recommended": "Core features only", "confidence": 0.85},
        }
        for field, data in fields_data.items():
            set_profile_field(state, field, data["options"], data["recommended"], data["confidence"])

        # Confirm all fields
        selections = {field: data["recommended"] for field, data in fields_data.items()}
        confirm_all_profile_fields(state, selections)
        assert is_profile_complete(state)

        # Advance to feature discovery
        advance_phase(state, "feature_discovery")
        save_state(state, slug)

        # Feature discovery
        add_feature(state, "core", "f1", "AI Requirements Extractor",
                    "Extract requirements from natural language",
                    "Core AI capability", problem_mapped_to="manual_planning", build_order=1)
        add_feature(state, "core", "f2", "Project Dashboard",
                    "Central hub for project status",
                    "Primary interface", problem_mapped_to="visibility", build_order=2)
        approve_features(state)
        advance_phase(state, "outline_generation")
        save_state(state, slug)

        # Outline generation with 10 enhanced sections
        import copy
        sections = copy.deepcopy(ENHANCED_SECTIONS)
        section_summaries = [
            "High-level product vision targeting project managers who need automated planning.",
            "Manual requirements gathering wastes 40% of sprint zero time in typical teams.",
            "Product managers create requirement documents; engineers consume structured specs.",
            "Natural language intake, profile generation, feature catalog, outline builder.",
            "GPT-4 extracts structured profiles from unstructured idea text with confidence scoring.",
            "Sub-2-second response times, 99.9% uptime, horizontal scaling via container orchestration.",
            "FastAPI backend with PostgreSQL, React frontend, Redis cache, S3 document storage.",
            "OAuth 2.0 authentication, role-based access control, SOC 2 Type II compliance.",
            "50% reduction in planning time, 90% profile accuracy, 80% user retention monthly.",
            "Phase 1 core intake, Phase 2 feature catalog, Phase 3 outline generation, Phase 4 export.",
        ]
        for s, summary in zip(sections, section_summaries):
            s["summary"] = summary
        set_outline_sections(state, sections)

        # Validate 10-section outline
        validation = run_all_checks(sections)
        assert validation["all_passed"] is True

        advance_phase(state, "outline_approval")
        save_state(state, slug)

        # Lock outline — should create 10 chapters
        lock_outline(state)
        assert is_outline_locked(state) is True
        assert len(state["chapters"]) == 10

        advance_phase(state, "chapter_build")
        save_state(state, slug)

        # Build 10 chapters
        chapter_dir = tmp_output_dir / slug / "chapters"
        chapter_dir.mkdir(parents=True, exist_ok=True)

        for i in range(1, 11):
            chapter_content = (
                f"# Chapter {i}: {sections[i-1]['title']}\n\n"
                f"## Purpose\n\n"
                f"This chapter exists to define {sections[i-1]['summary'].lower()}\n"
                f"The system requires this for complete project documentation.\n\n"
                f"## Design Intent\n\n"
                f"This approach was chosen to ensure clarity and reduce ambiguity.\n"
                f"The tradeoff is detail vs brevity - we chose detail for readability.\n\n"
                f"## Implementation Guidance\n\n"
                f"First, review the previous chapter for context.\n"
                f"Then, implement the logic described below.\n"
                f"Next, validate the output against the acceptance criteria.\n"
                f"The input is the approved data from the state file.\n"
                f"The output is a structured section of the build guide.\n"
                f"This step depends on the outline being locked.\n"
                f"The execution order is: review, implement, validate.\n"
                f"Step 1 is review. Step 2 is implementation.\n"
            )
            chapter_path = chapter_dir / f"ch{i}.md"
            chapter_path.write_text(chapter_content, encoding="utf-8")
            record_chapter_status(state, i, "draft", str(chapter_path))

            gate_results = run_chapter_gates(chapter_content)
            assert gate_results["all_passed"] is True, (
                f"Chapter {i} failed quality gates: "
                f"{[g for g, r in gate_results.items() if isinstance(r, dict) and not r.get('passed')]}"
            )
            record_chapter_status(state, i, "approved")

        assert all_chapters_approved(state) is True
        advance_phase(state, "quality_gates")
        save_state(state, slug)

        # Final quality gates
        all_chapter_text = ""
        for i in range(1, 11):
            ch_path = chapter_dir / f"ch{i}.md"
            all_chapter_text += ch_path.read_text(encoding="utf-8") + "\n\n"

        all_chapter_text += (
            "This system exists to transform project ideas into build guides. "
            "Start with phase 1 first. "
            "The definition of done is a complete, versioned build guide document. "
            "Success criteria: all quality gates pass."
        )

        final_gates = run_final_gates(all_chapter_text)
        assert final_gates["all_passed"] is True

        record_final_quality(state, {"all_passed": True, "details": []})
        advance_phase(state, "final_assembly")
        save_state(state, slug)

        # Final assembly with 10 chapters
        chapter_paths = [str(chapter_dir / f"ch{i}.md") for i in range(1, 11)]
        chapter_titles = [s["title"] for s in sections]

        result = assemble_full_document(
            chapter_paths=chapter_paths,
            chapter_titles=chapter_titles,
            project_name="Profile-Driven Test Project",
            project_slug=slug,
            version="v1",
        )

        assert Path(result["output_path"]).exists()
        record_document_assembly(state, result["filename"], result["output_path"])
        advance_phase(state, "complete")
        save_state(state, slug)

        # Final verification
        assert get_current_phase(state) == "complete"
        assert is_valid_project_state(state)
        assert len(state["chapters"]) == 10
        profile = get_project_profile(state)
        assert profile["confirmed_at"] is not None


class TestAutoBuildPipeline:
    """Test auto-build: from outline lock through auto-build to final document."""

    @patch("execution.auto_builder.generate_chapter_with_usage")
    @patch("execution.auto_builder.generate_chapter_with_retry_and_usage")
    def test_auto_build_completes_full_pipeline(self, mock_retry, mock_gen, tmp_output_dir):
        """From outline lock through auto-build to final document — zero human interaction."""
        from execution.auto_builder import run_auto_build
        from execution.chapter_writer import _fallback_chapter

        mock_gen.side_effect = lambda *a, **kw: (_fallback_chapter(a[2], f"Details about {a[2]}", a[4]), {})
        mock_retry.side_effect = lambda *a, **kw: (_fallback_chapter(a[2], f"Details about {a[2]}", a[4]), {})

        # Initialize and set up through outline approval
        state = initialize_state("Auto Build Integration Test")
        slug = state["project"]["slug"]

        record_idea(state, "Build an AI-powered project planner with auto-build")

        fields_data = {
            "problem_definition": {
                "options": [{"value": "slow", "label": "Slow planning"}],
                "recommended": "slow", "confidence": 0.9,
            },
            "target_user": {
                "options": [{"value": "pms", "label": "PMs"}],
                "recommended": "pms", "confidence": 0.85,
            },
            "value_proposition": {
                "options": [{"value": "auto", "label": "Automate"}],
                "recommended": "auto", "confidence": 0.8,
            },
            "deployment_type": {
                "options": [{"value": "saas", "label": "SaaS"}],
                "recommended": "saas", "confidence": 0.9,
            },
            "ai_depth": {
                "options": [{"value": "ai", "label": "AI-assisted"}],
                "recommended": "ai", "confidence": 0.85,
            },
            "monetization_model": {
                "options": [{"value": "freemium", "label": "Freemium"}],
                "recommended": "freemium", "confidence": 0.8,
            },
            "mvp_scope": {
                "options": [{"value": "core", "label": "Core only"}],
                "recommended": "core", "confidence": 0.85,
            },
        }
        for field, data in fields_data.items():
            set_profile_field(state, field, data["options"], data["recommended"], data["confidence"])
        selections = {field: data["recommended"] for field, data in fields_data.items()}
        confirm_all_profile_fields(state, selections)

        advance_phase(state, "feature_discovery")
        add_feature(state, "core", "f1", "Feature One", "First feature",
                    "Core", problem_mapped_to="slow", build_order=1)
        add_feature(state, "core", "f2", "Feature Two", "Second feature",
                    "Core", problem_mapped_to="slow", build_order=2)
        approve_features(state)

        advance_phase(state, "outline_generation")
        sections = [
            {"index": 1, "title": "Executive Summary", "type": "required",
             "summary": "High-level overview of the project."},
            {"index": 2, "title": "Functional Requirements", "type": "required",
             "summary": "Detailed specifications of capabilities."},
            {"index": 3, "title": "Technical Architecture", "type": "required",
             "summary": "System design and data flow."},
        ]
        set_outline_sections(state, sections)

        advance_phase(state, "outline_approval")
        lock_outline(state)
        set_build_depth_mode(state, "light")
        advance_phase(state, "chapter_build")
        save_state(state, slug)

        # Run auto-build — this is the zero-human-interaction part (light mode = legacy path)
        events = list(run_auto_build(state, slug))

        # Verify complete
        assert events[-1].event_type == "complete"
        assert get_current_phase(state) == "complete"

        # Verify all chapters approved with content files
        for chapter in state["chapters"]:
            assert chapter["status"] == "approved"
            assert chapter["content_path"] is not None
            assert Path(chapter["content_path"]).exists()

        # Verify final document exists
        assert state["document"]["output_path"] is not None
        assert Path(state["document"]["output_path"]).exists()
        assert state["document"]["filename"].endswith(".md")

        # Verify state can be reloaded
        reloaded = load_state(slug)
        assert reloaded["current_phase"] == "complete"
        assert reloaded["document"]["filename"] == state["document"]["filename"]
