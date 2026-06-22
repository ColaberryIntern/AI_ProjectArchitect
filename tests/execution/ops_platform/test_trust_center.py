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
    # Cost is now instrumented (real ledger summary), not a placeholder.
    assert "usd_7d" in ov["cost_7d"]


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


# ── v2: layers (with tech stack), controls, live ──


def test_layers_seven_with_metric_tech_and_reference():
    out = tc.layers()
    layers = out["layers"]
    assert len(layers) == 7
    nums = [L["layer"] for L in layers]
    assert nums == [1, 2, 3, 4, 5, 6, 7]
    scored = 0
    for L in layers:
        assert L["metric"]["label"] and "value" in L["metric"]
        assert isinstance(L["tech"], list) and L["tech"]      # our stack present
        for ti in L["tech"]:
            assert "name" in ti                               # tech items are scored objects
            if ti.get("inpact"):
                scored += 1
        assert isinstance(L["reference"], list)               # framework reference present
    assert scored >= 1  # at least some of our stack carries a framework INPACT score
    # INPACT tags mapped onto the right layers
    by_num = {L["layer"]: L for L in layers}
    assert by_num[5]["tag"] == "Permitted"
    assert by_num[6]["tag"] == "Transparent"


def test_controls_state_shape():
    s = tc.controls_state()
    assert "runtime" in s and "active_controls" in s and "pending_approvals" in s
    assert isinstance(s["active_controls"], list)


def test_live_and_page_data_shape():
    live = tc.live()
    assert "layers" in live and "controls" in live and "counters" in live
    pd = tc.page_data()
    assert set(pd) >= {"overview", "layers", "controls"}


def test_control_endpoints_wired():
    from app.main import app
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/admin/trust/live.json" in paths
    assert "/admin/trust/layers.json" in paths
    assert "/admin/trust/control/global/{action}" in paths
    assert "/admin/trust/control/agent/{agent_id}/{action}" in paths
    assert "/admin/trust/control/freeze/{capability_id}" in paths


# ── drill-downs ──


def test_layer_detail_shapes():
    for n in range(1, 8):
        d = tc.layer_detail(n)
        assert d["layer"] == n and d["name"]
        assert isinstance(d["recent_events"], list)
        assert d["events_kind"] in ("audit", "workflow_runs")
        assert isinstance(d["tech"], list)
    assert "error" in tc.layer_detail(99)


def test_agent_detail_known_runtime_agent():
    d = tc.agent_detail("cb_mention_responder")
    assert d["agent_id"] == "cb_mention_responder"
    assert d["declared"] is True
    assert d["autonomy_policy"] == "autonomous_low_risk_only"
    assert isinstance(d["recent_audit"], list)
    # attestation is read from the committed sidecar
    assert d["attestation"] and d["attestation"]["verdict"] in ("compliant", "conditional", "non_compliant")


def test_agent_detail_unknown_is_safe():
    d = tc.agent_detail("does_not_exist")
    assert d["declared"] is False
    assert isinstance(d["recent_audit"], list)


def test_compliance_detail_lists_all_attestations():
    d = tc.compliance_detail()
    assert d["total"] >= 13
    assert d["counts"]["non_compliant"] == 0
    assert all("verdict" in it and "artifact_id" in it for it in d["items"])


def test_audit_detail_and_replay_shapes():
    ad = tc.audit_detail(days=7, limit=5)
    assert set(ad) >= {"filters", "stats", "recent"} and isinstance(ad["recent"], list)
    rp = tc.audit_replay("no-such-correlation")
    assert rp["correlation_id"] == "no-such-correlation" and isinstance(rp["chain"], list)


def test_drilldown_endpoints_wired():
    from app.main import app
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/admin/trust/layer/{n}.json" in paths
    assert "/admin/trust/agent/{agent_id}.json" in paths
    assert "/admin/trust/compliance.json" in paths
    assert "/admin/trust/audit/replay.json" in paths


# ── cost explorer ──


def test_cost_summary_and_detail_shapes():
    s = tc.cost_summary()
    assert {"usd_7d", "calls_7d", "by_model", "by_source", "instrumented_since"} <= set(s)
    d = tc.cost_detail()
    assert {"total_usd", "recent", "by_model"} <= set(d)


def test_cost_endpoint_wired():
    from app.main import app
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/admin/trust/cost.json" in paths


# ── availability / SLO ──


def test_status_for_thresholds():
    assert tc._status_for(None, 100) == "unknown"
    assert tc._status_for(50, 100) == "healthy"
    assert tc._status_for(250, 100) == "stale"
    assert tc._status_for(500, 100) == "down"


def test_availability_shape_and_classification():
    a = tc.availability()
    assert {"overall_pct", "agents", "app", "monitored", "healthy"} <= set(a)
    by = {x.get("id"): x for x in a["agents"]}
    assert {"cb_mention_responder", "advisory_pipeline", "productivity_report"} <= set(by)
    assert by["advisory_pipeline"]["status"] == "on_demand"   # request-driven


def test_availability_endpoint_wired():
    from app.main import app
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/admin/trust/availability.json" in paths


# ── runtime trust (reputation -> attestation wiring) ──


def test_runtime_trust_shape_and_band():
    t = tc.runtime_trust("cb_mention_responder")
    assert 0 <= t["trust_score"] <= 100
    assert t["band"] in ("STRONG", "GOOD", "FAIR", "WEAK")
    assert {"availability", "reliability", "governance", "compliance"} <= set(t["components"])


def test_agent_detail_includes_runtime_trust():
    d = tc.agent_detail("cb_mention_responder")
    assert d.get("trust") and "trust_score" in d["trust"]


# ── lexicon (GOALS-Lexicon enforcement) ──


def test_lexicon_summary_and_detail_shapes():
    s = tc.lexicon_summary()
    assert {"term_count", "forbidden_count", "violations", "blocking", "status"} <= set(s)
    assert s["blocking"] == 0  # committed fleet is clean
    d = tc.lexicon_detail()
    assert {"summary", "terms", "forbidden", "violations"} <= set(d)
    assert d["terms"] and d["forbidden"]


def test_lexicon_wired_into_live_and_page_data():
    assert "lexicon" in tc.live()["counters"]
    assert "lexicon" in tc.page_data()


def test_lexicon_endpoint_wired():
    from app.main import app
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/admin/trust/lexicon.json" in paths
