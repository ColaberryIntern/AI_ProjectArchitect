"""Tests for the Trust Command Center read-only aggregator (Phase 10 v1).

Verifies the aggregator returns real, well-shaped data, never raises on partial
sources, and that the /admin/trust router is wired into the app.
"""

from execution.ops_platform import trust_center as tc


def test_snapshot_has_all_views_and_never_raises():
    snap = tc.snapshot()
    assert set(snap) >= {"executive", "operations", "governance", "audit"}


def test_overview_scorecard_and_cost_honesty():
    ov = tc.overview()
    assert ov["trust_score"]["overall"] == 74
    assert "governance" in ov["trust_score"]["pillars"]
    # Cost must be honestly labeled, never a fabricated number.
    assert ov["cost_7d"]["status"] == "not_instrumented"


def test_tbi_compliance_summary_scans_real_attestations():
    s = tc.tbi_compliance_summary()
    # We shipped 9 declarative + 4 runtime attestations = 13.
    assert s["total"] >= 13
    assert s["counts"]["non_compliant"] == 0
    assert s["framework_version"] == "TBI-2025.12.0"


def test_runtime_agents_summary_lists_four():
    s = tc.runtime_agents_summary()
    ids = {a["id"] for a in s["agents"]}
    assert {"cb_mention_responder", "autopickup_worker",
            "advisory_pipeline", "productivity_report"} <= ids


def test_sections_degrade_gracefully(monkeypatch):
    # Force a section to fail; aggregator must not raise.
    def boom():
        raise RuntimeError("simulated source outage")
    monkeypatch.setattr(tc, "tbi_compliance_summary", boom)
    ov = tc.overview()
    assert ov["compliance"]["status"] == "unavailable"


def test_router_wired_into_app():
    from app.main import app
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/admin/trust" in paths
    assert "/admin/trust/snapshot.json" in paths
