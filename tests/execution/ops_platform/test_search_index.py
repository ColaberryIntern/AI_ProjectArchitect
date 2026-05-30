"""Tests for execution/ops_platform/search_index.py"""

import pytest

from execution.ops_platform.capability_registry import CapabilityRegistry
from execution.ops_platform.plugin_loader import load_plugins
from execution.ops_platform import search_index
from execution.ops_platform.search_index import recommend_related, rebuild, search


@pytest.fixture
def registry(fake_plugin_root, tmp_path, monkeypatch):
    import execution.ops_platform.capability_registry as crm
    monkeypatch.setattr(crm, "_STATS_PATH", tmp_path / "stats.json")
    search_index.reset_index()
    return CapabilityRegistry(load_fn=lambda: load_plugins(root=fake_plugin_root))


def test_search_finds_by_name(registry):
    results = search("compose", registry=registry)
    assert any(r.capability_id == "test_compose" for r in results)


def test_search_finds_by_tag(registry):
    results = search("test_summary", registry=registry)
    assert any(r.capability_id == "test_summary" for r in results)


def test_search_empty_query_returns_empty(registry):
    assert search("", registry=registry) == []


def test_type_filter(registry):
    results = search("test", type_filter="agent", registry=registry)
    assert len(results) == 1
    assert results[0].capability_id == "test_agent"


def test_recommend_related_excludes_self(registry):
    related = recommend_related("test_summary", registry=registry)
    assert all(r.capability_id != "test_summary" for r in related)


def test_rebuild_returns_count(registry):
    count = rebuild(registry=registry)
    assert count == 3


def test_ranking_includes_usage_count(registry):
    registry.record_usage("test_summary")
    registry.record_usage("test_summary")
    rebuild(registry=registry)  # refresh after usage bump
    results = search("test", registry=registry)
    # All three match "test" — but test_summary should be ranked higher because of usage_count.
    cap_ids = [r.capability_id for r in results]
    assert cap_ids.index("test_summary") < cap_ids.index("test_agent")
