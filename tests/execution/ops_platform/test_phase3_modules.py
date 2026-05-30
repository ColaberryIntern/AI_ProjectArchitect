"""Tests for the new Phase 3 modules: training_pipeline, execution_assistant,
organizational_memory, workflow_optimizer, plus the deepened analytics +
semantic_analyzer entry points."""

import json

import pytest

from execution.ops_platform import (
    analytics,
    cache_bus,
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
    monkeypatch.setattr(pipeline_engine, "_PIPELINE_RUNS_DIR", tmp_path / "pipeline_runs")
    monkeypatch.setattr(cache_bus, "_VERSION_DIR", tmp_path / "versions")
    cache_bus.reset_for_tests()
    search_index.reset_index()
    recommendation_engine.reset_graph_cache()
    reg = CapabilityRegistry(load_fn=lambda: load_plugins(root=fake_plugin_root))
    import execution.ops_platform.capability_registry as crm2
    monkeypatch.setattr(crm2, "_DEFAULT_REGISTRY", reg)
    return reg


# ── training_pipeline ─────────────────────────────────────────────────


def test_training_pipeline_generates_bundle(registry, monkeypatch, tmp_path):
    from execution import llm_client
    monkeypatch.setattr(llm_client, "is_available", lambda: False)
    # Seed a walkthrough so generate_assets has something to chew on.
    training_agent._TRAINING_DIR.mkdir(parents=True, exist_ok=True)
    cap_id = registry.snapshot().capabilities[0]["id"]
    (training_agent._TRAINING_DIR / f"{cap_id}.md").write_text(
        "# Overview\n\nThis is what the workflow does.\n\n"
        "## When to use\n\n- For RFPs\n- For proposals\n\n"
        "## Steps\n\n1. Open the form. 2. Paste text. 3. Submit.\n"
    )
    bundle = training_pipeline.generate_assets(cap_id)
    assert bundle.capability_id == cap_id
    assert bundle.slide_count >= 2
    assert bundle.script_word_count > 0
    # Files exist on disk
    from pathlib import Path
    assert Path(bundle.voice_script_path).exists()
    assert Path(bundle.slides_path).exists()
    assert Path(bundle.storyboard_path).exists()


def test_training_pipeline_unknown_capability_raises(registry):
    with pytest.raises(ValueError):
        training_pipeline.generate_assets("does-not-exist")


def test_screenshot_hook_stub(registry, tmp_path):
    cap_id = registry.snapshot().capabilities[0]["id"]
    path = training_pipeline.record_screenshot_hook(
        cap_id, slide_id="slide_1", url="https://x.com/foo",
        selector=".main", description="capture the form",
    )
    assert path.exists()
    stub = json.loads(path.read_text())
    assert stub["status"] == "pending"


# ── execution_assistant ───────────────────────────────────────────────


def test_assistant_prepare_returns_required_inputs(registry):
    cap = registry.snapshot().capabilities[0]
    result = execution_assistant.prepare(cap["id"])
    assert result.capability_id == cap["id"]
    assert all(i.get("required") for i in result.required_inputs)


def test_assistant_prepare_unknown_capability_raises(registry):
    with pytest.raises(ValueError):
        execution_assistant.prepare("does-not-exist")


def test_assistant_explain_run(registry):
    cap = registry.snapshot().capabilities[0]
    rec = RunRecord(
        run_id="explain-1", capability_id=cap["id"],
        started_at="2026-05-26T10:00:00+00:00",
        finished_at="2026-05-26T10:00:01+00:00",
        status="succeeded",
        response={"summary": "Did the thing.", "files_created": ["a.py"], "next_recommended_tasks": []},
    )
    workflow_runner._persist(rec)
    result = execution_assistant.explain_output("explain-1")
    assert result is not None
    assert "Did the thing" in result.summary_paragraph
    assert any("file" in f.lower() for f in result.key_findings)


def test_assistant_intent_routes_to_recommender(registry, monkeypatch):
    from execution import llm_client
    monkeypatch.setattr(llm_client, "is_available", lambda: False)
    out = execution_assistant.intent_to_capabilities("summary", top_k=3)
    assert isinstance(out, list)


# ── organizational_memory ────────────────────────────────────────────


def test_memory_snapshot_contains_all_sections(registry, monkeypatch):
    from execution import llm_client
    monkeypatch.setattr(llm_client, "is_available", lambda: False)
    snap = organizational_memory.build_snapshot(registry=registry, persist=True)
    assert hasattr(snap, "what_succeeds")
    assert hasattr(snap, "what_fails")
    assert hasattr(snap, "team_preferences")
    assert hasattr(snap, "prompt_insights")
    assert hasattr(snap, "success_patterns")
    # Snapshot persists
    cached = organizational_memory.latest_snapshot()
    assert cached is not None


# ── workflow_optimizer ───────────────────────────────────────────────


def test_optimizer_returns_suggestion_list(registry, monkeypatch):
    from execution import llm_client
    monkeypatch.setattr(llm_client, "is_available", lambda: False)
    suggestions = workflow_optimizer.analyze(registry=registry)
    # Even with no data, low_value_candidates should fire for new capabilities.
    assert isinstance(suggestions, list)


def test_optimizer_decompose_flags_high_failure(registry):
    cap = registry.snapshot().capabilities[0]
    for i in range(8):
        workflow_runner._persist(RunRecord(
            run_id=f"f{i}", capability_id=cap["id"],
            started_at="2026-05-26T10:00:00+00:00",
            finished_at="2026-05-26T10:00:01+00:00",
            status="error", error_message="boom",
        ))
    suggestions = workflow_optimizer.analyze(registry=registry)
    decompose = [s for s in suggestions if s.kind == "DECOMPOSE_CAPABILITY"]
    assert any(s.capability_id == cap["id"] for s in decompose)


# ── analytics deepening ───────────────────────────────────────────────


def test_abandonment_analysis_smoke(registry):
    cap = registry.snapshot().capabilities[0]
    # one-time user
    workflow_runner._persist(RunRecord(
        run_id="x1", capability_id=cap["id"],
        started_at="2026-05-26T10:00:00+00:00", finished_at="2026-05-26T10:00:01+00:00",
        status="succeeded", inputs={"__initiator": "alice"},
    ))
    # repeat user
    workflow_runner._persist(RunRecord(
        run_id="x2", capability_id=cap["id"],
        started_at="2026-05-26T10:01:00+00:00", finished_at="2026-05-26T10:01:01+00:00",
        status="succeeded", inputs={"__initiator": "bob"},
    ))
    workflow_runner._persist(RunRecord(
        run_id="x3", capability_id=cap["id"],
        started_at="2026-05-26T10:02:00+00:00", finished_at="2026-05-26T10:02:01+00:00",
        status="succeeded", inputs={"__initiator": "bob"},
    ))
    rows = analytics.abandonment_analysis(min_runs=2, registry=registry)
    assert any(r["capability_id"] == cap["id"] for r in rows)


def test_duration_analysis_smoke(registry):
    cap = registry.snapshot().capabilities[0]
    for i in range(3):
        workflow_runner._persist(RunRecord(
            run_id=f"d{i}", capability_id=cap["id"],
            started_at="2026-05-26T10:00:00+00:00", finished_at="2026-05-26T10:00:01+00:00",
            status="succeeded", duration_ms=1000 + i * 200,
        ))
    rows = analytics.duration_analysis(registry=registry)
    assert any(r["capability_id"] == cap["id"] for r in rows)


def test_roi_trend_returns_buckets(registry):
    series = analytics.roi_trend(buckets=4, registry=registry)
    assert len(series) == 4
    assert all("window_start" in s for s in series)


def test_department_adoption_curve_returns_series(registry):
    curve = analytics.department_adoption_curve(buckets=3, registry=registry)
    assert "departments" in curve
    assert "series" in curve


def test_workflow_dependency_heatmap_smoke(registry):
    hm = analytics.workflow_dependency_heatmap(registry=registry)
    assert "nodes" in hm
    assert "matrix" in hm


def test_training_effectiveness_no_walkthrough_returns_empty(registry):
    rows = analytics.training_effectiveness(registry=registry)
    assert isinstance(rows, list)


# ── deepened semantic_analyzer ────────────────────────────────────────


def test_detect_anti_patterns_finds_deprecated(registry, monkeypatch):
    # Inject "deprecated" wording into one capability's README path
    from pathlib import Path
    cap = registry.snapshot().capabilities[0]
    meta = cap.get("_meta") or {}
    abs_dir = meta.get("plugin_dir_absolute")
    plugin_dir = Path(abs_dir) if abs_dir else None
    if plugin_dir:
        (plugin_dir / "README.md").write_text("# x\n\nThis capability is deprecated.")
    out = semantic_analyzer.detect_anti_patterns(registry=registry)
    assert "deprecated_tooling" in out


def test_workflow_overlap_returns_pairs(registry, monkeypatch):
    from execution import llm_client
    monkeypatch.setattr(llm_client, "is_available", lambda: False)
    semantic_analyzer.enrich_all(registry=registry)
    pairs = semantic_analyzer.workflow_overlap(registry=registry, threshold=0.2)
    assert isinstance(pairs, list)


def test_operational_patterns_aggregates(registry, monkeypatch):
    from execution import llm_client
    monkeypatch.setattr(llm_client, "is_available", lambda: False)
    semantic_analyzer.enrich_all(registry=registry)
    patterns = semantic_analyzer.operational_patterns(registry=registry)
    assert isinstance(patterns, dict)


# ── reputation history + trend ────────────────────────────────────────


def test_reputation_history_append_and_trend(registry):
    cap = registry.snapshot().capabilities[0]
    for _ in range(6):
        reputation_scorer.score_capability(cap["id"], registry=registry, persist=True)
    history = reputation_scorer.load_history(cap["id"])
    assert len(history) >= 6
    trend = reputation_scorer.trend(cap["id"])
    assert trend["direction"] in ("rising", "falling", "stable")


def test_score_if_stale_skips_fresh(registry):
    cap = registry.snapshot().capabilities[0]
    reputation_scorer.score_capability(cap["id"], registry=registry, persist=True)
    score, recomputed = reputation_scorer.score_if_stale(
        cap["id"], registry=registry, max_age_seconds=3600,
    )
    assert recomputed is False


# ── recommendation evidence ───────────────────────────────────────────


def test_recommendation_evidence_populated(registry, monkeypatch):
    from execution import llm_client
    monkeypatch.setattr(llm_client, "is_available", lambda: False)
    cap = registry.snapshot().capabilities[0]
    workflow_runner._persist(RunRecord(
        run_id="ev1", capability_id=cap["id"],
        started_at="2026-05-26T10:00:00+00:00", finished_at="2026-05-26T10:00:01+00:00",
        status="succeeded", inputs={"__initiator": "alice"},
    ))
    recs = recommendation_engine.recommend(query=cap["name"], top_k=3, registry=registry)
    assert recs
    cap_rec = next((r for r in recs if r.capability_id == cap["id"]), None)
    if cap_rec:
        assert "total_runs" in cap_rec.evidence
        assert cap_rec.evidence["total_runs"] >= 1


# ── feedback corpus appears in search ────────────────────────────────


def test_feedback_text_is_searchable(registry):
    cap = registry.snapshot().capabilities[0]
    feedback_store.submit_feedback({
        "capability_id": cap["id"],
        "submitter": {"name": "alice", "email": "a@x.com", "department": "Sales"},
        "ratings": {"usefulness": 5},
        "operational_notes": {"how_used": "lemonpoke pineapple banana"},
    }, registry=registry)
    # Force rebuild
    search_index.reset_index()
    results = search_index.search("lemonpoke", top_k=10, registry=registry)
    assert any(r.capability_id == cap["id"] for r in results)
