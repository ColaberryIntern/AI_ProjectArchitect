"""Job-registration tests for the cb_webhooks resubscribe cron.

These exercise scheduler.start_scheduler's gating: the daily resubscribe
job must only appear in _scheduler.get_jobs() when OPS_CB_WEBHOOK_SECRET
is set; otherwise it's omitted so /admin doesn't claim a job that would
no-op anyway.
"""
from __future__ import annotations

import pytest

from execution.products.ops import scheduler


@pytest.fixture
def _stop_scheduler():
    """Ensure each test leaves the module-level _scheduler stopped, even
    on assertion failure — otherwise APScheduler's background thread
    leaks into the next test and add_job raises."""
    yield
    scheduler.stop_scheduler()


def test_resubscribe_job_registered_when_secret_set(monkeypatch, _stop_scheduler):
    monkeypatch.setenv("OPS_CB_WEBHOOK_SECRET", "s3cret")
    scheduler.start_scheduler()
    job_ids = {j.id for j in scheduler._scheduler.get_jobs()}
    assert scheduler.RESUBSCRIBE_JOB_ID in job_ids
    assert scheduler.RESUBSCRIBE_JOB_ID == "ops_cb_webhook_resubscribe_daily"


def test_resubscribe_job_absent_when_secret_unset(monkeypatch, _stop_scheduler):
    monkeypatch.delenv("OPS_CB_WEBHOOK_SECRET", raising=False)
    scheduler.start_scheduler()
    job_ids = {j.id for j in scheduler._scheduler.get_jobs()}
    assert scheduler.RESUBSCRIBE_JOB_ID not in job_ids
    # Other jobs should still be registered — gating is specific to
    # resubscribe.
    assert scheduler.JOB_ID in job_ids
    assert scheduler.MENTION_JOB_ID in job_ids
