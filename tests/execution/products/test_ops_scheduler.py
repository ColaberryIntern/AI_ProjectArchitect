"""Regression guard for the background scheduler.

Root cause of a launch-long outage (found 2026-06-14): every job in
scheduler.start_scheduler() was added with `next_run_time=None`. In
APScheduler that PAUSES the job (it never fires) rather than running it
immediately. The whole background layer — My Day sync, stale-row purge,
@CB mention responder, auto-pickup — was silently dead: no cron ever ran,
so every user's data only refreshed on a manual dashboard visit and the
purge sweep never reconciled deleted-in-BC todos.

These tests assert the scheduler registers its jobs in a RUNNABLE state
(non-None next_run_time). The job bodies are neutralized so nothing does
real work if a (minutes-out) trigger somehow fired during the test.
"""
from __future__ import annotations

import pytest

from execution.products.ops import scheduler

_JOB_FUNCS = (
    "_sync_all_users",
    "_scan_cb_mentions",
    "_scan_autopickup",
    "_scan_autopickup_approve",
    "_purge_all_users",
    "_run_cb_smoke",
    "_run_cb_webhook_resubscribe",
)


@pytest.fixture(autouse=True)
def clean_scheduler(monkeypatch):
    # No scheduler left running from a prior import/test.
    try:
        scheduler.stop_scheduler()
    except Exception:
        pass
    scheduler._scheduler = None
    # Neutralize job bodies: registration is what we test, not execution.
    for fn in _JOB_FUNCS:
        monkeypatch.setattr(scheduler, fn, lambda *a, **k: None)
    yield
    try:
        scheduler.stop_scheduler()
    except Exception:
        pass
    scheduler._scheduler = None


def test_no_job_is_paused():
    """Every registered job must have a real next_run_time. A None value
    means paused — the exact bug that disabled the background layer."""
    scheduler.start_scheduler()
    jobs = scheduler._scheduler.get_jobs()
    assert jobs, "scheduler registered no jobs"
    paused = [j.id for j in jobs if j.next_run_time is None]
    assert not paused, f"paused jobs (next_run_time=None): {paused}"


def test_core_jobs_registered():
    """Sync + purge are the always-on jobs and must always be present."""
    scheduler.start_scheduler()
    ids = {j.id for j in scheduler._scheduler.get_jobs()}
    assert scheduler.JOB_ID in ids, "ops_sync job missing"
    assert scheduler.PURGE_JOB_ID in ids, "ops_purge job missing"
