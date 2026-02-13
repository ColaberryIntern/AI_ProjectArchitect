"""Unit tests for execution/state_manager.py."""

import json
from pathlib import Path

import pytest

from execution.state_manager import (
    PROFILE_REQUIRED_FIELDS,
    _hash_outline,
    _slugify,
    add_extracted_features,
    add_feature,
    advance_phase,
    all_chapters_approved,
    approve_features,
    approve_ideation,
    complete_ideation_dimension,
    confirm_all_profile_fields,
    confirm_profile_field,
    delete_project,
    get_build_depth_mode,
    get_chapter,
    get_current_phase,
    get_extracted_features,
    get_project_profile,
    get_revision_count,
    initialize_state,
    is_outline_locked,
    is_profile_complete,
    load_state,
    lock_outline,
    record_chapter_quality,
    record_chapter_score,
    record_chapter_status,
    record_document_assembly,
    record_final_quality,
    record_idea,
    record_ideation_response,
    record_outline_decision,
    save_state,
    set_build_depth_mode,
    set_outline_sections,
    set_profile_derived,
    set_profile_field,
    unlock_outline,
    verify_outline_integrity,
)


class TestSlugify:
    def test_simple_name(self):
        assert _slugify("Test Project") == "test-project"

    def test_special_characters(self):
        assert _slugify("My Cool Project!") == "my-cool-project"

    def test_multiple_spaces(self):
        assert _slugify("A   B   C") == "a-b-c"

    def test_leading_trailing_spaces(self):
        assert _slugify("  hello  ") == "hello"

    def test_numbers(self):
        assert _slugify("Project 123") == "project-123"


class TestDeleteProject:
    def test_delete_existing_project(self, tmp_output_dir):
        state = initialize_state("Delete Me")
        slug = state["project"]["slug"]
        assert (tmp_output_dir / slug).exists()
        result = delete_project(slug)
        assert result is True
        assert not (tmp_output_dir / slug).exists()

    def test_delete_nonexistent_returns_false(self, tmp_output_dir):
        result = delete_project("nonexistent-project")
        assert result is False

    def test_delete_path_traversal_raises_valueerror(self, tmp_output_dir):
        with pytest.raises(ValueError, match="Invalid project slug"):
            delete_project("../../../etc")

    def test_delete_permission_error_raises_oserror(self, tmp_output_dir, monkeypatch):
        state = initialize_state("Locked Project")
        slug = state["project"]["slug"]
        import shutil
        original_rmtree = shutil.rmtree
        def mock_rmtree(path, **kwargs):
            raise PermissionError("File is locked")
        monkeypatch.setattr(shutil, "rmtree", mock_rmtree)
        # Also patch the module-level reference
        import execution.state_manager as sm
        monkeypatch.setattr(sm.shutil, "rmtree", mock_rmtree)
        with pytest.raises(OSError, match="files are locked"):
            delete_project(slug)


class TestInitializeState:
    def test_creates_state_file(self, tmp_output_dir):
        state = initialize_state("My Test Project")
        path = tmp_output_dir / "my-test-project" / "project_state.json"
        assert path.exists()

    def test_required_fields_present(self, tmp_output_dir):
        state = initialize_state("My Project")
        assert "project" in state
        assert "current_phase" in state
        assert "idea" in state
        assert "ideation" in state
        assert "features" in state
        assert "outline" in state
        assert "chapters" in state
        assert "quality" in state
        assert "document" in state
        assert "version_history" in state

    def test_initial_phase(self, tmp_output_dir):
        state = initialize_state("My Project")
        assert state["current_phase"] == "idea_intake"

    def test_project_metadata(self, tmp_output_dir):
        state = initialize_state("My Test Project")
        assert state["project"]["name"] == "My Test Project"
        assert state["project"]["slug"] == "my-test-project"
        assert state["project"]["created_at"] is not None
        assert state["project"]["updated_at"] is not None

    def test_timestamps_set(self, tmp_output_dir):
        state = initialize_state("Timestamped Project")
        assert "T" in state["project"]["created_at"]  # ISO 8601 format
        assert len(state["version_history"]) == 1


class TestLoadAndSaveState:
    def test_round_trip(self, tmp_output_dir, sample_state):
        slug = sample_state["project"]["slug"]
        (tmp_output_dir / slug).mkdir(parents=True, exist_ok=True)
        save_state(sample_state, slug)
        loaded = load_state(slug)
        assert loaded["project"]["name"] == sample_state["project"]["name"]
        assert loaded["current_phase"] == sample_state["current_phase"]

    def test_load_missing_file(self, tmp_output_dir):
        with pytest.raises(FileNotFoundError):
            load_state("nonexistent-project")

    def test_save_creates_directory(self, tmp_output_dir, sample_state):
        slug = "new-project-dir"
        sample_state["project"]["slug"] = slug
        save_state(sample_state, slug)
        assert (tmp_output_dir / slug / "project_state.json").exists()

    def test_save_updates_timestamp(self, tmp_output_dir, sample_state):
        slug = sample_state["project"]["slug"]
        original_time = sample_state["project"]["updated_at"]
        save_state(sample_state, slug)
        loaded = load_state(slug)
        assert loaded["project"]["updated_at"] != original_time


class TestGetCurrentPhase:
    def test_returns_phase(self, sample_state):
        assert get_current_phase(sample_state) == "idea_intake"

    def test_after_advance(self, sample_state):
        advance_phase(sample_state, "feature_discovery")
        assert get_current_phase(sample_state) == "feature_discovery"


class TestAdvancePhase:
    def test_valid_transition(self, sample_state):
        result = advance_phase(sample_state, "feature_discovery")
        assert result["current_phase"] == "feature_discovery"

    def test_skip_phase_rejected(self, sample_state):
        with pytest.raises(ValueError, match="Invalid transition"):
            advance_phase(sample_state, "outline_generation")

    def test_backward_transition_rejected(self, sample_state):
        advance_phase(sample_state, "feature_discovery")
        with pytest.raises(ValueError, match="Invalid transition"):
            advance_phase(sample_state, "idea_intake")

    def test_invalid_phase_name(self, sample_state):
        with pytest.raises(ValueError, match="Invalid phase"):
            advance_phase(sample_state, "nonexistent_phase")

    def test_sequential_transitions(self, sample_state):
        advance_phase(sample_state, "feature_discovery")
        advance_phase(sample_state, "outline_generation")
        advance_phase(sample_state, "outline_approval")
        assert sample_state["current_phase"] == "outline_approval"


class TestRecordIdea:
    def test_captures_idea(self, sample_state):
        record_idea(sample_state, "Build an AI assistant for project planning")
        assert (
            sample_state["idea"]["original_raw"]
            == "Build an AI assistant for project planning"
        )
        assert sample_state["idea"]["captured_at"] is not None


class TestIdeation:
    def test_record_response(self, sample_state):
        record_ideation_response(
            sample_state,
            "business_model",
            "Who benefits from this?",
            "Internal teams managing projects",
        )
        responses = sample_state["ideation"]["business_model"]["responses"]
        assert len(responses) == 1
        assert responses[0]["question"] == "Who benefits from this?"
        assert responses[0]["answer"] == "Internal teams managing projects"

    def test_invalid_dimension(self, sample_state):
        with pytest.raises(ValueError, match="Invalid dimension"):
            record_ideation_response(sample_state, "invalid", "q?", "a")

    def test_complete_dimension(self, sample_state):
        complete_ideation_dimension(
            sample_state, "business_model", "Internal tool for team productivity"
        )
        dim = sample_state["ideation"]["business_model"]
        assert dim["status"] == "answered"
        assert dim["summary"] == "Internal tool for team productivity"

    def test_approve_ideation(self, sample_state):
        summary = "For: internal teams. Problem: manual planning. AI: question generation."
        approve_ideation(sample_state, summary)
        assert sample_state["ideation"]["approved"] is True
        assert sample_state["ideation"]["ideation_summary"] == summary


class TestFeatures:
    def test_add_core_feature(self, sample_state):
        add_feature(
            sample_state,
            "core",
            "feat-001",
            "Guided Questioning",
            "Ask structured questions",
            "Eliminates vagueness",
            problem_mapped_to="idea_vagueness",
            build_order=1,
        )
        assert len(sample_state["features"]["core"]) == 1
        assert sample_state["features"]["core"][0]["name"] == "Guided Questioning"

    def test_add_optional_feature(self, sample_state):
        add_feature(
            sample_state,
            "optional",
            "feat-opt-001",
            "PDF Export",
            "Export as PDF",
            "Distribution to non-technical stakeholders",
            deferred=True,
            defer_reason="Not MVP",
        )
        assert len(sample_state["features"]["optional"]) == 1
        assert sample_state["features"]["optional"][0]["deferred"] is True

    def test_invalid_feature_type(self, sample_state):
        with pytest.raises(ValueError, match="feature_type must be"):
            add_feature(sample_state, "invalid", "x", "x", "x", "x")

    def test_approve_features(self, sample_state):
        approve_features(sample_state)
        assert sample_state["features"]["approved"] is True


class TestOutline:
    def test_set_sections(self, sample_state, sample_outline_sections):
        set_outline_sections(sample_state, sample_outline_sections)
        assert len(sample_state["outline"]["sections"]) == 7

    def test_lock_outline(self, sample_state, sample_outline_sections):
        set_outline_sections(sample_state, sample_outline_sections)
        lock_outline(sample_state)
        assert sample_state["outline"]["status"] == "approved"
        assert sample_state["outline"]["locked_at"] is not None
        assert sample_state["outline"]["locked_hash"] is not None
        assert len(sample_state["chapters"]) == 7

    def test_lock_creates_chapters(self, sample_state, sample_outline_sections):
        set_outline_sections(sample_state, sample_outline_sections)
        lock_outline(sample_state)
        for i, ch in enumerate(sample_state["chapters"]):
            assert ch["index"] == i + 1
            assert ch["status"] == "pending"
            assert ch["revision_count"] == 0

    def test_lock_empty_outline_fails(self, sample_state):
        with pytest.raises(ValueError, match="no sections"):
            lock_outline(sample_state)

    def test_unlock_outline(self, sample_state, sample_outline_sections):
        set_outline_sections(sample_state, sample_outline_sections)
        lock_outline(sample_state)
        unlock_outline(sample_state, "Need to add security section")
        assert sample_state["outline"]["status"] == "unlocked"
        assert sample_state["outline"]["locked_hash"] is None
        assert sample_state["outline"]["version"] == 2

    def test_unlock_not_locked_fails(self, sample_state):
        with pytest.raises(ValueError, match="not approved"):
            unlock_outline(sample_state, "reason")

    def test_record_outline_decision(self, sample_state):
        record_outline_decision(sample_state, "revise", "Section 3 needs more detail")
        history = sample_state["outline"]["approval_history"]
        assert len(history) == 1
        assert history[0]["decision"] == "revise"

    def test_invalid_outline_decision(self, sample_state):
        with pytest.raises(ValueError, match="Invalid decision"):
            record_outline_decision(sample_state, "invalid")

    def test_is_outline_locked(self, sample_state, sample_outline_sections):
        assert is_outline_locked(sample_state) is False
        set_outline_sections(sample_state, sample_outline_sections)
        lock_outline(sample_state)
        assert is_outline_locked(sample_state) is True

    def test_verify_integrity(self, sample_state, sample_outline_sections):
        set_outline_sections(sample_state, sample_outline_sections)
        lock_outline(sample_state)
        assert verify_outline_integrity(sample_state) is True

    def test_integrity_fails_on_modification(self, sample_state, sample_outline_sections):
        set_outline_sections(sample_state, sample_outline_sections)
        lock_outline(sample_state)
        sample_state["outline"]["sections"][0]["title"] = "Modified Title"
        assert verify_outline_integrity(sample_state) is False


class TestChapters:
    @pytest.fixture
    def state_with_chapters(self, sample_state, sample_outline_sections):
        set_outline_sections(sample_state, sample_outline_sections)
        lock_outline(sample_state)
        return sample_state

    def test_get_chapter(self, state_with_chapters):
        ch = get_chapter(state_with_chapters, 1)
        assert ch is not None
        assert ch["outline_section"] == "System Purpose & Context"

    def test_get_chapter_not_found(self, state_with_chapters):
        assert get_chapter(state_with_chapters, 99) is None

    def test_record_chapter_status_draft(self, state_with_chapters):
        record_chapter_status(
            state_with_chapters, 1, "draft", "output/test/ch1.md"
        )
        ch = get_chapter(state_with_chapters, 1)
        assert ch["status"] == "draft"
        assert ch["content_path"] == "output/test/ch1.md"

    def test_record_chapter_approved(self, state_with_chapters):
        record_chapter_status(state_with_chapters, 1, "approved")
        ch = get_chapter(state_with_chapters, 1)
        assert ch["status"] == "approved"
        assert ch["approved_at"] is not None

    def test_record_chapter_revision(self, state_with_chapters):
        record_chapter_status(state_with_chapters, 1, "revision_1")
        ch = get_chapter(state_with_chapters, 1)
        assert ch["revision_count"] == 1

    def test_chapter_not_found(self, state_with_chapters):
        with pytest.raises(ValueError, match="not found"):
            record_chapter_status(state_with_chapters, 99, "draft")

    def test_invalid_chapter_status(self, state_with_chapters):
        with pytest.raises(ValueError, match="Invalid status"):
            record_chapter_status(state_with_chapters, 1, "invalid")

    def test_get_revision_count(self, state_with_chapters):
        assert get_revision_count(state_with_chapters, 1) == 0
        record_chapter_status(state_with_chapters, 1, "revision_1")
        assert get_revision_count(state_with_chapters, 1) == 1

    def test_revision_count_not_found(self, state_with_chapters):
        with pytest.raises(ValueError, match="not found"):
            get_revision_count(state_with_chapters, 99)

    def test_record_chapter_quality(self, state_with_chapters):
        report = {
            "completeness": "pass",
            "clarity": "pass",
            "build_readiness": "fail",
            "anti_vagueness": "pass",
            "intern_test": "fail",
            "details": [{"gate": "build_readiness", "issue": "Missing execution order"}],
        }
        record_chapter_quality(state_with_chapters, 1, report)
        ch = get_chapter(state_with_chapters, 1)
        assert ch["quality_report"]["completeness"] == "pass"
        assert ch["quality_report"]["build_readiness"] == "fail"

    def test_all_chapters_approved_false(self, state_with_chapters):
        assert all_chapters_approved(state_with_chapters) is False

    def test_all_chapters_approved_true(self, state_with_chapters):
        for ch in state_with_chapters["chapters"]:
            ch["status"] = "approved"
        assert all_chapters_approved(state_with_chapters) is True

    def test_all_chapters_approved_empty(self, sample_state):
        assert all_chapters_approved(sample_state) is False


class TestExtractedFeatures:
    def test_initial_state_has_empty_list(self, tmp_output_dir):
        state = initialize_state("Feature Test")
        assert state["ideation"]["extracted_features"] == []

    def test_get_extracted_features_empty(self, sample_state):
        assert get_extracted_features(sample_state) == []

    def test_add_features(self, sample_state):
        features = [
            {"name": "User auth", "description": "Login and registration"},
            {"name": "Dashboard", "description": "Main overview page"},
        ]
        add_extracted_features(sample_state, features)
        result = get_extracted_features(sample_state)
        assert len(result) == 2
        assert result[0]["name"] == "User auth"
        assert result[1]["name"] == "Dashboard"

    def test_dedup_by_name(self, sample_state):
        features1 = [{"name": "User auth", "description": "Login"}]
        features2 = [{"name": "User auth", "description": "Different desc"}, {"name": "Search", "description": "Find things"}]
        add_extracted_features(sample_state, features1)
        add_extracted_features(sample_state, features2)
        result = get_extracted_features(sample_state)
        assert len(result) == 2
        names = [f["name"] for f in result]
        assert "User auth" in names
        assert "Search" in names

    def test_case_insensitive_dedup(self, sample_state):
        features1 = [{"name": "User Auth", "description": "Login"}]
        features2 = [{"name": "user auth", "description": "Different"}]
        add_extracted_features(sample_state, features1)
        add_extracted_features(sample_state, features2)
        result = get_extracted_features(sample_state)
        assert len(result) == 1

    def test_backwards_compat_no_key(self, sample_state):
        # Simulate old state without extracted_features
        if "extracted_features" in sample_state.get("ideation", {}):
            del sample_state["ideation"]["extracted_features"]
        assert get_extracted_features(sample_state) == []

    def test_skips_empty_names(self, sample_state):
        features = [{"name": "", "description": "No name"}, {"name": "Valid", "description": "Has name"}]
        add_extracted_features(sample_state, features)
        result = get_extracted_features(sample_state)
        assert len(result) == 1
        assert result[0]["name"] == "Valid"


class TestFinalQualityAndDocument:
    def test_record_final_quality(self, sample_state):
        report = {"all_passed": True, "details": []}
        record_final_quality(sample_state, report)
        assert sample_state["quality"]["final_report"]["all_passed"] is True
        assert sample_state["quality"]["final_report"]["ran_at"] is not None

    def test_record_document_assembly(self, sample_state):
        record_document_assembly(
            sample_state,
            "Test_Project_Build_Guide_v1.md",
            "output/test-project/Test_Project_Build_Guide_v1.md",
        )
        assert (
            sample_state["document"]["filename"]
            == "Test_Project_Build_Guide_v1.md"
        )
        assert sample_state["document"]["assembled_at"] is not None


class TestProjectProfile:
    """Tests for project_profile state management."""

    def test_initialize_state_includes_profile(self, tmp_output_dir):
        state = initialize_state("Profile Test")
        assert "project_profile" in state
        profile = state["project_profile"]
        for field in PROFILE_REQUIRED_FIELDS:
            assert field in profile
            assert profile[field]["selected"] is None
            assert profile[field]["confirmed"] is False
            assert profile[field]["options"] == []

    def test_blank_profile_has_all_list_fields(self, tmp_output_dir):
        state = initialize_state("Profile Test")
        profile = state["project_profile"]
        assert profile["technical_constraints"] == []
        assert profile["non_functional_requirements"] == []
        assert profile["success_metrics"] == []
        assert profile["risk_assessment"] == []
        assert profile["core_use_cases"] == []
        assert profile["selected_features"] == []
        assert profile["generated_at"] is None
        assert profile["confirmed_at"] is None

    def test_ensure_project_profile_backward_compat(self, sample_state):
        # Old state without project_profile
        assert "project_profile" not in sample_state
        profile = get_project_profile(sample_state)
        assert "project_profile" in sample_state
        for field in PROFILE_REQUIRED_FIELDS:
            assert field in profile
            assert profile[field]["confirmed"] is False

    def test_get_project_profile_idempotent(self, sample_state):
        profile1 = get_project_profile(sample_state)
        profile1["problem_definition"]["selected"] = "test"
        profile2 = get_project_profile(sample_state)
        assert profile2["problem_definition"]["selected"] == "test"

    def test_set_profile_field(self, sample_state):
        options = [
            {"value": "saas", "label": "SaaS", "description": "Multi-tenant SaaS"},
            {"value": "internal", "label": "Internal", "description": "Internal tool"},
        ]
        set_profile_field(sample_state, "deployment_type", options, "saas", 0.9)
        profile = get_project_profile(sample_state)
        assert profile["deployment_type"]["selected"] == "saas"
        assert profile["deployment_type"]["confidence"] == 0.9
        assert profile["deployment_type"]["confirmed"] is False
        assert len(profile["deployment_type"]["options"]) == 2

    def test_set_profile_field_invalid(self, sample_state):
        with pytest.raises(ValueError, match="Invalid profile field"):
            set_profile_field(sample_state, "invalid_field", [], None, 0.0)

    def test_confirm_profile_field(self, sample_state):
        set_profile_field(sample_state, "ai_depth", [], "ai_assisted", 0.8)
        confirm_profile_field(sample_state, "ai_depth", "predictive_ml")
        profile = get_project_profile(sample_state)
        assert profile["ai_depth"]["selected"] == "predictive_ml"
        assert profile["ai_depth"]["confirmed"] is True

    def test_confirm_profile_field_invalid(self, sample_state):
        with pytest.raises(ValueError, match="Invalid profile field"):
            confirm_profile_field(sample_state, "bad_field", "value")

    def test_confirm_all_profile_fields(self, sample_state):
        # Initialize profile first
        get_project_profile(sample_state)
        selections = {
            "problem_definition": "Users need planning tools",
            "target_user": "Business analysts",
            "value_proposition": "AI-powered requirements",
            "deployment_type": "SaaS",
            "ai_depth": "AI-assisted",
            "monetization_model": "Freemium",
            "mvp_scope": "Core features only",
        }
        confirm_all_profile_fields(sample_state, selections)
        profile = get_project_profile(sample_state)
        for field in PROFILE_REQUIRED_FIELDS:
            assert profile[field]["confirmed"] is True
            assert profile[field]["selected"] == selections[field]
        assert profile["confirmed_at"] is not None

    def test_confirm_all_missing_field_raises(self, sample_state):
        get_project_profile(sample_state)
        selections = {
            "problem_definition": "value",
            "target_user": "value",
            # Missing other fields
        }
        with pytest.raises(ValueError, match="Missing required profile fields"):
            confirm_all_profile_fields(sample_state, selections)

    def test_confirm_all_empty_value_raises(self, sample_state):
        get_project_profile(sample_state)
        selections = {f: "value" for f in PROFILE_REQUIRED_FIELDS}
        selections["ai_depth"] = ""  # Empty value
        with pytest.raises(ValueError, match="Missing required profile fields"):
            confirm_all_profile_fields(sample_state, selections)

    def test_is_profile_complete_false_initially(self, sample_state):
        assert is_profile_complete(sample_state) is False

    def test_is_profile_complete_partial(self, sample_state):
        get_project_profile(sample_state)
        confirm_profile_field(sample_state, "problem_definition", "value")
        confirm_profile_field(sample_state, "target_user", "value")
        assert is_profile_complete(sample_state) is False

    def test_is_profile_complete_all_confirmed(self, sample_state):
        get_project_profile(sample_state)
        selections = {f: f"value_{f}" for f in PROFILE_REQUIRED_FIELDS}
        confirm_all_profile_fields(sample_state, selections)
        assert is_profile_complete(sample_state) is True

    def test_set_profile_derived(self, sample_state):
        get_project_profile(sample_state)
        set_profile_derived(
            sample_state,
            technical_constraints=["REST API", "Modern browsers"],
            nfrs=["99.9% uptime"],
            success_metrics=["50% faster planning"],
            risk_assessment=["LLM dependency"],
            core_use_cases=["Submit idea", "Review profile"],
        )
        profile = get_project_profile(sample_state)
        assert profile["technical_constraints"] == ["REST API", "Modern browsers"]
        assert profile["non_functional_requirements"] == ["99.9% uptime"]
        assert profile["success_metrics"] == ["50% faster planning"]
        assert profile["risk_assessment"] == ["LLM dependency"]
        assert profile["core_use_cases"] == ["Submit idea", "Review profile"]
        assert profile["generated_at"] is not None

    def test_profile_persists_through_save_load(self, tmp_output_dir, sample_state):
        slug = sample_state["project"]["slug"]
        get_project_profile(sample_state)
        selections = {f: f"value_{f}" for f in PROFILE_REQUIRED_FIELDS}
        confirm_all_profile_fields(sample_state, selections)
        save_state(sample_state, slug)
        loaded = load_state(slug)
        assert is_profile_complete(loaded) is True
        assert loaded["project_profile"]["problem_definition"]["selected"] == "value_problem_definition"


# ---------------------------------------------------------------------------
# Build Depth Mode
# ---------------------------------------------------------------------------

class TestBuildDepthMode:
    def test_default_is_professional(self, sample_state):
        assert get_build_depth_mode(sample_state) == "professional"

    def test_set_and_get(self, sample_state):
        set_build_depth_mode(sample_state, "light")
        assert get_build_depth_mode(sample_state) == "light"

    def test_set_all_valid_modes(self, sample_state):
        for mode in ("light", "standard", "professional", "enterprise"):
            set_build_depth_mode(sample_state, mode)
            assert get_build_depth_mode(sample_state) == mode

    def test_invalid_mode_raises(self, sample_state):
        with pytest.raises(ValueError, match="Invalid depth mode"):
            set_build_depth_mode(sample_state, "extreme")

    def test_persists_through_save_load(self, tmp_output_dir, sample_state):
        slug = sample_state["project"]["slug"]
        set_build_depth_mode(sample_state, "enterprise")
        save_state(sample_state, slug)
        loaded = load_state(slug)
        assert get_build_depth_mode(loaded) == "enterprise"

    def test_old_state_without_field_defaults(self, sample_state):
        """Existing projects without build_depth_mode should default to professional."""
        profile = sample_state.get("project_profile", {})
        profile.pop("build_depth_mode", None)
        assert get_build_depth_mode(sample_state) == "professional"

    def test_alias_lite_resolves_to_light(self, sample_state):
        """Setting 'lite' should resolve and store as 'light'."""
        set_build_depth_mode(sample_state, "lite")
        assert get_build_depth_mode(sample_state) == "light"

    def test_alias_architect_resolves_to_enterprise(self, sample_state):
        """Setting 'architect' should resolve and store as 'enterprise'."""
        set_build_depth_mode(sample_state, "architect")
        assert get_build_depth_mode(sample_state) == "enterprise"

    def test_reading_old_alias_resolves(self, sample_state):
        """If old state stored 'lite', reading should resolve to 'light'."""
        profile = get_project_profile(sample_state)
        profile["build_depth_mode"] = "lite"
        assert get_build_depth_mode(sample_state) == "light"


# ---------------------------------------------------------------------------
# Chapter Score
# ---------------------------------------------------------------------------

class TestRecordChapterScore:
    def test_records_score(self, sample_state):
        set_outline_sections(sample_state, [
            {"index": 1, "title": "Ch1", "type": "required", "summary": "s1"},
        ])
        lock_outline(sample_state)
        score = {"total_score": 82, "word_count": 3200, "status": "complete"}
        record_chapter_score(sample_state, 1, score)
        ch = get_chapter(sample_state, 1)
        assert ch["chapter_score"]["total_score"] == 82

    def test_nonexistent_chapter_raises(self, sample_state):
        set_outline_sections(sample_state, [
            {"index": 1, "title": "Ch1", "type": "required", "summary": "s1"},
        ])
        lock_outline(sample_state)
        with pytest.raises(ValueError, match="Chapter 99 not found"):
            record_chapter_score(sample_state, 99, {"total_score": 50})


class TestIntelligenceGoals:
    """Tests for intelligence goals in state manager."""

    def test_blank_profile_has_intelligence_goals(self, sample_state):
        profile = get_project_profile(sample_state)
        assert "intelligence_goals" in profile
        assert profile["intelligence_goals"] == []

    def test_set_intelligence_goals(self, sample_state):
        from execution.state_manager import set_intelligence_goals
        goals = [
            {"id": "g1", "user_facing_label": "Predict churn", "goal_type": "prediction"},
            {"id": "g2", "user_facing_label": "Classify tickets", "goal_type": "classification"},
        ]
        set_intelligence_goals(sample_state, goals)
        profile = get_project_profile(sample_state)
        assert len(profile["intelligence_goals"]) == 2
        assert profile["intelligence_goals"][0]["id"] == "g1"
        assert profile["intelligence_goals"][0]["user_facing_label"] == "Predict churn"

    def test_set_intelligence_goals_normalizes_old_fields(self, sample_state):
        """Setting goals with old field names should normalize to canonical names."""
        from execution.state_manager import set_intelligence_goals
        goals = [
            {"id": "g1", "label": "Predict churn", "type": "prediction", "confidence_level": "high_confidence"},
        ]
        set_intelligence_goals(sample_state, goals)
        profile = get_project_profile(sample_state)
        stored = profile["intelligence_goals"][0]
        assert stored["user_facing_label"] == "Predict churn"
        assert stored["goal_type"] == "prediction"
        assert stored["confidence_required"] == "high_confidence"
        assert "label" not in stored or stored.get("user_facing_label")  # canonical name present

    def test_get_intelligence_goals(self, sample_state):
        from execution.state_manager import get_intelligence_goals, set_intelligence_goals
        set_intelligence_goals(sample_state, [{"id": "g1", "user_facing_label": "Goal", "goal_type": "prediction"}])
        result = get_intelligence_goals(sample_state)
        assert len(result) == 1
        assert result[0]["user_facing_label"] == "Goal"

    def test_get_intelligence_goals_normalizes_old_format(self, sample_state):
        """Reading goals stored in old format should return canonical field names."""
        from execution.state_manager import get_intelligence_goals
        profile = get_project_profile(sample_state)
        # Simulate old-format data already stored
        profile["intelligence_goals"] = [
            {"id": "g1", "label": "Old Goal", "type": "classification", "confidence_level": "business_reliable"},
        ]
        result = get_intelligence_goals(sample_state)
        assert result[0]["user_facing_label"] == "Old Goal"
        assert result[0]["goal_type"] == "classification"
        assert result[0]["confidence_required"] == "business_reliable"

    def test_get_intelligence_goals_empty_by_default(self, sample_state):
        from execution.state_manager import get_intelligence_goals
        result = get_intelligence_goals(sample_state)
        assert result == []

    def test_initialize_state_includes_intelligence_goals(self, tmp_output_dir):
        state = initialize_state("Test Goals Project")
        profile = get_project_profile(state)
        assert "intelligence_goals" in profile
        assert profile["intelligence_goals"] == []

    def test_normalize_goal_data_maps_old_fields(self):
        from execution.state_manager import normalize_goal_data
        old = {"id": "g1", "label": "My Goal", "type": "prediction", "confidence_level": "high_confidence", "description": "Desc"}
        result = normalize_goal_data(old)
        assert result["user_facing_label"] == "My Goal"
        assert result["goal_type"] == "prediction"
        assert result["confidence_required"] == "high_confidence"
        assert result["impact_level"] is None
        assert result["description"] == "Desc"

    def test_normalize_goal_data_preserves_new_fields(self):
        from execution.state_manager import normalize_goal_data
        new = {
            "id": "g1", "user_facing_label": "My Goal", "goal_type": "recommendation",
            "confidence_required": "critical_accuracy", "impact_level": "high",
            "description": "Desc",
        }
        result = normalize_goal_data(new)
        assert result == new

    def test_normalize_goal_data_handles_non_dict(self):
        from execution.state_manager import normalize_goal_data
        assert normalize_goal_data("not a dict") == "not a dict"
        assert normalize_goal_data(None) is None
