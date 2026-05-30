"""Phase 2 end-to-end smoke test.

Runs the Phase 2 surface against the in-tree fake plugin tree:
- enrich all capabilities
- build the operational graph
- score reputation for one capability
- run discovery
- ask for recommendations
- compute analytics roll-ups

Each step should complete without raising, with realistic shapes.
This is the lowest bar — it doesn't assert semantic correctness, just that
the pipeline holds together when wired through real modules.
"""

from __future__ import annotations

import pytest

from execution.ops_platform import (
    analytics,
    feedback_store,
    operational_graph,
    pipeline_engine,
    recommendation_engine,
    reputation_scorer,
    search_index,
    semantic_analyzer,
    training_agent,
    workflow_discovery,
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
    monkeypatch.setattr(operational_graph, "_GRAPH_PERSIST_PATH", tmp_path / "graph.json")
    monkeypatch.setattr(workflow_discovery, "_DISCOVERY_DIR", tmp_path / "discoveries")
    monkeypatch.setattr(pipeline_engine, "_PIPELINES_DIR", tmp_path / "pipelines")
    monkeypatch.setattr(pipeline_engine, "_PIPELINE_RUNS_DIR", tmp_path / "pipeline_runs")
    monkeypatch.setattr(training_agent, "_TRAINING_DIR", tmp_path / "training")
    reg = CapabilityRegistry(load_fn=lambda: load_plugins(root=fake_plugin_root))
    import execution.ops_platform.capability_registry as crm2
    monkeypatch.setattr(crm2, "_DEFAULT_REGISTRY", reg)
    search_index.reset_index()
    recommendation_engine.reset_graph_cache()
    return reg


def test_phase2_pipeline_holds_together(registry, monkeypatch):
    from execution import llm_client
    monkeypatch.setattr(llm_client, "is_available", lambda: False)

    # 1. Enrichment runs on every capability and survives the heuristic path.
    enrichments = semantic_analyzer.enrich_all(registry=registry)
    assert len(enrichments) == len(registry.snapshot().capabilities)

    # 2. Seed run history so the graph and discovery have something to chew on.
    caps = registry.snapshot().capabilities
    a, b = caps[0]["id"], caps[1]["id"]
    workflow_runner._persist(RunRecord(
        run_id="r1", capability_id=a,
        started_at="2026-05-26T10:00:00+00:00", finished_at="2026-05-26T10:00:01+00:00",
        status="succeeded", inputs={"__initiator": "alice"},
    ))
    workflow_runner._persist(RunRecord(
        run_id="r2", capability_id=b,
        started_at="2026-05-26T10:02:00+00:00", finished_at="2026-05-26T10:02:01+00:00",
        status="succeeded", inputs={"__initiator": "alice"},
    ))

    # 3. Graph build succeeds.
    g = operational_graph.build_graph(registry=registry, persist=True)
    assert g.stats()["node_count"] > 0

    # 4. Reputation score for capability A.
    rep = reputation_scorer.score_capability(a, registry=registry, persist=True)
    assert rep.capability_id == a

    # 5. Discovery returns a list (may be empty under min_occurrences).
    patterns = workflow_discovery.discover_patterns(
        window=2, min_occurrences=2, registry=registry
    )
    assert isinstance(patterns, list)

    # 6. Recommendations return a non-error list, even with sparse data.
    recommendation_engine.reset_graph_cache()
    recs = recommendation_engine.recommend(query="summary", top_k=3, registry=registry)
    assert isinstance(recs, list)

    # 7. Analytics roll-ups don't raise.
    summary = analytics.executive_summary(registry=registry)
    assert summary["capability_count"] >= 2
    assert "total_hours_saved" in summary

    bot = analytics.bottlenecks(registry=registry, min_runs=1)
    assert isinstance(bot, list)
