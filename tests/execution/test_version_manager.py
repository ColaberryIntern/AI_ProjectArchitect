"""Unit tests for execution/version_manager.py."""

import pytest

from execution.version_manager import (
    compare_versions,
    create_version,
    get_active_version,
    get_document_version_string,
    get_version_history,
)


class TestCreateVersion:
    def test_increments_version(self, sample_state):
        assert sample_state["outline"]["version"] == 1
        create_version(sample_state, "Added security section")
        assert sample_state["outline"]["version"] == 2

    def test_records_history(self, sample_state):
        create_version(sample_state, "Added security section")
        history = sample_state["version_history"]
        assert len(history) == 2  # initial + new
        assert history[-1]["version"] == 2
        assert history[-1]["change_summary"] == "Added security section"

    def test_updates_document_version(self, sample_state):
        create_version(sample_state, "Change")
        assert sample_state["document"]["version"] == "v2"


class TestGetActiveVersion:
    def test_returns_current(self, sample_state):
        assert get_active_version(sample_state) == 1

    def test_after_increment(self, sample_state):
        create_version(sample_state, "Change")
        assert get_active_version(sample_state) == 2


class TestGetVersionHistory:
    def test_returns_history(self, sample_state):
        history = get_version_history(sample_state)
        assert len(history) == 1
        assert history[0]["version"] == 1

    def test_after_multiple_versions(self, sample_state):
        create_version(sample_state, "Change 1")
        create_version(sample_state, "Change 2")
        history = get_version_history(sample_state)
        assert len(history) == 3


class TestGetDocumentVersionString:
    def test_initial(self, sample_state):
        assert get_document_version_string(sample_state) == "v1"

    def test_after_increment(self, sample_state):
        create_version(sample_state, "Change")
        assert get_document_version_string(sample_state) == "v2"


class TestCompareVersions:
    def test_compare_existing(self, sample_state):
        create_version(sample_state, "Change 1")
        result = compare_versions(sample_state, 1, 2)
        assert result["v1"]["version"] == 1
        assert result["v2"]["version"] == 2
        assert result["versions_between"] == 0

    def test_nonexistent_version(self, sample_state):
        with pytest.raises(ValueError, match="not found"):
            compare_versions(sample_state, 1, 99)

    def test_versions_between(self, sample_state):
        create_version(sample_state, "Change 1")
        create_version(sample_state, "Change 2")
        result = compare_versions(sample_state, 1, 3)
        assert result["versions_between"] == 1
