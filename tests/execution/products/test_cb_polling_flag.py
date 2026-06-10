"""Tests for the OPS_CB_MENTION_POLLING_ENABLED feature flag.

Polling-off is the operator's path to retire the 10-min cron once BC
webhooks (PR #10) have been verified to catch everything. These tests
cover all three integration points:

  1. cb_mention_worker.scan_all_users() short-circuits with a heartbeat
     that signals "intentional", not "stale".
  2. scheduler.start_scheduler() skips registering _scan_cb_mentions when
     the flag is off, and emits a WARNING-level log so misconfiguration
     surfaces in container logs.
  3. (Admin endpoint coverage lives in tests/app/.)

Default is ON; only the literal "false" (case-insensitive) disables.
"""
from __future__ import annotations

import importlib
import json

import pytest

from execution.products.ops import cb_mention_worker as cb


@pytest.fixture(autouse=True)
def _isolate_heartbeat_and_seen(tmp_path, monkeypatch):
    """Redirect HEARTBEAT_PATH + SEEN_PATH off the real prod paths."""
    monkeypatch.setattr(cb, "HEARTBEAT_PATH", tmp_path / "heartbeat.json")
    monkeypatch.setattr(cb, "SEEN_PATH", tmp_path / "seen.json")
    yield


# ── _polling_enabled() env parsing ──────────────────────────────────


@pytest.mark.parametrize("value", ["false", "False", "FALSE", "  false  "])
def test_polling_enabled_returns_false_for_false_variants(value, monkeypatch):
    monkeypatch.setenv("OPS_CB_MENTION_POLLING_ENABLED", value)
    assert cb._polling_enabled() is False


@pytest.mark.parametrize("value", ["true", "True", "TRUE", "1", "yes", "anything"])
def test_polling_enabled_returns_true_for_non_false_values(value, monkeypatch):
    monkeypatch.setenv("OPS_CB_MENTION_POLLING_ENABLED", value)
    assert cb._polling_enabled() is True


def test_polling_enabled_default_when_env_unset(monkeypatch):
    monkeypatch.delenv("OPS_CB_MENTION_POLLING_ENABLED", raising=False)
    assert cb._polling_enabled() is True


# ── scan_all_users() short-circuit + heartbeat write ────────────────


@pytest.mark.parametrize("value", ["false", "False", "FALSE"])
def test_scan_all_users_short_circuits_when_disabled(value, monkeypatch):
    monkeypatch.setenv("OPS_CB_MENTION_POLLING_ENABLED", value)
    # If the early-return is missing, the function will try to import
    # tenancy/vault and we'd see a different summary shape. The fact
    # that we don't need to stub them is itself a partial assertion.
    summary = cb.scan_all_users()
    assert summary["skipped"] is True
    assert summary["reason"] == "polling_disabled"
    assert summary["users_with_token"] == 0
    assert summary["total_mentions_found"] == 0
    assert summary["total_responded"] == 0
    assert summary["total_failed"] == 0
    assert summary["fatal_error"] is None
    assert summary["per_user"] == []


def test_scan_all_users_writes_heartbeat_even_when_skipped(monkeypatch):
    monkeypatch.setenv("OPS_CB_MENTION_POLLING_ENABLED", "false")
    cb.scan_all_users()
    assert cb.HEARTBEAT_PATH.exists()
    loaded = json.loads(cb.HEARTBEAT_PATH.read_text(encoding="utf-8"))
    assert loaded["skipped"] is True
    assert loaded["reason"] == "polling_disabled"


def test_scan_all_users_proceeds_when_env_unset(monkeypatch):
    monkeypatch.delenv("OPS_CB_MENTION_POLLING_ENABLED", raising=False)
    monkeypatch.setattr(
        "execution.products.library.tenancy.list_users",
        lambda active_only=True: [],
    )
    summary = cb.scan_all_users()
    assert "skipped" not in summary
    assert summary["users_with_token"] == 0
    assert summary["fatal_error"] is None


def test_scan_all_users_proceeds_when_env_true(monkeypatch):
    monkeypatch.setenv("OPS_CB_MENTION_POLLING_ENABLED", "true")
    monkeypatch.setattr(
        "execution.products.library.tenancy.list_users",
        lambda active_only=True: [],
    )
    summary = cb.scan_all_users()
    assert "skipped" not in summary
    assert summary["users_with_token"] == 0


def test_scan_all_users_proceeds_for_any_non_false_string(monkeypatch):
    monkeypatch.setenv("OPS_CB_MENTION_POLLING_ENABLED", "yes please")
    monkeypatch.setattr(
        "execution.products.library.tenancy.list_users",
        lambda active_only=True: [],
    )
    summary = cb.scan_all_users()
    assert "skipped" not in summary


# ── scheduler integration: job registration toggles on the flag ─────


def _reload_scheduler():
    """Re-import scheduler so module-level POLLING_ENABLED is re-evaluated
    against the current env. Returns the freshly-loaded module.
    """
    from execution.products.ops import scheduler as ops_sched
    return importlib.reload(ops_sched)


@pytest.fixture
def fresh_scheduler():
    """Reload + auto-stop the scheduler so the BackgroundScheduler thread
    doesn't leak across tests."""
    mod = _reload_scheduler()
    try:
        yield mod
    finally:
        try:
            mod.stop_scheduler()
        except Exception:
            pass
        # Reload one more time with the default env so other test files
        # see POLLING_ENABLED=True at import.
        _reload_scheduler()


def test_start_scheduler_skips_mention_job_when_flag_false(
        monkeypatch, fresh_scheduler):
    monkeypatch.setenv("OPS_CB_MENTION_POLLING_ENABLED", "false")
    mod = _reload_scheduler()
    assert mod.POLLING_ENABLED is False
    mod.start_scheduler()
    try:
        job_ids = {j.id for j in mod._scheduler.get_jobs()}
        assert mod.MENTION_JOB_ID not in job_ids
        # Sanity: the other jobs are still registered.
        assert mod.JOB_ID in job_ids
        assert mod.AUTOPICKUP_JOB_ID in job_ids
    finally:
        mod.stop_scheduler()


def test_start_scheduler_registers_mention_job_when_flag_true(
        monkeypatch, fresh_scheduler):
    monkeypatch.setenv("OPS_CB_MENTION_POLLING_ENABLED", "true")
    mod = _reload_scheduler()
    assert mod.POLLING_ENABLED is True
    mod.start_scheduler()
    try:
        job_ids = {j.id for j in mod._scheduler.get_jobs()}
        assert mod.MENTION_JOB_ID in job_ids
    finally:
        mod.stop_scheduler()


def test_start_scheduler_registers_mention_job_when_env_unset(
        monkeypatch, fresh_scheduler):
    monkeypatch.delenv("OPS_CB_MENTION_POLLING_ENABLED", raising=False)
    mod = _reload_scheduler()
    assert mod.POLLING_ENABLED is True
    mod.start_scheduler()
    try:
        job_ids = {j.id for j in mod._scheduler.get_jobs()}
        assert mod.MENTION_JOB_ID in job_ids
    finally:
        mod.stop_scheduler()


def test_start_scheduler_logs_warning_when_disabled(
        monkeypatch, caplog, fresh_scheduler):
    monkeypatch.setenv("OPS_CB_MENTION_POLLING_ENABLED", "false")
    mod = _reload_scheduler()
    with caplog.at_level("WARNING", logger=mod.logger.name):
        mod.start_scheduler()
        try:
            warnings = [r for r in caplog.records if r.levelname == "WARNING"]
            assert any("DISABLED" in r.message for r in warnings), \
                f"expected WARNING with 'DISABLED', got: {[r.message for r in warnings]}"
        finally:
            mod.stop_scheduler()
