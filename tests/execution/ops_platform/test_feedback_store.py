"""Tests for execution/ops_platform/feedback_store.py"""

import pytest

from execution.ops_platform.capability_registry import CapabilityRegistry
from execution.ops_platform.plugin_loader import load_plugins
from execution.ops_platform import feedback_store
from execution.ops_platform.feedback_store import (
    FeedbackInvalid,
    get_aggregate,
    list_feedback,
    submit_feedback,
)


@pytest.fixture(autouse=True)
def isolate_paths(tmp_path, monkeypatch):
    """Redirect feedback storage + registry stats to tmp_path."""
    monkeypatch.setattr(feedback_store, "_FEEDBACK_DIR", tmp_path / "feedback")
    monkeypatch.setattr(feedback_store, "_INDEX_PATH", tmp_path / "feedback" / "_index.json")
    import execution.ops_platform.capability_registry as crm
    monkeypatch.setattr(crm, "_STATS_PATH", tmp_path / "stats.json")


@pytest.fixture
def registry(fake_plugin_root):
    return CapabilityRegistry(load_fn=lambda: load_plugins(root=fake_plugin_root))


def test_submit_minimal_feedback(registry):
    record = submit_feedback({
        "capability_id": "test_summary",
        "submitter": {"name": "Ali"},
    }, registry=registry)
    assert record["id"]
    assert record["submitted_at"]
    listed = list_feedback("test_summary")
    assert len(listed) == 1


def test_aggregate_updates_after_submit(registry):
    submit_feedback({
        "capability_id": "test_summary",
        "submitter": {"name": "Ali"},
        "ratings": {"usefulness": 5, "accuracy": 4},
    }, registry=registry)
    submit_feedback({
        "capability_id": "test_summary",
        "submitter": {"name": "Sam"},
        "ratings": {"usefulness": 3, "accuracy": 5},
    }, registry=registry)
    agg = get_aggregate("test_summary")
    assert agg["total_feedback"] == 2
    assert agg["averages"]["usefulness"] == 4.0
    assert agg["averages"]["accuracy"] == 4.5
    assert agg["overall_average"] is not None


def test_invalid_capability_id_rejected(registry):
    with pytest.raises(FeedbackInvalid):
        submit_feedback({
            # missing capability_id
            "submitter": {"name": "x"},
        }, registry=registry)


def test_records_pushed_to_registry(registry):
    submit_feedback({
        "capability_id": "test_summary",
        "submitter": {"name": "Ali"},
        "ratings": {"usefulness": 4},
    }, registry=registry)
    cap = registry.get("test_summary")
    assert cap["ratings"]["total_feedback"] == 1
    assert cap["ratings"]["averages"]["usefulness"] == 4.0


def test_suggested_enhancements_counted(registry):
    submit_feedback({
        "capability_id": "test_summary",
        "submitter": {"name": "Ali"},
        "suggested_enhancements": [
            {"kind": "missing_feature", "description": "needs file upload"},
            {"kind": "automation_idea", "description": "auto-tag"},
        ],
    }, registry=registry)
    agg = get_aggregate("test_summary")
    assert agg["suggestion_count"] == 2
