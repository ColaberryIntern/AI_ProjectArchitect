"""Tests for execution/ops_platform/marketplace.py"""

import json

import pytest

from execution.ops_platform import (
    audit_log, cache_bus, marketplace, pipeline_engine, workspaces,
)
from execution.ops_platform.capability_registry import CapabilityRegistry
from execution.ops_platform.plugin_loader import load_plugins


@pytest.fixture
def registry(fake_plugin_root, tmp_path, monkeypatch):
    import execution.ops_platform.capability_registry as crm
    monkeypatch.setattr(crm, "_STATS_PATH", tmp_path / "registry_stats.json")
    monkeypatch.setattr(audit_log, "_AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(marketplace, "_MARKET_DIR", tmp_path / "marketplace")
    monkeypatch.setattr(marketplace, "_FORKED_DIR", tmp_path / "forked_capabilities")
    monkeypatch.setattr(workspaces, "_WORKSPACES_DIR", tmp_path / "workspaces")
    monkeypatch.setattr(pipeline_engine, "_PIPELINES_DIR", tmp_path / "pipelines")
    monkeypatch.setattr(pipeline_engine, "_PLUGIN_PIPELINES_DIR", tmp_path / "plugin_pipelines")
    monkeypatch.setattr(cache_bus, "_VERSION_DIR", tmp_path / "versions")
    # marketplace.fork writes capability lineage to OUTPUT_DIR/forked_capabilities
    from config import settings
    monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
    cache_bus.reset_for_tests()
    reg = CapabilityRegistry(load_fn=lambda: load_plugins(root=fake_plugin_root))
    import execution.ops_platform.capability_registry as crm2
    monkeypatch.setattr(crm2, "_DEFAULT_REGISTRY", reg)
    return reg


def test_publish_capability_template(registry):
    cap_id = registry.snapshot().capabilities[0]["id"]
    tpl = marketplace.publish_capability_template(
        capability_id=cap_id, title="Reusable Summarizer",
        category="Sales", tags=["reusable", "sales"],
        published_by={"name": "alice"},
    )
    assert tpl.template_kind == "capability"
    assert tpl.source_id == cap_id
    rows = audit_log.list_entries(action="marketplace.published")
    assert any(r["entity_id"] == tpl.template_id for r in rows)


def test_publish_pipeline_template(registry, tmp_path):
    # Save a pipeline first
    manifest = {
        "pipeline_id": "tplpipe", "name": "P", "version": "1.0.0",
        "created_by": {"name": "x"},
        "steps": [{"step_id": "s1", "capability_id": "test_summary"}],
    }
    pipeline_engine.save_pipeline(manifest)
    tpl = marketplace.publish_pipeline_template(
        pipeline_id="tplpipe", title="Reusable Pipeline",
        category="Sales", published_by={"name": "alice"},
    )
    assert tpl.template_kind == "pipeline"


def test_list_filter_by_kind(registry):
    cap_id = registry.snapshot().capabilities[0]["id"]
    marketplace.publish_capability_template(
        capability_id=cap_id, title="A", category="Sales",
        published_by={"name": "x"},
    )
    out = marketplace.list_templates(template_kind="capability")
    assert len(out) >= 1
    assert all(t.template_kind == "capability" for t in out)


def test_get_unknown_template_returns_none(registry):
    assert marketplace.get_template("nope") is None


def test_fork_pipeline_persists_new_manifest(registry):
    manifest = {
        "pipeline_id": "forksrc", "name": "P", "version": "1.0.0",
        "created_by": {"name": "x"},
        "steps": [{"step_id": "s1", "capability_id": "test_summary"}],
    }
    pipeline_engine.save_pipeline(manifest)
    tpl = marketplace.publish_pipeline_template(
        pipeline_id="forksrc", title="Forkable", category="Sales",
        published_by={"name": "x"},
    )
    result = marketplace.fork(tpl.template_id, actor={"name": "alice"})
    assert "forked_id" in result
    assert pipeline_engine.load_pipeline(result["forked_id"]) is not None
    # Lineage incremented
    refreshed = marketplace.get_template(tpl.template_id)
    assert refreshed.fork_count == 1
    assert result["forked_id"] in refreshed.derived_versions


def test_fork_capability_records_lineage(registry, tmp_path):
    cap_id = registry.snapshot().capabilities[0]["id"]
    tpl = marketplace.publish_capability_template(
        capability_id=cap_id, title="Forkable Cap", category="Sales",
        published_by={"name": "x"},
    )
    result = marketplace.fork(tpl.template_id, workspace_id=None,
                                actor={"name": "alice"})
    assert "forked_id" in result
    forked_path = tmp_path / "forked_capabilities" / f"{result['forked_id']}.json"
    assert forked_path.exists()


def test_fork_unknown_template_returns_error(registry):
    result = marketplace.fork("nope")
    assert result["error"] == "template not found"


def test_fork_audit_carries_correlation_id(registry):
    cap_id = registry.snapshot().capabilities[0]["id"]
    tpl = marketplace.publish_capability_template(
        capability_id=cap_id, title="X", category="Sales",
        published_by={"name": "x"},
    )
    marketplace.fork(tpl.template_id, actor={"name": "alice"},
                       correlation_id="corr-abc")
    rows = audit_log.list_entries(correlation_id="corr-abc")
    assert any(r["action"] == "marketplace.forked" for r in rows)
