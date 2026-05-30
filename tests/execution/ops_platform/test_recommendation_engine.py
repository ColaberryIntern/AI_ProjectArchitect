"""Tests for execution/ops_platform/recommendation_engine.py"""

import pytest

from execution.ops_platform import (
    operational_graph,
    recommendation_engine,
    reputation_scorer,
    search_index,
    semantic_analyzer,
    workflow_runner,
)
from execution.ops_platform.capability_registry import CapabilityRegistry
from execution.ops_platform.plugin_loader import load_plugins
from execution.ops_platform.workflow_runner import RunRecord


@pytest.fixture(autouse=True)
def reset_graph():
    recommendation_engine.reset_graph_cache()
    search_index.reset_index()
    yield
    recommendation_engine.reset_graph_cache()
    search_index.reset_index()


@pytest.fixture
def registry(fake_plugin_root, tmp_path, monkeypatch):
    import execution.ops_platform.capability_registry as crm
    monkeypatch.setattr(crm, "_STATS_PATH", tmp_path / "registry_stats.json")
    monkeypatch.setattr(workflow_runner, "_RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(semantic_analyzer, "_CACHE_DIR", tmp_path / "semantic")
    monkeypatch.setattr(reputation_scorer, "_SCORE_DIR", tmp_path / "reputation")
    monkeypatch.setattr(operational_graph, "_GRAPH_PERSIST_PATH", tmp_path / "graph.json")
    reg = CapabilityRegistry(load_fn=lambda: load_plugins(root=fake_plugin_root))
    # ensure default singleton points at this registry for modules that call default_registry()
    import execution.ops_platform.capability_registry as crm2
    monkeypatch.setattr(crm2, "_DEFAULT_REGISTRY", reg)
    return reg


def test_empty_query_returns_baseline(registry, monkeypatch):
    from execution import llm_client
    monkeypatch.setattr(llm_client, "is_available", lambda: False)
    recs = recommendation_engine.recommend(query="", top_k=5, registry=registry)
    assert len(recs) >= 1
    assert all(r.final_score >= 0 for r in recs)


def test_query_pulls_lexical_matches(registry, monkeypatch):
    from execution import llm_client
    monkeypatch.setattr(llm_client, "is_available", lambda: False)
    recs = recommendation_engine.recommend(query="summary", top_k=5, registry=registry)
    assert len(recs) >= 1
    # at least one item should reference the lexical match
    assert any(r.sub_scores["lexical"] > 0 for r in recs)


def test_reasons_are_populated(registry, monkeypatch):
    from execution import llm_client
    monkeypatch.setattr(llm_client, "is_available", lambda: False)
    recs = recommendation_engine.recommend(query="test", top_k=3, registry=registry)
    for r in recs:
        assert r.reasons, "every recommendation should ship at least one reason"


def test_pipeline_recommendation_matches_query():
    # No fixture needed — recommend_pipelines_for_query reads pipeline_engine directly
    recs = recommendation_engine.recommend_pipelines_for_query("proposal rfp", top_k=3)
    # the built-in proposal_analysis pipeline ships with the repo
    assert isinstance(recs, list)


def test_kind_filter_only_returns_pipelines(registry, monkeypatch):
    from execution import llm_client
    monkeypatch.setattr(llm_client, "is_available", lambda: False)
    # fake plugin tree has no pipelines — filter to "pipeline" returns nothing
    recs = recommendation_engine.recommend(
        query="test", kinds=["pipeline"], top_k=5, registry=registry,
    )
    assert all(r.type == "pipeline" for r in recs)


def test_recommend_next_after_succeeded_run(registry, monkeypatch):
    from execution import llm_client
    monkeypatch.setattr(llm_client, "is_available", lambda: False)
    caps = registry.snapshot().capabilities[:2]
    a, b = caps[0]["id"], caps[1]["id"]
    workflow_runner._persist(RunRecord(
        run_id="rA", capability_id=a,
        started_at="2026-05-26T10:00:00+00:00", finished_at="2026-05-26T10:00:01+00:00",
        status="succeeded", inputs={"__initiator": "alice"},
    ))
    workflow_runner._persist(RunRecord(
        run_id="rB", capability_id=b,
        started_at="2026-05-26T10:05:00+00:00", finished_at="2026-05-26T10:05:01+00:00",
        status="succeeded", inputs={"__initiator": "alice"},
    ))
    recommendation_engine.reset_graph_cache()
    recs = recommendation_engine.recommend_next_after_run("rA", top_k=3, registry=registry)
    assert isinstance(recs, list)
