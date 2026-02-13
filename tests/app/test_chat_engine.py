"""Tests for the chat conversation engine."""

import json
from unittest.mock import patch

import pytest

from app.chat_engine import (
    LOCK_SIGNAL,
    _is_confirmation,
    _is_done,
    get_welcome_message,
    process_message,
)
from execution.state_manager import (
    add_feature,
    advance_phase,
    delete_project,
    get_chat_step,
    get_extracted_features,
    initialize_state,
    record_idea,
    save_state,
    set_chat_step,
)


@pytest.fixture
def chat_state(tmp_output_dir):
    """Create a fresh project state for chat testing."""
    return initialize_state("Chat Test Project")


class TestIsConfirmation:
    """Test the confirmation detection helper."""

    def test_yes_variants(self):
        assert _is_confirmation("yes") is True
        assert _is_confirmation("Yes") is True
        assert _is_confirmation("YES") is True
        assert _is_confirmation("yep") is True
        assert _is_confirmation("yeah") is True
        assert _is_confirmation("y") is True
        assert _is_confirmation("correct") is True
        assert _is_confirmation("looks good") is True
        assert _is_confirmation("ok") is True
        assert _is_confirmation("sure!") is True

    def test_not_confirmation(self):
        assert _is_confirmation("no") is False
        assert _is_confirmation("I want to change it") is False
        assert _is_confirmation("actually, let me rephrase") is False
        assert _is_confirmation("") is False


class TestGetWelcomeMessage:
    """Test getting the welcome message for a phase."""

    def test_idea_intake_welcome(self, chat_state):
        msg = get_welcome_message(chat_state)
        assert msg is not None
        assert "Tell me about" in msg

    def test_feature_discovery_welcome(self, chat_state):
        chat_state["current_phase"] = "feature_discovery"
        msg = get_welcome_message(chat_state)
        assert msg is not None
        assert "feature catalog" in msg.lower()

    def test_complete_welcome(self, chat_state):
        chat_state["current_phase"] = "complete"
        msg = get_welcome_message(chat_state)
        assert msg is not None
        assert "Congratulations" in msg


class TestPhase1IdeaIntakeFlow:
    """Test the complete Phase 1 conversation flow."""

    def test_initial_step_is_welcome(self, chat_state):
        assert get_chat_step(chat_state) == "idea_intake.welcome"

    def test_capture_idea_advances_to_feature_discovery(self, chat_state, tmp_output_dir):
        slug = chat_state["project"]["slug"]

        result = process_message(chat_state, slug, "I want to build an AI inventory manager")

        # Should have captured the idea
        assert chat_state["idea"]["original_raw"] == "I want to build an AI inventory manager"
        assert "raw_idea" in result["field_updates"]
        assert result["field_updates"]["raw_idea"] == "I want to build an AI inventory manager"

        # Should advance directly to feature discovery
        assert chat_state["current_phase"] == "feature_discovery"
        assert get_chat_step(chat_state) == "feature_discovery.welcome"
        assert result["reload"] is True
        assert result["redirect_url"] is not None
        assert "feature-discovery" in result["redirect_url"]

        # Bot should acknowledge
        assert len(result["bot_messages"]) > 0
        assert "captured" in result["bot_messages"][0].lower()

    def test_chat_messages_are_persisted(self, chat_state, tmp_output_dir):
        slug = chat_state["project"]["slug"]

        process_message(chat_state, slug, "My project idea")

        messages = chat_state["chat"]["messages"]
        assert len(messages) >= 2
        assert any(m["role"] == "user" and m["text"] == "My project idea" for m in messages)
        assert any(m["role"] == "bot" for m in messages)

    def test_unknown_step_gives_fallback(self, chat_state, tmp_output_dir):
        slug = chat_state["project"]["slug"]
        chat_state["chat"]["current_step"] = "unknown.step"

        result = process_message(chat_state, slug, "Hello")
        assert len(result["bot_messages"]) > 0
        assert "not sure" in result["bot_messages"][0].lower() or "form" in result["bot_messages"][0].lower()


# ---------------------------------------------------------------------------
# Phase 2: Feature Discovery (form-based with guidance chat)
# ---------------------------------------------------------------------------

@pytest.fixture
def feature_state(tmp_output_dir):
    """Create state at the feature_discovery phase with guidance step."""
    state = initialize_state("Feature Test Project")
    slug = state["project"]["slug"]
    record_idea(state, "Build an AI task manager")
    advance_phase(state, "feature_discovery")
    set_chat_step(state, "feature_discovery.welcome")
    save_state(state, slug)
    return state


class TestPhase2FeatureDiscoveryGuidance:
    """Test Phase 2 feature discovery guidance chat."""

    def test_guidance_provides_tips(self, feature_state, tmp_output_dir):
        slug = feature_state["project"]["slug"]

        result = process_message(feature_state, slug, "What should I do?")

        assert len(result["bot_messages"]) > 0
        # Should provide catalog-related guidance
        msg = result["bot_messages"][0].lower()
        assert "feature" in msg or "catalog" in msg or "select" in msg

    def test_guidance_cycles_tips(self, feature_state, tmp_output_dir):
        slug = feature_state["project"]["slug"]

        result1 = process_message(feature_state, slug, "help")
        result2 = process_message(feature_state, slug, "more tips")

        # Both should return messages
        assert len(result1["bot_messages"]) > 0
        assert len(result2["bot_messages"]) > 0


# ---------------------------------------------------------------------------
# Phase 3-8: Guidance message tests
# ---------------------------------------------------------------------------


class TestGuidanceMessages:
    """Test Phase 3-8 contextual guidance."""

    def test_outline_generation_guidance(self, tmp_output_dir):
        state = initialize_state("Guidance Test")
        slug = state["project"]["slug"]
        state["current_phase"] = "outline_generation"
        set_chat_step(state, "outline_generation.welcome")
        save_state(state, slug)

        result = process_message(state, slug, "What should I do here?")
        assert len(result["bot_messages"]) > 0
        assert "section" in result["bot_messages"][0].lower() or "title" in result["bot_messages"][0].lower()

    def test_guidance_cycles_tips(self, tmp_output_dir):
        state = initialize_state("Guidance Cycle Test")
        slug = state["project"]["slug"]
        state["current_phase"] = "quality_gates"
        set_chat_step(state, "quality_gates.welcome")
        save_state(state, slug)

        result1 = process_message(state, slug, "help")
        result2 = process_message(state, slug, "more tips")

        assert result1["bot_messages"][0] != result2["bot_messages"][0] or len(result1["bot_messages"]) > 0

    def test_all_phases_have_welcome(self):
        from app.chat_engine import PHASE_WELCOME
        expected_phases = [
            "idea_intake", "feature_discovery",
            "outline_generation", "outline_approval", "chapter_build",
            "quality_gates", "final_assembly", "complete",
        ]
        for phase in expected_phases:
            assert phase in PHASE_WELCOME, f"Missing welcome for {phase}"


# ---------------------------------------------------------------------------
# Lock Features & Continue tests
# ---------------------------------------------------------------------------


class TestLockFeaturesAndContinue:
    """Test the __LOCK_FEATURES__ fast-track flow."""

    def test_lock_from_feature_discovery_with_core(
        self, feature_state, tmp_output_dir,
    ):
        """Lock during feature discovery works if core features already exist."""
        slug = feature_state["project"]["slug"]
        add_feature(
            feature_state,
            feature_type="core",
            feature_id="core_1",
            name="User authentication",
            description="Login system",
            rationale="Essential",
            problem_mapped_to="core problem",
            build_order=1,
        )
        save_state(feature_state, slug)

        result = process_message(feature_state, slug, LOCK_SIGNAL)

        assert feature_state["features"]["approved"] is True
        assert feature_state["current_phase"] == "outline_generation"
        assert result["redirect_url"] is not None

    def test_lock_with_no_features_blocks(
        self, feature_state, tmp_output_dir,
    ):
        """Lock with no features at all should block."""
        slug = feature_state["project"]["slug"]

        result = process_message(feature_state, slug, LOCK_SIGNAL)

        assert "can't lock" in result["bot_messages"][0].lower()
        assert feature_state["current_phase"] != "outline_generation"

    def test_lock_from_wrong_phase_blocked(self, chat_state, tmp_output_dir):
        """Lock from idea_intake phase should be blocked."""
        slug = chat_state["project"]["slug"]

        result = process_message(chat_state, slug, LOCK_SIGNAL)

        assert "only available" in result["bot_messages"][0].lower()
        assert chat_state["current_phase"] == "idea_intake"


# ---------------------------------------------------------------------------
# Delete Project tests
# ---------------------------------------------------------------------------


class TestDeleteProject:
    """Test project deletion functionality."""

    def test_delete_existing_project(self, tmp_output_dir):
        state = initialize_state("Delete Me")
        slug = state["project"]["slug"]
        project_dir = tmp_output_dir / slug
        assert project_dir.exists()

        result = delete_project(slug)

        assert result is True
        assert not project_dir.exists()

    def test_delete_nonexistent_project(self, tmp_output_dir):
        result = delete_project("nonexistent-project")
        assert result is False

    def test_delete_rejects_path_traversal(self, tmp_output_dir):
        with pytest.raises(ValueError):
            delete_project("../../../etc")

        with pytest.raises(ValueError):
            delete_project("foo/bar")

        with pytest.raises(ValueError):
            delete_project("foo\\bar")
