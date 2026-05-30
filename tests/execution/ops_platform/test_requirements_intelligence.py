"""Tests for execution/ops_platform/requirements_intelligence.py"""

import pytest

from execution.ops_platform import requirements_intelligence
from execution.ops_platform.requirements_intelligence import (
    extract_from_run,
    feed_into_project,
    load_aggregate,
)
from execution.ops_platform.workflow_runner import RunRecord


@pytest.fixture(autouse=True)
def isolate_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(requirements_intelligence, "_INTELLIGENCE_DIR", tmp_path / "intel")
    monkeypatch.setattr(requirements_intelligence, "_EXTRACTS_PATH", tmp_path / "intel" / "extracts.jsonl")
    monkeypatch.setattr(requirements_intelligence, "_AGGREGATE_PATH", tmp_path / "intel" / "aggregate.json")


def _run_with(response):
    return RunRecord(
        run_id="r1",
        capability_id="cap1",
        started_at="2026-05-26T00:00:00+00:00",
        finished_at="2026-05-26T00:00:05+00:00",
        status="succeeded",
        inputs={},
        response=response,
    )


def test_extract_from_failed_run_returns_none(make_response):
    run = _run_with(make_response())
    run.status = "contract_failed"
    assert extract_from_run(run) is None


def test_extract_mines_components_and_routes(make_response):
    run = _run_with(make_response(
        components_added=[{"name": "AlertService", "kind": "service", "purpose": "ring bells"}],
        routes_added=[{"method": "POST", "path": "/alerts", "handler": "AlertView.post", "purpose": "create alert"}],
    ))
    extract = extract_from_run(run)
    assert extract is not None
    reqs = extract["patterns"]["reusable_requirements"]
    assert any(r["name"] == "AlertService" for r in reqs)
    assert any(r["name"] == "POST /alerts" for r in reqs)


def test_extract_mines_architecture_decisions(make_response):
    run = _run_with(make_response(
        dependencies_added=[{"name": "redis", "version": "7", "ecosystem": "pip", "reason": "cache"}],
        database_changes=[{"change_type": "table_added", "target": "alerts", "description": "new"}],
    ))
    extract = extract_from_run(run)
    decisions = extract["patterns"]["architecture_decisions"]
    assert any(d["decision"] == "Use redis" for d in decisions)
    assert any(d["category"] == "database" for d in decisions)


def test_aggregate_accumulates_across_runs(make_response):
    for _ in range(3):
        run = _run_with(make_response(
            components_added=[{"name": "AlertService", "kind": "service"}],
        ))
        extract_from_run(run)
    agg = load_aggregate()
    assert agg["extract_count"] == 3
    # AlertService appears 3 times — seen_count should reflect that
    matching = [r for r in agg["reusable_requirements"] if r["name"] == "AlertService"]
    assert matching and matching[0]["seen_count"] == 3


def test_feed_into_project_returns_feature_shapes(make_response):
    for _ in range(2):
        run = _run_with(make_response(
            components_added=[{"name": "AlertService", "kind": "service", "purpose": "notify"}],
        ))
        extract_from_run(run)
    suggestions = feed_into_project("my-project", top_n=5)
    assert suggestions
    s = suggestions[0]
    # Should match the existing Requirement shape (feature.schema.json)
    for required_key in ["id", "name", "description", "type", "actor", "action", "priority", "traces_to"]:
        assert required_key in s
    assert s["type"] in ("core", "optional")
    assert s["priority"] in ("must", "should", "could", "wont")
