"""Tests for execution/ops_platform/semantic_analyzer.py"""

import json
from unittest.mock import patch

import pytest

from execution.llm_client import LLMResponse
from execution.ops_platform import semantic_analyzer
from execution.ops_platform.capability_registry import CapabilityRegistry
from execution.ops_platform.plugin_loader import load_plugins


@pytest.fixture
def registry(fake_plugin_root, tmp_path, monkeypatch):
    import execution.ops_platform.capability_registry as crm
    monkeypatch.setattr(crm, "_STATS_PATH", tmp_path / "registry_stats.json")
    monkeypatch.setattr(semantic_analyzer, "_CACHE_DIR", tmp_path / "semantic")
    return CapabilityRegistry(load_fn=lambda: load_plugins(root=fake_plugin_root))


def test_heuristic_enrichment_works_without_llm(registry, monkeypatch):
    from execution import llm_client
    monkeypatch.setattr(llm_client, "is_available", lambda: False)
    cap = registry.snapshot().capabilities[0]
    result = semantic_analyzer.enrich_capability(cap)
    assert result.source == "heuristic"
    assert result.payload["capability_id"] == cap["id"]
    assert "automation_potential" in result.payload
    assert isinstance(result.payload["semantic_tags"], list)
    assert 1 <= result.payload["complexity_score"] <= 5


def test_llm_enrichment_overrides_heuristic(registry, monkeypatch):
    from execution import llm_client
    monkeypatch.setattr(llm_client, "is_available", lambda: True)
    payload = {
        "operational_intent": "Summarize a proposal",
        "business_domains": ["Sales"],
        "recommended_departments": ["Sales"],
        "workflow_patterns": ["document_summarization"],
        "automation_potential": "fully_automatable",
        "complexity_score": 2,
        "reusability_score": 4,
        "business_impact_score": 4,
        "capability_similarity": [],
        "duplicate_candidates": [],
        "recommended_followup_workflows": [],
        "recommended_preceding_workflows": [],
        "execution_dependencies": [],
        "organizational_value_summary": "Saves time on proposals.",
        "recommended_user_personas": ["Account Executive"],
        "estimated_roi": "~30 min per run",
        "semantic_tags": ["sales", "proposal"],
    }
    def fake_chat(**kw):
        return LLMResponse(content=json.dumps(payload), model="t", usage={"prompt_tokens": 1, "completion_tokens": 1}, stop_reason="stop")
    monkeypatch.setattr(llm_client, "chat", fake_chat)

    cap = registry.snapshot().capabilities[0]
    result = semantic_analyzer.enrich_capability(cap)
    assert result.source == "llm"
    assert result.payload["automation_potential"] == "fully_automatable"


def test_cache_hit_skips_recomputation(registry, monkeypatch):
    from execution import llm_client
    monkeypatch.setattr(llm_client, "is_available", lambda: False)
    cap = registry.snapshot().capabilities[0]
    first = semantic_analyzer.enrich_capability(cap)
    assert first.from_cache is False

    second = semantic_analyzer.enrich_capability(cap)
    assert second.from_cache is True
    assert second.payload["capability_id"] == cap["id"]


def test_duplicate_detection_threshold(registry, monkeypatch):
    from execution import llm_client
    monkeypatch.setattr(llm_client, "is_available", lambda: False)
    semantic_analyzer.enrich_all(registry=registry)

    # Manually overwrite cache so two capabilities share identical tags
    for cap in registry.snapshot().capabilities[:2]:
        cached = semantic_analyzer._load_cached(cap["id"])
        cached["semantic_tags"] = ["alpha", "beta", "gamma", "delta"]
        (semantic_analyzer._CACHE_DIR / f"{cap['id']}.json").write_text(json.dumps(cached))

    dupes = semantic_analyzer.detect_duplicates(registry=registry, threshold=0.6)
    cap_ids = [c["id"] for c in registry.snapshot().capabilities[:2]]
    assert cap_ids[0] in dupes
    assert cap_ids[1] in dupes[cap_ids[0]]


def test_extract_json_handles_fenced(registry):
    assert semantic_analyzer._extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert semantic_analyzer._extract_json('garbage {"x": 2} trailing') == {"x": 2}
    assert semantic_analyzer._extract_json("not json") is None


def test_llm_failure_falls_back_to_heuristic(registry, monkeypatch):
    from execution import llm_client
    monkeypatch.setattr(llm_client, "is_available", lambda: True)
    def explode(**kw):
        raise llm_client.LLMClientError("boom")
    monkeypatch.setattr(llm_client, "chat", explode)
    cap = registry.snapshot().capabilities[0]
    result = semantic_analyzer.enrich_capability(cap)
    assert result.source == "heuristic"
