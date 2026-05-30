"""Tests for Phase 4 modules: audit_log, capability_versions, prompt_diff,
workspaces, builder, optimizer simulate/apply, executive_reporting,
telemetry, adoption."""

import json
from datetime import datetime, timezone

import pytest

from execution.ops_platform import (
    adoption,
    analytics,
    audit_log,
    builder,
    cache_bus,
    capability_versions,
    discovery_queue,
    executive_reporting,
    feedback_store,
    operational_graph,
    organizational_memory,
    pipeline_engine,
    prompt_diff,
    recommendation_engine,
    reputation_scorer,
    search_index,
    semantic_analyzer,
    telemetry,
    training_agent,
    training_pipeline,
    workflow_discovery,
    workflow_optimizer,
    workflow_runner,
    workspaces,
)
from execution.ops_platform.capability_registry import CapabilityRegistry
from execution.ops_platform.errors import OpsError, not_found, invalid_input
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
    monkeypatch.setattr(audit_log, "_AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(capability_versions, "_VERSIONS_DIR", tmp_path / "cap_versions")
    monkeypatch.setattr(workspaces, "_WORKSPACES_DIR", tmp_path / "workspaces")
    monkeypatch.setattr(builder, "_DRAFTS_DIR", tmp_path / "drafts")
    monkeypatch.setattr(prompt_diff, "_DIFFS_DIR", tmp_path / "prompt_diffs")
    monkeypatch.setattr(executive_reporting, "_REPORTING_DIR", tmp_path / "reporting")
    monkeypatch.setattr(telemetry, "_TELEMETRY_DIR", tmp_path / "telemetry")
    cache_bus.reset_for_tests()
    search_index.reset_index()
    recommendation_engine.reset_graph_cache()
    reg = CapabilityRegistry(load_fn=lambda: load_plugins(root=fake_plugin_root))
    import execution.ops_platform.capability_registry as crm2
    monkeypatch.setattr(crm2, "_DEFAULT_REGISTRY", reg)
    return reg


# ── audit_log ─────────────────────────────────────────────────────────


def test_audit_record_and_read(registry):
    entry = audit_log.record(
        action="test.event", entity_type="test_entity", entity_id="x1",
        actor="alice", new_state={"foo": "bar"},
    )
    assert entry.entity_id == "x1"
    rows = audit_log.list_entries(entity_id="x1")
    assert len(rows) == 1
    assert rows[0]["action"] == "test.event"


def test_audit_filter_by_action(registry):
    audit_log.record(action="foo.bar", entity_type="t", entity_id="a", actor="x")
    audit_log.record(action="baz.qux", entity_type="t", entity_id="b", actor="x")
    rows = audit_log.list_entries(action="foo.bar")
    assert all(r["action"] == "foo.bar" for r in rows)


def test_audit_correlation_replay(registry):
    cid = "corr-123"
    audit_log.record(action="step.one", entity_type="t", entity_id="a", actor="x",
                     correlation_id=cid)
    audit_log.record(action="step.two", entity_type="t", entity_id="a", actor="x",
                     correlation_id=cid)
    rows = audit_log.replay(cid)
    assert len(rows) == 2


def test_audit_stats(registry):
    audit_log.record(action="a", entity_type="t", entity_id="x", actor="alice")
    audit_log.record(action="a", entity_type="t", entity_id="y", actor="bob")
    s = audit_log.stats(days=1)
    assert s["total"] == 2
    assert s["by_action"]["a"] == 2


# ── capability_versions ────────────────────────────────────────────────


def test_register_and_list_versions(registry):
    cap_id = registry.snapshot().capabilities[0]["id"]
    v = capability_versions.register_version(
        cap_id, semver="1.0.0", changelog="initial",
        created_by={"name": "alice"},
    )
    assert v.semver == "1.0.0"
    assert v.status == "draft"
    versions = capability_versions.list_versions(cap_id)
    assert len(versions) == 1


def test_promote_draft_to_approved_demotes_old(registry):
    cap_id = registry.snapshot().capabilities[0]["id"]
    v1 = capability_versions.register_version(
        cap_id, semver="1.0.0", changelog="first", created_by="alice",
    )
    v1_promoted = capability_versions.promote(v1.version_id, target_status="approved",
                                               approver="alice")
    assert v1_promoted.status == "approved"
    v2 = capability_versions.register_version(
        cap_id, semver="2.0.0", changelog="second", created_by="alice",
    )
    v2_promoted = capability_versions.promote(v2.version_id, target_status="approved",
                                               approver="alice")
    refreshed_v1 = capability_versions.get_version(v1.version_id)
    assert refreshed_v1.status == "deprecated"
    assert v2_promoted.status == "approved"


def test_forward_transition_only(registry):
    cap_id = registry.snapshot().capabilities[0]["id"]
    v = capability_versions.register_version(
        cap_id, semver="1.0.0", changelog="x", created_by="x", status="approved",
    )
    with pytest.raises(ValueError):
        capability_versions.promote(v.version_id, target_status="draft")


def test_rollback_swaps_approved(registry):
    cap_id = registry.snapshot().capabilities[0]["id"]
    v1 = capability_versions.register_version(cap_id, semver="1.0.0", changelog="x", created_by="x")
    capability_versions.promote(v1.version_id, target_status="approved", approver="x")
    v2 = capability_versions.register_version(cap_id, semver="2.0.0", changelog="y", created_by="x")
    capability_versions.promote(v2.version_id, target_status="approved", approver="x")
    # v1 is deprecated, v2 approved. Roll back to v1.
    rolled = capability_versions.rollback(cap_id, target_version_id=v1.version_id, actor="x")
    assert rolled.status == "approved"
    # And v2 is now deprecated
    assert capability_versions.get_version(v2.version_id).status == "deprecated"


def test_compare_returns_diff_shape(registry):
    cap_id = registry.snapshot().capabilities[0]["id"]
    v1 = capability_versions.register_version(
        cap_id, semver="1.0.0", changelog="x", created_by="x",
        prompt_snapshot="first prompt body",
    )
    v2 = capability_versions.register_version(
        cap_id, semver="1.1.0", changelog="y", created_by="x",
        prompt_snapshot="second prompt body, longer this time",
    )
    diff = capability_versions.compare(v1.version_id, v2.version_id)
    assert "manifest_diff" in diff
    assert "prompt_diff_summary" in diff


def test_resolve_version_for_call_picks_approved(registry):
    cap_id = registry.snapshot().capabilities[0]["id"]
    v = capability_versions.register_version(cap_id, semver="1.0.0", changelog="x", created_by="x")
    capability_versions.promote(v.version_id, target_status="approved", approver="x")
    resolved = capability_versions.resolve_version_for_call(cap_id)
    assert resolved.version_id == v.version_id


# ── prompt_diff ────────────────────────────────────────────────────────


def test_diff_prompts_smoke(registry):
    cap_id = registry.snapshot().capabilities[0]["id"]
    v1 = capability_versions.register_version(cap_id, semver="1.0.0", changelog="x", created_by="x",
                                                prompt_snapshot="line one\nline two\nline three")
    v2 = capability_versions.register_version(cap_id, semver="1.1.0", changelog="y", created_by="x",
                                                prompt_snapshot="line one\nline TWO improved\nline three\nline four")
    d = prompt_diff.diff_prompts(v1.version_id, v2.version_id)
    assert d.added_lines >= 1
    assert d.delta_words != 0


def test_diff_executions_returns_verdict(registry):
    cap_id = registry.snapshot().capabilities[0]["id"]
    v1 = capability_versions.register_version(cap_id, semver="1.0.0", changelog="x", created_by="x")
    v2 = capability_versions.register_version(cap_id, semver="2.0.0", changelog="y", created_by="x")
    d = prompt_diff.diff_executions(v1.version_id, v2.version_id)
    assert d.verdict == "insufficient_data"


# ── workspaces ─────────────────────────────────────────────────────────


def test_create_and_get_workspace(registry):
    ws = workspaces.create_workspace(
        workspace_id="sales-na", name="Sales NA",
        owner={"name": "alice"}, department="Sales",
    )
    fetched = workspaces.get_workspace("sales-na")
    assert fetched is not None
    assert fetched.name == "Sales NA"


def test_duplicate_workspace_id_rejected(registry):
    workspaces.create_workspace(workspace_id="xyz", name="X", owner={"name": "a"})
    with pytest.raises(ValueError):
        workspaces.create_workspace(workspace_id="xyz", name="X2", owner={"name": "a"})


def test_attach_capability_idempotent(registry):
    workspaces.create_workspace(workspace_id="ops", name="Ops", owner={"name": "a"})
    workspaces.attach_capability("ops", "cap_a")
    workspaces.attach_capability("ops", "cap_a")
    ws = workspaces.get_workspace("ops")
    assert ws.capability_ids == ["cap_a"]


# ── builder ────────────────────────────────────────────────────────────


def test_builder_rejects_short_intent(registry):
    with pytest.raises(ValueError):
        builder.generate("hi")


def test_builder_generates_draft_with_signals(registry, monkeypatch):
    from execution import llm_client
    monkeypatch.setattr(llm_client, "is_available", lambda: False)
    draft = builder.generate("Summarize a proposal for the sales team",
                              department="Sales")
    assert draft.draft_id
    assert isinstance(draft.confidence_score, float)
    assert isinstance(draft.warnings, list)


def test_builder_publish_unknown_draft_returns_error(registry):
    result = builder.publish_draft("nonexistent")
    assert "error" in result


# ── optimizer simulate / apply ────────────────────────────────────────


def test_optimizer_simulate_unknown_returns_none(registry):
    assert workflow_optimizer.simulate("does:not:exist") is None


def test_optimizer_apply_records_audit(registry):
    cap = registry.snapshot().capabilities[0]
    for i in range(8):
        workflow_runner._persist(RunRecord(
            run_id=f"e{i}", capability_id=cap["id"],
            started_at="2026-05-26T10:00:00+00:00",
            finished_at="2026-05-26T10:00:01+00:00",
            status="error", error_message="boom",
        ))
    suggestions = workflow_optimizer.analyze(registry=registry)
    target = next(s for s in suggestions if s.kind == "DECOMPOSE_CAPABILITY")
    result = workflow_optimizer.apply(target.suggestion_id, actor="alice")
    assert result["status"] == "acknowledged"
    # Verify audit row
    rows = audit_log.list_entries(action="optimizer.acknowledged")
    assert any(r["entity_id"] == target.suggestion_id for r in rows)


# ── executive reporting ───────────────────────────────────────────────


def test_executive_scorecard_renders(registry):
    sc = executive_reporting.executive_scorecard(registry=registry)
    assert sc.capability_count >= 1
    assert isinstance(sc.top_value_capabilities, list)


def test_monthly_report_persists(registry):
    now = datetime.now(timezone.utc)
    report = executive_reporting.monthly_report(now.year, now.month, registry=registry)
    assert report["year"] == now.year
    listed = executive_reporting.list_monthly_reports()
    assert any(r["month"] == now.month for r in listed)


# ── telemetry ─────────────────────────────────────────────────────────


def test_health_summary_smoke(registry):
    s = telemetry.health_summary(registry=registry)
    assert hasattr(s, "total_runs_24h")


def test_cache_freshness_returns_topics(registry):
    cf = telemetry.cache_freshness_seconds()
    assert "run_recorded" in cf


def test_telemetry_snapshot_persists(registry):
    path = telemetry.snapshot(registry=registry)
    assert path.exists()


def test_dependency_health_smoke(registry):
    out = telemetry.dependency_health(registry=registry)
    assert "declared_mcp" in out
    assert "unused_mcp" in out


# ── adoption ──────────────────────────────────────────────────────────


def test_badges_new_capability_gets_new_badge(registry):
    cap_id = registry.snapshot().capabilities[0]["id"]
    badges = adoption.badges_for(cap_id)
    assert any(b["kind"] == "new" for b in badges)


def test_badges_production_safe_at_25_runs_90pct(registry):
    cap_id = registry.snapshot().capabilities[0]["id"]
    for i in range(28):
        workflow_runner._persist(RunRecord(
            run_id=f"p{i}", capability_id=cap_id,
            started_at="2026-05-26T10:00:00+00:00",
            finished_at="2026-05-26T10:00:01+00:00",
            status="succeeded",
        ))
    badges = adoption.badges_for(cap_id)
    kinds = [b["kind"] for b in badges]
    assert "production_safe" in kinds


def test_mode_for_user_runs():
    assert adoption.mode_for(0) == "beginner"
    assert adoption.mode_for(10) == "intermediate"
    assert adoption.mode_for(100) == "expert"


def test_confidence_indicator_low_when_no_runs(registry):
    cap_id = registry.snapshot().capabilities[0]["id"]
    ci = adoption.confidence_indicator(cap_id)
    assert ci["level"] == "low"


def test_estimated_completion_time_returns_seconds(registry):
    cap_id = registry.snapshot().capabilities[0]["id"]
    for i in range(3):
        workflow_runner._persist(RunRecord(
            run_id=f"t{i}", capability_id=cap_id,
            started_at="2026-05-26T10:00:00+00:00",
            finished_at="2026-05-26T10:00:01+00:00",
            status="succeeded", duration_ms=1500 + i * 200,
        ))
    et = adoption.estimated_completion_time(cap_id)
    assert et["seconds"] is not None
    assert et["sample_count"] == 3


# ── errors helper ─────────────────────────────────────────────────────


def test_ops_error_serializes():
    err = not_found("widget", "abc")
    body = err.to_dict()
    assert body["error"]["code"] == "widget.not_found"
    assert "correlation_id" in body["error"]
