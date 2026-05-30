"""Tests for execution/ops_platform/operational_graph.py"""

import pytest

from execution.ops_platform import operational_graph, semantic_analyzer, workflow_runner
from execution.ops_platform.capability_registry import CapabilityRegistry
from execution.ops_platform.plugin_loader import load_plugins
from execution.ops_platform.workflow_runner import RunRecord


@pytest.fixture
def registry(fake_plugin_root, tmp_path, monkeypatch):
    import execution.ops_platform.capability_registry as crm
    monkeypatch.setattr(crm, "_STATS_PATH", tmp_path / "registry_stats.json")
    monkeypatch.setattr(workflow_runner, "_RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(semantic_analyzer, "_CACHE_DIR", tmp_path / "semantic")
    monkeypatch.setattr(operational_graph, "_GRAPH_PERSIST_PATH", tmp_path / "graph.json")
    return CapabilityRegistry(load_fn=lambda: load_plugins(root=fake_plugin_root))


def test_graph_builds_capability_nodes_and_dept_edges(registry):
    g = operational_graph.build_graph(registry=registry)
    stats = g.stats()
    assert stats["node_count"] > 0
    assert stats["by_node_type"].get("capability", 0) >= 2
    assert stats["by_edge_kind"].get("used_in_dept", 0) >= 1


def test_followed_by_edge_emerges_from_run_sequence(registry):
    caps = registry.snapshot().capabilities[:2]
    a, b = caps[0]["id"], caps[1]["id"]
    workflow_runner._persist(RunRecord(
        run_id="r1", capability_id=a,
        started_at="2026-05-26T10:00:00+00:00", finished_at="2026-05-26T10:00:01+00:00",
        status="succeeded", inputs={"__initiator": "alice"},
    ))
    workflow_runner._persist(RunRecord(
        run_id="r2", capability_id=b,
        started_at="2026-05-26T10:05:00+00:00", finished_at="2026-05-26T10:05:01+00:00",
        status="succeeded", inputs={"__initiator": "alice"},
    ))

    g = operational_graph.build_graph(registry=registry)
    followed = g.top_followed_by(a, top_k=5)
    assert (b, 1.0) in [(cid, w) for cid, w in followed]


def test_persist_writes_snapshot_to_disk(registry):
    g = operational_graph.build_graph(registry=registry, persist=True)
    assert operational_graph._GRAPH_PERSIST_PATH.exists()
    persisted = operational_graph.get_persisted()
    assert persisted is not None
    assert "nodes" in persisted and "edges" in persisted


def test_no_self_edges(registry):
    g = operational_graph.OperationalGraph()
    g.add_node("capability", "x")
    g.add_edge("capability:x", "capability:x", "depends_on")
    assert len(g.edges) == 0


def test_department_capabilities_query(registry):
    g = operational_graph.build_graph(registry=registry)
    sales_caps = g.department_capabilities("Sales", top_k=10)
    assert len(sales_caps) >= 1
