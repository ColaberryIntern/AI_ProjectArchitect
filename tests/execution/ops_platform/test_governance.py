"""Phase 7E tests: change_requests + compliance_reports."""

import json

import pytest

from execution.ops_platform import (
    approvals, audit_log, cache_bus, change_requests, compliance_reports,
    controls, incidents,
)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(change_requests, "_CR_DIR", tmp_path / "crs")
    monkeypatch.setattr(approvals, "_APPROVALS_DIR", tmp_path / "approvals")
    monkeypatch.setattr(audit_log, "_AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(compliance_reports, "_REPORTS_DIR", tmp_path / "compliance")
    monkeypatch.setattr(controls, "_CONTROLS_DIR", tmp_path / "controls")
    monkeypatch.setattr(incidents, "_INCIDENTS_DIR", tmp_path / "incidents")
    monkeypatch.setattr(cache_bus, "_VERSION_DIR", tmp_path / "versions")
    controls._RATE_LIMIT_HITS.clear()
    cache_bus.reset_for_tests()
    yield


# ── change_requests ──────────────────────────────────────────────────


def test_draft_change_request():
    cr = change_requests.draft(
        title="Promote v2", action="version.promote",
        entity_type="capability_version", entity_id="v-x",
        proposed_change={"status": "approved"},
        rollback_plan="Re-approve v1",
        requested_by="alice",
    )
    assert cr.state == "draft"


def test_submit_creates_approval_and_advances_state():
    cr = change_requests.draft(
        title="t", action="x", entity_type="t", entity_id="e",
        proposed_change={}, rollback_plan="undo", requested_by="alice",
    )
    submitted = change_requests.submit(cr.cr_id,
                                          single_approver_roles=["admin"])
    assert submitted.state == "submitted"
    assert submitted.approval_request_id
    appr = approvals.get(submitted.approval_request_id)
    assert appr is not None


def test_sync_reflects_approval_outcome():
    cr = change_requests.draft(
        title="t", action="x", entity_type="t", entity_id="e",
        proposed_change={}, rollback_plan="undo", requested_by="alice",
    )
    change_requests.submit(cr.cr_id, single_approver_roles=["admin"])
    approvals.submit_decision(change_requests.get(cr.cr_id).approval_request_id,
                                 approver={"name": "bob", "roles": ["admin"]},
                                 decision="approved")
    synced = change_requests.sync_state_from_approval(cr.cr_id)
    assert synced.state == "approved"


def test_mark_executed_only_after_approval():
    cr = change_requests.draft(
        title="t", action="x", entity_type="t", entity_id="e",
        proposed_change={}, rollback_plan="undo", requested_by="alice",
    )
    # Cannot execute a draft
    result = change_requests.mark_executed(cr.cr_id)
    assert result.state != "executed"


def test_cancel_works_on_draft():
    cr = change_requests.draft(
        title="t", action="x", entity_type="t", entity_id="e",
        proposed_change={}, rollback_plan="undo", requested_by="alice",
    )
    cancelled = change_requests.cancel(cr.cr_id, actor="alice", reason="oops")
    assert cancelled.state == "cancelled"


# ── compliance_reports ───────────────────────────────────────────────


def test_operational_report_json_smoke():
    audit_log.record(action="test.x", entity_type="t", entity_id="e", actor="alice")
    report = compliance_reports.operational_report(days=7, format="json")
    assert "audit_summary" in report
    assert "access_review" in report


def test_operational_report_csv():
    audit_log.record(action="test.x", entity_type="t", entity_id="e", actor="alice")
    out = compliance_reports.operational_report(days=7, format="csv")
    assert "actor" in out


def test_operational_report_markdown():
    audit_log.record(action="test.x", entity_type="t", entity_id="e", actor="alice")
    out = compliance_reports.operational_report(days=7, format="markdown")
    assert out.startswith("# ")


def test_operational_report_invalid_format():
    with pytest.raises(ValueError):
        compliance_reports.operational_report(days=7, format="rtf")


def test_access_review_groups_by_actor():
    audit_log.record(action="a", entity_type="t", entity_id="e", actor="alice")
    audit_log.record(action="b", entity_type="t", entity_id="e", actor="alice")
    audit_log.record(action="a", entity_type="t", entity_id="e", actor="bob")
    review = compliance_reports.access_review(days=7)
    assert "alice" in review["actors"]
    assert review["actors"]["alice"]["action_count"] >= 2


def test_export_to_file_writes_path(tmp_path):
    audit_log.record(action="x", entity_type="t", entity_id="e", actor="alice")
    path = compliance_reports.export_to_file(days=7, format="markdown")
    assert path.exists()
    assert path.suffix == ".md"
