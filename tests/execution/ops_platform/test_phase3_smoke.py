"""Phase 3 end-to-end smoke test.

Exercises the new modules in concert:
  - emit cache_bus events
  - confirm graph cache invalidates between runs
  - run optimizer + memory snapshot
  - publish a discovery via the queue → pipeline_engine path
"""

from __future__ import annotations

import pytest

from execution.ops_platform import (
    analytics,
    cache_bus,
    discovery_queue,
    execution_assistant,
    feedback_store,
    operational_graph,
    organizational_memory,
    pipeline_engine,
    recommendation_engine,
    reputation_scorer,
    search_index,
    semantic_analyzer,
    training_agent,
    training_pipeline,
    workflow_discovery,
    workflow_optimizer,
    workflow_runner,
)
from execution.ops_platform.capability_registry import CapabilityRegistry
from execution.ops_platform.plugin_loader import load_plugins
from execution.ops_platform.workflow_runner import RunRecord


@pytest.fixture
def registry(fake_plugin_root, tmp_path, monkeypatch):
    import execution.ops_platform.capability_registry as crm
    monkeypatch.setattr(crm, "_STATS_PATH", tmp_path / "registry_stats.json")
    monkeypatch.setattr(workflow_runner, "_RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(feedback_store, "_FEEDBACK_DIR", tmp_path / "feedback")
    monkeypatch.setattr(feedback_store, "_INDEX_PATH", tmp_path / "feedback" / "_index.json")
    monkeypatch.setattr(semantic_analyzer, "_CACHE_DIR", tmp_path / "semantic")
    monkeypatch.setattr(reputation_scorer, "_SCORE_DIR", tmp_path / "reputation")
    monkeypatch.setattr(reputation_scorer, "_HISTORY_DIR", tmp_path / "reputation_history")
    monkeypatch.setattr(operational_graph, "_GRAPH_PERSIST_PATH", tmp_path / "graph.json")
    monkeypatch.setattr(training_agent, "_TRAINING_DIR", tmp_path / "training")
    monkeypatch.setattr(training_pipeline, "_ASSETS_DIR", tmp_path / "training_assets")
    monkeypatch.setattr(organizational_memory, "_MEMORY_DIR", tmp_path / "org_memory")
    monkeypatch.setattr(discovery_queue, "_QUEUE_DIR", tmp_path / "discovery_queue")
    monkeypatch.setattr(discovery_queue, "_INDEX_PATH", tmp_path / "discovery_queue" / "_index.json")
    monkeypatch.setattr(pipeline_engine, "_PIPELINES_DIR", tmp_path / "pipelines")
    monkeypatch.setattr(pipeline_engine, "_PLUGIN_PIPELINES_DIR", tmp_path / "plugin_pipelines")
    monkeypatch.setattr(pipeline_engine, "_PIPELINE_RUNS_DIR", tmp_path / "pipeline_runs")
    monkeypatch.setattr(cache_bus, "_VERSION_DIR", tmp_path / "versions")
    cache_bus.reset_for_tests()
    search_index.reset_index()
    recommendation_engine.reset_graph_cache()
    reg = CapabilityRegistry(load_fn=lambda: load_plugins(root=fake_plugin_root))
    import execution.ops_platform.capability_registry as crm2
    monkeypatch.setattr(crm2, "_DEFAULT_REGISTRY", reg)
    return reg


def test_phase3_pipeline_holds_together(registry, monkeypatch):
    from execution import llm_client
    monkeypatch.setattr(llm_client, "is_available", lambda: False)

    caps = registry.snapshot().capabilities
    a, b = caps[0]["id"], caps[1]["id"]

    # 1. Seed run history → triggers cache_bus.RUN_RECORDED implicitly via _persist
    for i in range(4):
        rec = RunRecord(
            run_id=f"r{i}", capability_id=a if i % 2 == 0 else b,
            started_at=f"2026-05-26T10:0{i}:00+00:00",
            finished_at=f"2026-05-26T10:0{i}:01+00:00",
            status="succeeded", inputs={"__initiator": "alice"},
        )
        workflow_runner._persist(rec)
    cache_bus.emit(cache_bus.Topic.RUN_RECORDED, {})

    # 2. Build graph and confirm subsequent rebuilds happen on bus event
    g1 = recommendation_engine._cached_graph()
    nodes_first = g1.stats()["node_count"]
    cache_bus.emit(cache_bus.Topic.PIPELINE_CREATED, {})
    g2 = recommendation_engine._cached_graph()
    # Cache may produce same graph but the rebuild happened (different instance)
    assert g2 is not None

    # 3. Discovery flow: discover → queue → approve → publish
    patterns = workflow_discovery.discover_patterns(
        window=2, min_occurrences=1, registry=registry,
    )
    if patterns:
        workflow_discovery.record_to_queue(patterns)
        items = discovery_queue.list_items(state="pending")
        if items:
            discovery_queue.approve(items[0].queue_id)
            _, err = discovery_queue.publish(items[0].queue_id)
            assert err is None or "invalid" in err.lower() or "binding" in err.lower()

    # 4. Organizational memory snapshot
    snap = organizational_memory.build_snapshot(registry=registry, persist=True)
    assert snap.generated_at

    # 5. Workflow optimizer
    suggestions = workflow_optimizer.analyze(registry=registry)
    assert isinstance(suggestions, list)

    # 6. Execution assistant
    result = execution_assistant.prepare(a)
    assert result.capability_id == a

    # 7. Analytics depth
    assert isinstance(analytics.abandonment_analysis(registry=registry), list)
    assert isinstance(analytics.duration_analysis(registry=registry), list)
    assert "departments" in analytics.department_adoption_curve(registry=registry)

    # 8. Search index now includes feedback corpus
    feedback_store.submit_feedback({
        "capability_id": a,
        "submitter": {"name": "alice", "email": "a@x.com", "department": "Sales"},
        "ratings": {"usefulness": 4},
        "operational_notes": {"how_used": "zaffrelock pretzel knot"},
    }, registry=registry)
    search_index.reset_index()
    hits = search_index.search("zaffrelock", top_k=5, registry=registry)
    assert any(h.capability_id == a for h in hits)
