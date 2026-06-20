"""Tests for the runtime control store (the real kill-switch) + worker obedience.

Isolated to tmp_path so no real control/audit/heartbeat state is touched.
"""

import pytest

from execution.ops_platform import audit_log, runtime_controls


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_controls, "_STORE_PATH", tmp_path / "runtime_controls.json")
    monkeypatch.setattr(audit_log, "_AUDIT_DIR", tmp_path / "audit")
    yield


# ── store ──


def test_default_not_paused():
    assert runtime_controls.is_paused("cb_mention_responder") is False


def test_per_agent_pause_resume():
    runtime_controls.set_agent_paused("autopickup_worker", True, actor="ali", reason="t")
    assert runtime_controls.is_paused("autopickup_worker") is True
    assert runtime_controls.is_paused("cb_mention_responder") is False  # others unaffected
    runtime_controls.set_agent_paused("autopickup_worker", False, actor="ali")
    assert runtime_controls.is_paused("autopickup_worker") is False


def test_global_pause_stops_everything():
    runtime_controls.set_global_paused(True, actor="ali", reason="kill")
    for aid in runtime_controls.KNOWN_RUNTIME_AGENTS:
        assert runtime_controls.is_paused(aid) is True
    runtime_controls.set_global_paused(False, actor="ali")
    for aid in runtime_controls.KNOWN_RUNTIME_AGENTS:
        assert runtime_controls.is_paused(aid) is False


def test_get_state_lists_known_agents():
    s = runtime_controls.get_state()
    assert set(s["agents"]) >= set(runtime_controls.KNOWN_RUNTIME_AGENTS)
    assert s["global_paused"] is False


def test_mutations_are_audited():
    runtime_controls.set_global_paused(True, actor="ali", reason="kill")
    rows = audit_log.list_entries(action="runtime.global_paused", days=1)
    assert len(rows) >= 1
    assert rows[0]["entity_type"] == "runtime_control"


# ── workers obey the store (end-to-end, no monkeypatch of is_paused) ──


def test_cb_mention_worker_honors_global_pause(monkeypatch, tmp_path):
    from execution.products.ops import cb_mention_worker
    monkeypatch.setattr(cb_mention_worker, "HEARTBEAT_PATH", tmp_path / "hb.json")
    runtime_controls.set_global_paused(True, actor="ali")
    out = cb_mention_worker.scan_all_users()
    assert out.get("reason") == "paused_by_operator"
    assert out.get("total_responded", 0) == 0


def test_autopickup_worker_honors_pause():
    from execution.products.ops import autopickup_worker
    runtime_controls.set_agent_paused("autopickup_worker", True, actor="ali")
    out = autopickup_worker.scan_all_users()
    assert out.get("status") == "paused_by_operator"


def test_productivity_runner_honors_pause():
    from execution.products.ops.productivity import runner
    runtime_controls.set_agent_paused("productivity_report", True, actor="ali")
    res = runner.run()
    assert res.status == "paused_by_operator"
