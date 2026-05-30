"""Tests for execution/ops_platform/training_agent.py"""

import pytest

from execution.ops_platform.capability_registry import CapabilityRegistry
from execution.ops_platform.plugin_loader import load_plugins
from execution.ops_platform import training_agent


@pytest.fixture
def registry(fake_plugin_root, tmp_path, monkeypatch):
    import execution.ops_platform.capability_registry as crm
    monkeypatch.setattr(crm, "_STATS_PATH", tmp_path / "stats.json")
    monkeypatch.setattr(training_agent, "_TRAINING_DIR", tmp_path / "training")
    from execution import llm_client
    monkeypatch.setattr(llm_client, "is_available", lambda: False)
    return CapabilityRegistry(load_fn=lambda: load_plugins(root=fake_plugin_root))


def test_unknown_capability_raises(registry):
    with pytest.raises(ValueError):
        training_agent.generate_training("nope", registry=registry)


def test_fallback_walkthrough_contains_sections(registry):
    result = training_agent.generate_training("test_summary", registry=registry)
    assert result.llm_used is False
    assert "What this does" in result.markdown
    assert "Walkthrough" in result.markdown
    assert "tips" in result.markdown.lower()


def test_walkthrough_persisted(registry, tmp_path):
    result = training_agent.generate_training("test_summary", registry=registry)
    md = training_agent.get_training_markdown("test_summary")
    assert md is not None
    assert md == result.markdown


def test_get_training_returns_none_when_missing(registry):
    assert training_agent.get_training_markdown("test_compose") is None
