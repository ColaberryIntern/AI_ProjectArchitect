"""Phase 8B tests: agent_registry + agent_runtime + autonomy policy gating."""

import pytest

from execution.ops_platform import (
    agent_registry, agent_runtime, approvals, audit_log, cache_bus, controls,
    realtime_bus, runtime_queue, worker_coordination,
)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_registry, "_AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr(agent_runtime, "_EXECUTIONS_DIR", tmp_path / "executions")
    monkeypatch.setattr(approvals, "_APPROVALS_DIR", tmp_path / "approvals")
    monkeypatch.setattr(controls, "_CONTROLS_DIR", tmp_path / "controls")
    monkeypatch.setattr(runtime_queue, "_QUEUE_DIR", tmp_path / "queue")
    monkeypatch.setattr(worker_coordination, "_WORKERS_DIR", tmp_path / "workers")
    monkeypatch.setattr(audit_log, "_AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(realtime_bus, "_EVENTS_DIR", tmp_path / "events")
    monkeypatch.setattr(realtime_bus, "_SEQUENCE_PATH", tmp_path / "sequence.json")
    monkeypatch.setattr(cache_bus, "_VERSION_DIR", tmp_path / "versions")
    cache_bus.reset_for_tests()
    realtime_bus.reset_for_tests()
    controls._RATE_LIMIT_HITS.clear()
    yield


def _register(autonomy="recommend_only", confidence_threshold=0.7,
                permitted=("reclaim_stale_queue",), rollback_required=True):
    return agent_registry.register_agent(
        name="test-agent", description="x",
        autonomy_policy=autonomy,
        confidence_threshold=confidence_threshold,
        permitted_actions=list(permitted),
        rollback_required=rollback_required,
        created_by="alice",
    )


def test_register_validates_policy():
    with pytest.raises(ValueError):
        agent_registry.register_agent(
            name="x", description="x", autonomy_policy="bogus",
            confidence_threshold=0.5, permitted_actions=[],
        )


def test_register_validates_confidence():
    with pytest.raises(ValueError):
        agent_registry.register_agent(
            name="x", description="x", autonomy_policy="recommend_only",
            confidence_threshold=2.0, permitted_actions=[],
        )


def test_pause_then_resume():
    a = _register()
    paused = agent_registry.pause(a.agent_id, actor="alice")
    assert paused.paused
    resumed = agent_registry.resume(a.agent_id, actor="alice")
    assert not resumed.paused


def test_execute_without_reasoning_chain_raises():
    a = _register()
    with pytest.raises(agent_runtime.AutonomyViolation):
        agent_runtime.execute(
            agent_id=a.agent_id, action_kind="reclaim_stale_queue",
            target={}, inputs={},
            reasoning_chain=[],
            evidence_refs=[{"source": "test"}],
            confidence=0.9, rollback_plan="undo",
        )


def test_execute_without_evidence_raises():
    a = _register()
    with pytest.raises(agent_runtime.AutonomyViolation):
        agent_runtime.execute(
            agent_id=a.agent_id, action_kind="reclaim_stale_queue",
            target={}, inputs={},
            reasoning_chain=["because"],
            evidence_refs=[],
            confidence=0.9, rollback_plan="undo",
        )


def test_paused_agent_blocks_execution():
    a = _register()
    agent_registry.pause(a.agent_id)
    ex = agent_runtime.execute(
        agent_id=a.agent_id, action_kind="reclaim_stale_queue",
        target={}, inputs={},
        reasoning_chain=["test"],
        evidence_refs=[{"source": "test"}],
        confidence=0.9, rollback_plan="undo",
    )
    assert ex.outcome == "PAUSED"


def test_action_not_permitted_denied():
    a = _register(permitted=("evict_stale_workers",))
    ex = agent_runtime.execute(
        agent_id=a.agent_id, action_kind="quarantine_capability",
        target={"entity_type": "capability", "entity_id": "cap"},
        inputs={},
        reasoning_chain=["test"], evidence_refs=[{"source": "test"}],
        confidence=0.9, rollback_plan="undo",
    )
    assert ex.outcome == "DENIED"
    assert "permitted_actions" in ex.detail


def test_below_confidence_threshold_denied():
    a = _register(autonomy="autonomous_full", confidence_threshold=0.8)
    ex = agent_runtime.execute(
        agent_id=a.agent_id, action_kind="reclaim_stale_queue",
        target={}, inputs={},
        reasoning_chain=["test"], evidence_refs=[{"source": "test"}],
        confidence=0.5, rollback_plan="undo",
    )
    assert ex.outcome == "DENIED"
    assert "below agent threshold" in ex.detail


def test_missing_rollback_plan_denied():
    a = _register(autonomy="autonomous_full", rollback_required=True)
    ex = agent_runtime.execute(
        agent_id=a.agent_id, action_kind="reclaim_stale_queue",
        target={}, inputs={},
        reasoning_chain=["test"], evidence_refs=[{"source": "test"}],
        confidence=0.9, rollback_plan="",
    )
    assert ex.outcome == "DENIED"
    assert "rollback_plan" in ex.detail


def test_recommend_only_never_applies():
    a = _register(autonomy="recommend_only")
    ex = agent_runtime.execute(
        agent_id=a.agent_id, action_kind="reclaim_stale_queue",
        target={}, inputs={},
        reasoning_chain=["test"], evidence_refs=[{"source": "test"}],
        confidence=0.95, rollback_plan="undo",
    )
    assert ex.outcome == "SUGGESTED"


def test_approval_required_creates_approval():
    a = _register(autonomy="approval_required")
    ex = agent_runtime.execute(
        agent_id=a.agent_id, action_kind="reclaim_stale_queue",
        target={"entity_type": "capability", "entity_id": "cap"},
        inputs={},
        reasoning_chain=["queue stuck", "needs reclaim"],
        evidence_refs=[{"source": "reliability", "kind": "retry_storm"}],
        confidence=0.9, rollback_plan="re-pend nothing — operation is idempotent",
    )
    assert ex.outcome == "APPROVAL_REQUIRED"
    assert ex.approval_request_id
    appr = approvals.get(ex.approval_request_id)
    assert appr is not None


def test_autonomous_low_risk_only_blocks_medium_risk():
    a = _register(autonomy="autonomous_low_risk_only")
    ex = agent_runtime.execute(
        agent_id=a.agent_id, action_kind="reclaim_stale_queue",
        target={}, inputs={},
        reasoning_chain=["test"], evidence_refs=[{"source": "x"}],
        confidence=0.9, rollback_plan="undo", risk="medium",
    )
    assert ex.outcome == "DENIED"
    assert "autonomous_low_risk_only" in ex.detail


def test_autonomous_full_applies_concrete_action():
    a = _register(autonomy="autonomous_full",
                    permitted=("reclaim_stale_queue",))
    ex = agent_runtime.execute(
        agent_id=a.agent_id, action_kind="reclaim_stale_queue",
        target={}, inputs={},
        reasoning_chain=["queue stuck"], evidence_refs=[{"source": "x"}],
        confidence=0.9, rollback_plan="re-pend nothing", risk="low",
    )
    assert ex.outcome == "APPLIED"


def test_revoke_records_audit():
    a = _register(autonomy="autonomous_full", permitted=("reclaim_stale_queue",))
    ex = agent_runtime.execute(
        agent_id=a.agent_id, action_kind="reclaim_stale_queue",
        target={}, inputs={},
        reasoning_chain=["test"], evidence_refs=[{"source": "x"}],
        confidence=0.9, rollback_plan="undo", risk="low",
    )
    assert ex.outcome == "APPLIED"
    revoked = agent_runtime.revoke(ex.execution_id, actor="alice", reason="rethink")
    assert revoked.outcome == "ERROR"
    rows = audit_log.list_entries(action="agent.execution_revoked")
    assert any(r["entity_id"] == ex.execution_id for r in rows)
