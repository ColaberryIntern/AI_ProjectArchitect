"""End-to-end smoke: load plugins → search → run → feedback → verify → extract → suggest."""

import json
import pytest

from execution.llm_client import LLMResponse
from execution.ops_platform.capability_registry import CapabilityRegistry
from execution.ops_platform.plugin_loader import load_plugins
from execution.ops_platform import (
    feedback_store,
    requirements_intelligence,
    search_index,
    training_agent,
    verification_agent,
    workflow_runner,
)


@pytest.fixture
def env(fake_plugin_root, tmp_path, monkeypatch, make_response):
    """Wire every module to point at the same tmp_path scratch space."""
    import execution.ops_platform.capability_registry as crm

    monkeypatch.setattr(crm, "_STATS_PATH", tmp_path / "stats.json")
    monkeypatch.setattr(workflow_runner, "_RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(feedback_store, "_FEEDBACK_DIR", tmp_path / "feedback")
    monkeypatch.setattr(feedback_store, "_INDEX_PATH", tmp_path / "feedback" / "_index.json")
    monkeypatch.setattr(requirements_intelligence, "_INTELLIGENCE_DIR", tmp_path / "intel")
    monkeypatch.setattr(requirements_intelligence, "_EXTRACTS_PATH", tmp_path / "intel" / "extracts.jsonl")
    monkeypatch.setattr(requirements_intelligence, "_AGGREGATE_PATH", tmp_path / "intel" / "aggregate.json")
    monkeypatch.setattr(training_agent, "_TRAINING_DIR", tmp_path / "training")
    search_index.reset_index()

    # Mock LLM to return a complete response contract payload.
    from execution import llm_client
    monkeypatch.setattr(llm_client, "is_available", lambda: True)
    def fake_chat(**kwargs):
        # Verification calls go through here too — return contract for any caller.
        if "Verification" in kwargs.get("system_prompt", ""):
            return LLMResponse(
                content=json.dumps({
                    "completed_requirements": ["summary"],
                    "partial_requirements": [],
                    "missing_requirements": [],
                    "architecture_issues": [],
                    "ui_issues": [],
                    "technical_debt": [],
                    "recommendations": ["Add monitoring."],
                    "deployment_readiness": "green",
                }),
                model="test", usage={}, stop_reason="stop",
            )
        return LLMResponse(
            content=json.dumps(make_response(
                summary="Workflow done.",
                components_added=[{"name": "ReportService", "kind": "service", "purpose": "compose reports"}],
                routes_added=[{"method": "POST", "path": "/reports", "handler": "ReportView.post", "purpose": "create report"}],
                verification_steps=[{"step": "open /reports", "expected": "200 OK"}],
                tests_written=[{"path": "tests/x.py", "count": 1, "scope": "unit"}],
            )),
            model="test", usage={"prompt_tokens": 10, "completion_tokens": 20}, stop_reason="stop",
        )
    monkeypatch.setattr(llm_client, "chat", fake_chat)

    return CapabilityRegistry(load_fn=lambda: load_plugins(root=fake_plugin_root))


def test_end_to_end_user_journey(env, fake_plugin_root):
    registry = env

    # 1. Discover via search
    hits = search_index.search("compose", registry=registry)
    assert any(h.capability_id == "test_compose" for h in hits)

    # 2. Run a workflow
    run = workflow_runner.run_workflow("test_summary", {"text": "hi"}, registry=registry)
    assert run.status == "succeeded"

    # 3. Submit feedback
    feedback_store.submit_feedback({
        "capability_id": "test_summary",
        "run_id": run.run_id,
        "submitter": {"name": "Smoke"},
        "ratings": {"usefulness": 5, "accuracy": 4, "time_savings": 5, "reliability": 4},
    }, registry=registry)
    cap = registry.get("test_summary")
    assert cap["ratings"]["total_feedback"] == 1

    # 4. Verify the run
    verification = verification_agent.verify_run(run.run_id, registry=registry, use_llm=True)
    assert verification.payload["deployment_readiness"] in ("green", "yellow")
    assert verification.llm_used is True

    # 5. Generate training walkthrough
    training = training_agent.generate_training("test_summary", registry=registry)
    assert training.output_path
    assert len(training.markdown) > 100

    # 6. Requirements intelligence has captured the run's patterns
    aggregate = requirements_intelligence.load_aggregate()
    assert aggregate["extract_count"] >= 1
    suggestions = requirements_intelligence.feed_into_project("my-project")
    assert suggestions
    # The mined component should appear as a Requirement candidate.
    assert any("ReportService" in s.get("name", "") for s in suggestions)
