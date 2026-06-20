"""Tests for the TBI compliance scorer (execution/ops_platform/tbi_compliance.py).

Deterministic, no I/O except the audit-emission test (isolated to tmp_path).
"""

import pytest

from execution.ops_platform import audit_log, tbi_compliance


def _dim(status="satisfied", evidence="mapped to existing control"):
    return {"status": status, "evidence": evidence}


def _base_attestation():
    return {
        "artifact_id": "agent:test",
        "artifact_kind": "agent",
        "framework_version": tbi_compliance.CURRENT_FRAMEWORK_VERSION,
        "inpact": {k: _dim() for k in tbi_compliance.INPACT_DIMENSIONS},
        "goals": {k: _dim() for k in tbi_compliance.GOALS_TARGETS},
        "layers": [{"layer": 5, "how": "agent_registry autonomy policy"}],
        "verdict": "compliant",
        "approver": {"name": "Ali"},
        "verified_at": "2026-06-20T00:00:00Z",
    }


# ── Happy path ───────────────────────────────────────────────────────


def test_fully_satisfied_is_compliant():
    v = tbi_compliance.evaluate_attestation(_base_attestation())
    assert v.verdict == "compliant"
    assert v.passed is True
    assert v.blocking_issues == []
    assert v.inpact_satisfied == 6
    assert v.goals_satisfied == 5


def test_justified_na_is_conditional():
    att = _base_attestation()
    att["inpact"]["instant"] = _dim(status="n_a", evidence="static blueprint, no live latency")
    v = tbi_compliance.evaluate_attestation(att)
    assert v.verdict == "conditional"
    assert v.passed is True
    assert any("not applicable" in w for w in v.warnings)


# ── Blocking failures ────────────────────────────────────────────────


def test_unjustified_na_blocks():
    att = _base_attestation()
    att["goals"]["availability"] = {"status": "n_a", "evidence": "  "}
    v = tbi_compliance.evaluate_attestation(att)
    assert v.verdict == "non_compliant"
    assert any("without written justification" in b for b in v.blocking_issues)


def test_gap_status_blocks():
    att = _base_attestation()
    att["inpact"]["transparent"] = _dim(status="gap")
    v = tbi_compliance.evaluate_attestation(att)
    assert v.verdict == "non_compliant"
    assert any("gap" in b for b in v.blocking_issues)


def test_missing_dimension_blocks():
    att = _base_attestation()
    del att["inpact"]["permitted"]
    v = tbi_compliance.evaluate_attestation(att)
    assert v.verdict == "non_compliant"
    assert any("inpact.permitted" in b for b in v.blocking_issues)


def test_framework_version_mismatch_blocks():
    att = _base_attestation()
    att["framework_version"] = "TBI-1999.01.0"
    v = tbi_compliance.evaluate_attestation(att)
    assert v.verdict == "non_compliant"
    assert any("framework_version" in b for b in v.blocking_issues)


def test_no_layers_blocks():
    att = _base_attestation()
    att["layers"] = []
    v = tbi_compliance.evaluate_attestation(att)
    assert v.verdict == "non_compliant"
    assert any("layers" in b for b in v.blocking_issues)


def test_non_dict_attestation_blocks():
    v = tbi_compliance.evaluate_attestation("not-an-object")
    assert v.verdict == "non_compliant"


# ── Trust profile gating ─────────────────────────────────────────────


def test_trust_do_not_deploy_blocks():
    att = _base_attestation()
    att["trust_score_ref"] = "cap-x"
    v = tbi_compliance.evaluate_attestation(
        att, trust_profile={"deployment_recommendation": "DO_NOT_DEPLOY"})
    assert v.verdict == "non_compliant"
    assert any("DO_NOT_DEPLOY" in b for b in v.blocking_issues)


def test_trust_requires_review_is_conditional():
    att = _base_attestation()
    v = tbi_compliance.evaluate_attestation(
        att, trust_profile={"deployment_recommendation": "REQUIRES_REVIEW"})
    assert v.verdict == "conditional"


def test_trust_safe_for_production_stays_compliant():
    att = _base_attestation()
    v = tbi_compliance.evaluate_attestation(
        att, trust_profile={"deployment_recommendation": "SAFE_FOR_PRODUCTION"})
    assert v.verdict == "compliant"


def test_trust_profile_attribute_access():
    class _Profile:
        deployment_recommendation = "DO_NOT_DEPLOY"
    v = tbi_compliance.evaluate_attestation(_base_attestation(), trust_profile=_Profile())
    assert v.verdict == "non_compliant"


# ── Determinism + audit emission ─────────────────────────────────────


def test_deterministic():
    att = _base_attestation()
    a = tbi_compliance.evaluate_attestation(att)
    b = tbi_compliance.evaluate_attestation(att)
    assert a.to_dict() == b.to_dict()


def test_emits_audit_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_log, "_AUDIT_DIR", tmp_path / "audit")
    tbi_compliance.evaluate_attestation(_base_attestation(), record_audit=True)
    rows = audit_log.list_entries(action="tbi.evaluated", days=1)
    assert len(rows) >= 1
    assert rows[0]["entity_id"] == "agent:test"
    assert rows[0]["metadata"]["verdict"] == "compliant"
