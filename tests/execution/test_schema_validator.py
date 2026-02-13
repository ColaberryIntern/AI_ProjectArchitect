"""Unit tests for execution/schema_validator.py."""

import pytest
from jsonschema import ValidationError

from execution.schema_validator import (
    get_state_validation_errors,
    get_validation_errors,
    is_valid_project_state,
    load_schema,
    validate_against_schema,
    validate_project_state,
)
from config.settings import PROJECT_STATE_SCHEMA


class TestLoadSchema:
    def test_load_valid_schema(self):
        schema = load_schema(PROJECT_STATE_SCHEMA)
        assert "type" in schema
        assert schema["type"] == "object"

    def test_load_missing_schema(self):
        with pytest.raises(FileNotFoundError):
            load_schema("nonexistent_schema.json")


class TestValidateProjectState:
    def test_valid_state_passes(self, sample_state):
        assert validate_project_state(sample_state) is True

    def test_missing_project_field(self, sample_state):
        del sample_state["project"]
        with pytest.raises(ValidationError):
            validate_project_state(sample_state)

    def test_missing_current_phase(self, sample_state):
        del sample_state["current_phase"]
        with pytest.raises(ValidationError):
            validate_project_state(sample_state)

    def test_invalid_phase_value(self, sample_state):
        sample_state["current_phase"] = "invalid_phase"
        with pytest.raises(ValidationError):
            validate_project_state(sample_state)

    def test_invalid_project_name_empty(self, sample_state):
        sample_state["project"]["name"] = ""
        with pytest.raises(ValidationError):
            validate_project_state(sample_state)

    def test_invalid_slug_format(self, sample_state):
        sample_state["project"]["slug"] = "Has Spaces"
        with pytest.raises(ValidationError):
            validate_project_state(sample_state)

    def test_additional_properties_rejected(self, sample_state):
        sample_state["extra_field"] = "not allowed"
        with pytest.raises(ValidationError):
            validate_project_state(sample_state)

    def test_valid_with_ideation_data(self, sample_state):
        sample_state["ideation"]["business_model"]["status"] = "answered"
        sample_state["ideation"]["business_model"]["responses"] = [
            {"question": "Who benefits?", "answer": "Internal teams"}
        ]
        sample_state["ideation"]["business_model"]["summary"] = "Internal tool"
        assert validate_project_state(sample_state) is True

    def test_invalid_ideation_status(self, sample_state):
        sample_state["ideation"]["business_model"]["status"] = "invalid"
        with pytest.raises(ValidationError):
            validate_project_state(sample_state)

    def test_valid_with_outline_sections(self, sample_state, sample_outline_sections):
        sample_state["outline"]["sections"] = sample_outline_sections
        assert validate_project_state(sample_state) is True

    def test_invalid_outline_section_type(self, sample_state):
        sample_state["outline"]["sections"] = [
            {"index": 1, "title": "Test", "type": "invalid", "summary": "Test"}
        ]
        with pytest.raises(ValidationError):
            validate_project_state(sample_state)

    def test_valid_with_chapters(self, sample_state):
        sample_state["chapters"] = [
            {
                "index": 1,
                "outline_section": "System Purpose",
                "status": "draft",
                "revision_count": 0,
                "content_path": None,
                "quality_report": None,
                "approved_at": None,
            }
        ]
        assert validate_project_state(sample_state) is True

    def test_invalid_chapter_status(self, sample_state):
        sample_state["chapters"] = [
            {
                "index": 1,
                "outline_section": "Test",
                "status": "invalid",
                "revision_count": 0,
            }
        ]
        with pytest.raises(ValidationError):
            validate_project_state(sample_state)

    def test_chapter_revision_count_max(self, sample_state):
        sample_state["chapters"] = [
            {
                "index": 1,
                "outline_section": "Test",
                "status": "revision_2",
                "revision_count": 3,  # exceeds max of 2
            }
        ]
        with pytest.raises(ValidationError):
            validate_project_state(sample_state)

    def test_valid_core_feature(self, sample_state, sample_core_feature):
        sample_state["features"]["core"] = [sample_core_feature]
        assert validate_project_state(sample_state) is True

    def test_valid_optional_feature(self, sample_state, sample_optional_feature):
        sample_state["features"]["optional"] = [sample_optional_feature]
        assert validate_project_state(sample_state) is True

    def test_core_feature_missing_required(self, sample_state):
        sample_state["features"]["core"] = [
            {"id": "x", "name": "x"}  # missing required fields
        ]
        with pytest.raises(ValidationError):
            validate_project_state(sample_state)


class TestGetValidationErrors:
    def test_valid_state_no_errors(self, sample_state):
        errors = get_state_validation_errors(sample_state)
        assert errors == []

    def test_invalid_state_returns_errors(self, sample_state):
        del sample_state["project"]
        errors = get_state_validation_errors(sample_state)
        assert len(errors) > 0
        assert any("project" in e for e in errors)

    def test_multiple_errors(self):
        # Completely invalid state
        errors = get_state_validation_errors({"foo": "bar"})
        assert len(errors) > 0


class TestIsValidProjectState:
    def test_valid_state(self, sample_state):
        assert is_valid_project_state(sample_state) is True

    def test_invalid_state(self):
        assert is_valid_project_state({"invalid": True}) is False
