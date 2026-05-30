"""Tests for execution/ops_platform/discovery_queue.py"""

import pytest

from execution.ops_platform import (
    cache_bus,
    discovery_queue,
    pipeline_engine,
    workflow_discovery,
)
from execution.ops_platform.workflow_discovery import DiscoveredPattern


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(discovery_queue, "_QUEUE_DIR", tmp_path / "discovery_queue")
    monkeypatch.setattr(discovery_queue, "_INDEX_PATH", tmp_path / "discovery_queue" / "_index.json")
    monkeypatch.setattr(pipeline_engine, "_PIPELINES_DIR", tmp_path / "pipelines")
    monkeypatch.setattr(pipeline_engine, "_PLUGIN_PIPELINES_DIR", tmp_path / "plugin_pipelines")
    monkeypatch.setattr(cache_bus, "_VERSION_DIR", tmp_path / "versions")
    cache_bus.reset_for_tests()
    yield


def _make_pattern():
    return DiscoveredPattern(
        sequence=["cap_a", "cap_b"],
        occurrences=4,
        distinct_initiators=2,
        last_observed="2026-05-26T12:00:00+00:00",
        capability_names=["Cap A", "Cap B"],
        draft_pipeline={
            "pipeline_id": "discovered_a_b",
            "name": "A then B",
            "version": "0.1.0",
            "created_by": {"name": "workflow_discovery"},
            "steps": [
                {"step_id": "s1", "capability_id": "cap_a"},
                {"step_id": "s2", "capability_id": "cap_b", "depends_on": ["s1"]},
            ],
        },
    )


def test_record_then_list_pending(isolated):
    discovery_queue.record_discovery(_make_pattern())
    pending = discovery_queue.list_items(state="pending")
    assert len(pending) == 1
    assert pending[0].sequence == ["cap_a", "cap_b"]


def test_record_is_idempotent(isolated):
    p = _make_pattern()
    discovery_queue.record_discovery(p)
    p.occurrences = 7
    discovery_queue.record_discovery(p)
    all_items = discovery_queue.list_items()
    assert len(all_items) == 1
    assert all_items[0].occurrences == 7


def test_reject_state_is_remembered(isolated):
    item = discovery_queue.record_discovery(_make_pattern())
    discovery_queue.reject(item.queue_id, reviewer="alice", notes="duplicate")
    assert discovery_queue.is_rejected(["cap_a", "cap_b"])
    # Re-recording does not resurrect it
    discovery_queue.record_discovery(_make_pattern())
    refreshed = discovery_queue.get_item(item.queue_id)
    assert refreshed.state == "rejected"


def test_publish_persists_pipeline(isolated):
    item = discovery_queue.record_discovery(_make_pattern())
    discovery_queue.approve(item.queue_id, reviewer="alice")
    published, err = discovery_queue.publish(item.queue_id)
    assert err is None
    assert published.state == "published"
    assert pipeline_engine.load_pipeline(published.published_pipeline_id) is not None


def test_queue_stats_counts(isolated):
    a = discovery_queue.record_discovery(_make_pattern())
    p2 = _make_pattern()
    p2.sequence = ["cap_a", "cap_c"]
    p2.capability_names = ["Cap A", "Cap C"]
    p2.draft_pipeline["pipeline_id"] = "discovered_a_c"
    p2.draft_pipeline["steps"][1]["capability_id"] = "cap_c"
    b = discovery_queue.record_discovery(p2)
    discovery_queue.reject(b.queue_id)
    stats = discovery_queue.queue_stats()
    assert stats["pending"] == 1
    assert stats["rejected"] == 1
    assert stats["total"] == 2
