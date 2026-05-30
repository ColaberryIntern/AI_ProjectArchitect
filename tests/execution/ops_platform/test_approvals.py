"""Phase 6E tests: approval runtime."""

import json

import pytest

from execution.ops_platform import approvals, audit_log, cache_bus


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(approvals, "_APPROVALS_DIR", tmp_path / "approvals")
    monkeypatch.setattr(audit_log, "_AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(cache_bus, "_VERSION_DIR", tmp_path / "versions")
    cache_bus.reset_for_tests()
    yield


def test_request_single_approval():
    req = approvals.request_approval(
        action="version.promote", entity_type="capability_version",
        entity_id="v1", requested_by="alice",
        single_approver_roles=["admin"],
    )
    assert req.state == "pending"
    assert len(req.stages) == 1


def test_single_approval_completes():
    req = approvals.request_approval(
        action="version.promote", entity_type="capability_version",
        entity_id="v1", requested_by="alice",
        single_approver_roles=["admin"],
    )
    updated = approvals.submit_decision(
        req.request_id, approver={"name": "bob", "roles": ["admin"]},
        decision="approved",
    )
    assert updated.state == "approved"
    assert updated.final_decision_at


def test_rejection_terminates_request():
    req = approvals.request_approval(
        action="x", entity_type="t", entity_id="e", requested_by="a",
    )
    updated = approvals.submit_decision(
        req.request_id, approver={"name": "b", "roles": ["admin"]},
        decision="rejected",
    )
    assert updated.state == "rejected"


def test_quorum_requires_n_approvals():
    req = approvals.request_approval(
        action="x", entity_type="t", entity_id="q1", requested_by="a",
        single_approver_roles=["admin"], quorum=2,
    )
    approvals.submit_decision(req.request_id, approver={"name": "b1", "roles": ["admin"]},
                                decision="approved")
    mid = approvals.get(req.request_id)
    assert mid.state == "in_progress"
    final = approvals.submit_decision(req.request_id, approver={"name": "b2", "roles": ["admin"]},
                                         decision="approved")
    assert final.state == "approved"


def test_multi_stage_advances():
    stages = [
        {"stage_name": "security_review", "required_roles": ["reviewer"], "quorum": 1, "decisions": []},
        {"stage_name": "ops_signoff", "required_roles": ["admin"], "quorum": 1, "decisions": []},
    ]
    req = approvals.request_approval(
        action="x", entity_type="t", entity_id="ms", requested_by="a",
        stages=stages,
    )
    s1 = approvals.submit_decision(req.request_id, approver={"name": "r", "roles": ["reviewer"]},
                                      decision="approved")
    assert s1.current_stage_index == 1
    assert s1.state == "in_progress"
    s2 = approvals.submit_decision(req.request_id, approver={"name": "a", "roles": ["admin"]},
                                      decision="approved")
    assert s2.state == "approved"


def test_decision_hash_present():
    req = approvals.request_approval(
        action="x", entity_type="t", entity_id="h", requested_by="a",
    )
    final = approvals.submit_decision(req.request_id, approver={"name": "b", "roles": ["admin"]},
                                         decision="approved", comment="LGTM")
    stage = final.stages[0]
    decisions = stage["decisions"]
    assert decisions and decisions[0].get("decision_hash")


def test_cancel_request():
    req = approvals.request_approval(action="x", entity_type="t", entity_id="c",
                                        requested_by="a")
    cancelled = approvals.cancel(req.request_id, actor="alice", reason="changed mind")
    assert cancelled.state == "cancelled"


def test_mark_executed_after_approval():
    req = approvals.request_approval(action="x", entity_type="t", entity_id="ex",
                                        requested_by="a")
    approvals.submit_decision(req.request_id, approver={"name": "b", "roles": ["admin"]},
                                 decision="approved")
    executed = approvals.mark_executed(req.request_id, execution_ref="run-123")
    assert executed.state == "executed"


def test_expire_sweep(monkeypatch):
    import json
    req = approvals.request_approval(action="x", entity_type="t", entity_id="ex",
                                        requested_by="a", ttl_hours=1)
    path = approvals._APPROVALS_DIR / f"{req.request_id}.json"
    row = json.loads(path.read_text())
    row["expires_at"] = "2020-01-01T00:00:00+00:00"
    path.write_text(json.dumps(row))
    expired = approvals.expire_stale()
    assert req.request_id in expired
