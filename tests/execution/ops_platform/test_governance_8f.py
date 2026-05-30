"""Phase 8F tests: signed_audit + retention + access_reviews + governance_scorecards."""

import gzip
import json
import os
import time
from datetime import datetime, timedelta, timezone

import pytest

from execution.ops_platform import (
    access_reviews, approvals, audit_log, cache_bus, change_requests, controls,
    experiments, governance_scorecards, incidents, retention_policy,
    secrets, service_identities, signed_audit, workspaces,
)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_log, "_AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(signed_audit, "_SIGNED_DIR", tmp_path / "audit_signed")
    monkeypatch.setattr(access_reviews, "_REVIEWS_DIR", tmp_path / "access_reviews")
    monkeypatch.setattr(governance_scorecards, "_SCORECARDS_DIR", tmp_path / "scorecards")
    monkeypatch.setattr(service_identities, "_SERVICES_DIR", tmp_path / "service_ids")
    monkeypatch.setattr(approvals, "_APPROVALS_DIR", tmp_path / "approvals")
    monkeypatch.setattr(change_requests, "_CR_DIR", tmp_path / "crs")
    monkeypatch.setattr(incidents, "_INCIDENTS_DIR", tmp_path / "incidents")
    monkeypatch.setattr(experiments, "_EXPERIMENTS_DIR", tmp_path / "experiments")
    monkeypatch.setattr(workspaces, "_WORKSPACES_DIR", tmp_path / "workspaces")
    monkeypatch.setattr(controls, "_CONTROLS_DIR", tmp_path / "controls")
    monkeypatch.setattr(secrets, "_SECRETS_DIR", tmp_path / "secrets")
    monkeypatch.setattr(cache_bus, "_VERSION_DIR", tmp_path / "versions")
    controls._RATE_LIMIT_HITS.clear()
    cache_bus.reset_for_tests()
    yield


# ── signed_audit ─────────────────────────────────────────────────────


def test_sign_pending_writes_rows():
    audit_log.record(action="a", entity_type="t", entity_id="e", actor="alice")
    audit_log.record(action="b", entity_type="t", entity_id="e", actor="alice")
    signed = signed_audit.sign_pending(days_back=1)
    assert signed >= 2


def test_verify_chain_passes_when_intact():
    audit_log.record(action="x", entity_type="t", entity_id="e", actor="alice")
    signed_audit.sign_pending(days_back=1)
    report = signed_audit.verify_chain(days=1)
    assert report.valid is True
    assert report.rows_inspected >= 1


def test_verify_detects_tampered_row(tmp_path):
    audit_log.record(action="x", entity_type="t", entity_id="e", actor="alice")
    signed_audit.sign_pending(days_back=1)
    # Tamper with the most recent signed row
    files = sorted(signed_audit._SIGNED_DIR.glob("*.jsonl"))
    assert files
    raw = files[-1].read_text().splitlines()
    if raw:
        # Modify the canonical_row_json in the first row
        row = json.loads(raw[0])
        row["canonical_row_json"] = "{}"
        raw[0] = json.dumps(row)
        files[-1].write_text("\n".join(raw) + "\n")
    report = signed_audit.verify_chain(days=1)
    assert report.valid is False
    assert report.broken_reason


def test_sign_pending_is_idempotent():
    audit_log.record(action="a", entity_type="t", entity_id="e", actor="alice")
    n1 = signed_audit.sign_pending(days_back=1)
    n2 = signed_audit.sign_pending(days_back=1)
    assert n2 == 0


def test_signing_mode_reflects_secret_presence(monkeypatch, tmp_path):
    # Without secret → plain_chain
    assert signed_audit.signing_mode() == "plain_chain"
    # With secret → hmac
    monkeypatch.setenv("OPS_AUDIT_HMAC_KEY", "test-secret")
    assert signed_audit.signing_mode() == "hmac"


# ── retention_policy ─────────────────────────────────────────────────


def test_apply_policy_returns_list(tmp_path):
    audit_log.record(action="x", entity_type="t", entity_id="e", actor="alice")
    results = retention_policy.apply_policy()
    assert isinstance(results, list)


def test_archive_old_file_to_gz(tmp_path, monkeypatch):
    from execution.ops_platform import retention_policy as rp
    monkeypatch.setattr(rp, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(rp, "DEFAULT_POLICY",
                          {"test_dir": {"archive_days": 1, "hard_delete_days": 30}})
    target = tmp_path / "ops_platform" / "test_dir"
    target.mkdir(parents=True, exist_ok=True)
    file = target / "old.jsonl"
    file.write_text("hello\n")
    old_ts = time.time() - 86400 * 5
    os.utime(file, (old_ts, old_ts))
    rp.apply_policy()
    assert not file.exists()
    assert (target / "old.jsonl.gz").exists()


# ── access_reviews ───────────────────────────────────────────────────


def test_run_campaign_returns_summary():
    audit_log.record(action="x", entity_type="t", entity_id="e", actor="alice")
    audit_log.record(action="y", entity_type="t", entity_id="e", actor="bob")
    campaign = access_reviews.run_campaign(lookback_days=7)
    assert campaign.summary["total_actors"] >= 2


def test_orphaned_service_accounts_detected():
    si, _ = service_identities.create(display_name="dormant",
                                          roles=["viewer"])
    campaign = access_reviews.run_campaign(lookback_days=7)
    assert any(o["service_id"] == si.service_id for o in campaign.orphaned_service_accounts)


def test_campaign_persists_and_lists():
    campaign = access_reviews.run_campaign(lookback_days=7)
    listed = access_reviews.list_campaigns()
    assert any(c.campaign_id == campaign.campaign_id for c in listed)


# ── governance_scorecards ────────────────────────────────────────────


def test_build_scorecard_returns_components():
    card = governance_scorecards.build(workspace_id="global", lookback_days=7)
    assert hasattr(card, "approval_hygiene")
    assert hasattr(card, "rollback_readiness")
    assert hasattr(card, "audit_integrity")
    assert 0.0 <= card.overall_score <= 100.0


def test_pending_approvals_affect_hygiene():
    approvals.request_approval(action="x", entity_type="t", entity_id="e",
                                  requested_by="alice")
    card = governance_scorecards.build(workspace_id="global", lookback_days=7)
    assert card.approval_hygiene["pending_total"] >= 1


def test_persist_scorecard(tmp_path):
    card = governance_scorecards.build(workspace_id="global", lookback_days=7)
    governance_scorecards.persist(card)
    files = list(governance_scorecards._SCORECARDS_DIR.glob("global_*.json"))
    assert files
