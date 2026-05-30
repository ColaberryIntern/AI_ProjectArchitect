"""Tests for execution/ops_platform/capability_registry.py"""

import pytest

from execution.ops_platform.capability_registry import CapabilityRegistry
from execution.ops_platform.plugin_loader import load_plugins


@pytest.fixture
def registry(fake_plugin_root, tmp_path, monkeypatch):
    """Build a registry pointing at the fake plugin tree, with stats redirected
    to tmp_path so each test starts fresh."""
    # Redirect registry stats path so test runs don't pollute repo output.
    import execution.ops_platform.capability_registry as crm
    monkeypatch.setattr(crm, "_STATS_PATH", tmp_path / "registry_stats.json")
    return CapabilityRegistry(load_fn=lambda: load_plugins(root=fake_plugin_root))


def test_snapshot_has_all_capabilities(registry):
    snap = registry.snapshot()
    assert len(snap.capabilities) == 3


def test_get_by_id(registry):
    cap = registry.get("test_summary")
    assert cap is not None
    assert cap["name"] == "Test Summary"


def test_list_by_type(registry):
    workflows = registry.list(type_name="workflow")
    assert len(workflows) == 2
    agents = registry.list(type_name="agent")
    assert len(agents) == 1


def test_list_by_tag(registry):
    matches = registry.list(tag="test_summary")
    assert len(matches) == 1
    assert matches[0]["id"] == "test_summary"


def test_record_usage_increments(registry):
    assert registry.record_usage("test_summary") == 1
    assert registry.record_usage("test_summary") == 2
    cap = registry.get("test_summary")
    assert cap["usage_count"] == 2


def test_set_rating_aggregate(registry):
    registry.set_rating_aggregate("test_summary", {"overall_average": 4.5, "total_feedback": 3})
    cap = registry.get("test_summary")
    assert cap["ratings"]["overall_average"] == 4.5


def test_departments(registry):
    depts = registry.snapshot().departments()
    assert "Sales" in depts
