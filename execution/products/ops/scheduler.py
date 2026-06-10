"""Background scheduler for /my-day/ sync.

Runs every OPS_SYNC_INTERVAL_MINUTES (default 5) for every user whose
vault has a `basecamp_ai_clone` token. Each user's sync is wrapped in
try/except so one user failing never blocks the others.

Started/stopped from app/main.py lifespan, same pattern as the skill
scanner scheduler.
"""
from __future__ import annotations

import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

INTERVAL_MINUTES = int(os.environ.get("OPS_SYNC_INTERVAL_MINUTES", "5"))
MENTION_INTERVAL_MINUTES = int(os.environ.get("OPS_MENTION_INTERVAL_MINUTES", "10"))
AUTOPICKUP_INTERVAL_MINUTES = int(os.environ.get("OPS_AUTOPICKUP_INTERVAL_MINUTES", "15"))
APPROVE_INTERVAL_MINUTES = int(os.environ.get("OPS_AUTOPICKUP_APPROVE_INTERVAL_MINUTES", "5"))
# M6 (2026-06-09 audit): cron fires hourly; per-user purge.is_purge_due()
# then gates whether the actual purge runs for that user (default 24h
# per user). Hourly cron + 24h per-user gate means a user hitting their
# due time at minute 30 only waits ~30 min, not until midnight.
PURGE_CRON_MINUTES = int(os.environ.get("OPS_PURGE_CRON_MINUTES", "60"))
SMOKE_CRON_HOUR = int(os.environ.get("OPS_CB_SMOKE_CRON_HOUR", "3"))
SMOKE_CRON_TIMEZONE = os.environ.get("OPS_CB_SMOKE_CRON_TIMEZONE", "America/New_York")
JOB_ID = "ops_sync_all_users"
MENTION_JOB_ID = "ops_cb_mentions_all_users"
AUTOPICKUP_JOB_ID = "ops_autopickup_all_users"
APPROVE_JOB_ID = "ops_autopickup_approve_all_users"
PURGE_JOB_ID = "ops_purge_all_users"
SMOKE_JOB_ID = "ops_cb_smoke_nightly"

_scheduler: BackgroundScheduler | None = None


def _sync_all_users() -> None:
    """Walk every user with a vault token; pull their BC todos + re-score."""
    from execution.products.library import tenancy, vault
    from . import scorer, sync

    try:
        users = tenancy.list_users(active_only=True)
    except Exception:
        logger.warning("ops_sync: failed to list users", exc_info=True)
        return

    n_ran = 0
    n_skipped = 0
    n_already_running = 0
    n_failed = 0
    for u in users:
        # Only run for users that have a vault entry for basecamp_ai_clone
        try:
            has_token = any(
                c.tool_name == "basecamp_ai_clone"
                for c in vault.list_for_user(u.user_id, caller_id="ops_sync_cron")
            )
        except Exception:
            has_token = False
        if not has_token:
            n_skipped += 1
            continue
        try:
            r = sync.pull_todos_for_user(u.email)
            # already_running = a user-triggered sync is mid-flight. Skip
            # without firing the scorer — the user-triggered sync will
            # cover both. Without this, we'd race the manual sync and
            # potentially overwrite its fresh state with stale data.
            if r.get("status") == "already_running":
                n_already_running += 1
                continue
            if r.get("status") in ("ok", "partial"):
                scorer.score_all_todos(u.email)
            n_ran += 1
        except Exception:
            logger.warning("ops_sync: user %s failed", u.email, exc_info=True)
            n_failed += 1

    logger.info(
        "ops_sync cron: ran=%d skipped=%d already_running=%d failed=%d",
        n_ran, n_skipped, n_already_running, n_failed,
    )


def _scan_cb_mentions() -> None:
    from . import cb_mention_worker
    try:
        cb_mention_worker.scan_all_users()
    except Exception:
        logger.warning("ops_cb_mentions: scan_all_users threw", exc_info=True)


def _scan_autopickup() -> None:
    """[Auto-Pickup Worker] Phase 1 cron entrypoint. No-op unless
    OPS_AUTOPICKUP_ENABLED=true. Walks Phase 1 users (default just Ali)
    and drafts proposed-next-step comments on their top AI-tier todos
    in allowlisted buckets (default just Ali Personal 7463955)."""
    from . import autopickup_worker
    try:
        autopickup_worker.scan_all_users()
    except Exception:
        logger.warning("ops_autopickup: scan_all_users threw", exc_info=True)


def _purge_all_users() -> None:
    """M6 (2026-06-09 audit) cron entrypoint.

    Walks every user with a vault token; runs purge.purge_stale_active_rows
    for each one whose last purge is due (default: once per 24h per
    user). The cron itself fires hourly so a user who hits their due
    time mid-day isn't waiting until the next day-boundary.
    """
    from execution.products.library import tenancy, vault
    from . import purge

    try:
        users = tenancy.list_users(active_only=True)
    except Exception:
        logger.warning("ops_purge: failed to list users", exc_info=True)
        return

    n_ran = 0
    n_skipped = 0
    n_due_skipped = 0
    n_failed = 0
    for u in users:
        try:
            has_token = any(
                c.tool_name == "basecamp_ai_clone"
                for c in vault.list_for_user(u.user_id, caller_id="ops_purge_cron")
            )
        except Exception:
            has_token = False
        if not has_token:
            n_skipped += 1
            continue
        if not purge.is_purge_due(u.email):
            n_due_skipped += 1
            continue
        try:
            purge.purge_stale_active_rows(u.email)
            n_ran += 1
        except Exception:
            logger.warning("ops_purge: user %s failed", u.email, exc_info=True)
            n_failed += 1

    logger.info(
        "ops_purge cron: ran=%d skipped=%d not-due=%d failed=%d",
        n_ran, n_skipped, n_due_skipped, n_failed,
    )


def _scan_autopickup_approve() -> None:
    """[Auto-Pickup Approve] Phase 1.5 cron entrypoint. Walks the
    autopickup audit log, fetches each ticket's BC comments, classifies
    the next-after-autopickup human reply as approved / rejected /
    ambiguous, and logs the detection. No-op when OPS_AUTOPICKUP_ENABLED
    is false (same flag as the writer worker)."""
    from . import autopickup_approve_worker
    try:
        autopickup_approve_worker.scan_all_users()
    except Exception:
        logger.warning("ops_autopickup_approve: scan_all_users threw",
                                  exc_info=True)


def _run_cb_smoke() -> None:
    """Nightly smoke test — post a known @CB ping, wait, assert reply.
    No-op when bucket/todo env vars are unset (so non-prod environments
    don't run it). See execution/products/ops/cb_smoke.py."""
    from . import cb_smoke
    try:
        cb_smoke.run()
    except Exception:
        logger.warning("ops_cb_smoke: run() threw", exc_info=True)


def start_scheduler() -> None:
    """Add jobs to the background scheduler. Idempotent."""
    global _scheduler
    if _scheduler is not None:
        logger.info("ops sync scheduler already running")
        return
    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        _sync_all_users,
        trigger=IntervalTrigger(minutes=INTERVAL_MINUTES),
        id=JOB_ID,
        name="My Day BC sync (per user with vault token)",
        replace_existing=True,
        next_run_time=None,
    )
    _scheduler.add_job(
        _scan_cb_mentions,
        trigger=IntervalTrigger(minutes=MENTION_INTERVAL_MINUTES),
        id=MENTION_JOB_ID,
        name="CB System @-mention auto-response (per user with vault token)",
        replace_existing=True,
        next_run_time=None,
    )
    _scheduler.add_job(
        _scan_autopickup,
        trigger=IntervalTrigger(minutes=AUTOPICKUP_INTERVAL_MINUTES),
        id=AUTOPICKUP_JOB_ID,
        name="Auto-Pickup Worker Phase 1 (draft-only on allowlisted buckets)",
        replace_existing=True,
        next_run_time=None,
    )
    _scheduler.add_job(
        _scan_autopickup_approve,
        trigger=IntervalTrigger(minutes=APPROVE_INTERVAL_MINUTES),
        id=APPROVE_JOB_ID,
        name="Auto-Pickup Approve Worker (detect human reply on draft comments)",
        replace_existing=True,
        next_run_time=None,
    )
    _scheduler.add_job(
        _purge_all_users,
        trigger=IntervalTrigger(minutes=PURGE_CRON_MINUTES),
        id=PURGE_JOB_ID,
        name="Stale-row purge (per user, gated by purge.is_purge_due)",
        replace_existing=True,
        next_run_time=None,
    )
    # Only register the smoke job when bucket/todo env vars are configured,
    # so dev / preview environments don't pretend to run a smoke test that
    # has no fixture todo to ping. See cb_smoke.is_configured().
    from . import cb_smoke
    if cb_smoke.is_configured():
        _scheduler.add_job(
            _run_cb_smoke,
            trigger=CronTrigger(hour=SMOKE_CRON_HOUR, minute=0,
                                timezone=SMOKE_CRON_TIMEZONE),
            id=SMOKE_JOB_ID,
            name=f"CB @-mention nightly smoke test ({SMOKE_CRON_TIMEZONE} "
                 f"{SMOKE_CRON_HOUR:02d}:00)",
            replace_existing=True,
            next_run_time=None,
        )
    _scheduler.start()
    smoke_status = "enabled" if cb_smoke.is_configured() else "disabled (env unset)"
    logger.info(
        "ops schedulers started: sync every %d min, mentions every %d min, "
        "autopickup every %d min, approve-scan every %d min, "
        "purge cron every %d min, cb_smoke %s",
        INTERVAL_MINUTES, MENTION_INTERVAL_MINUTES,
        AUTOPICKUP_INTERVAL_MINUTES, APPROVE_INTERVAL_MINUTES,
        PURGE_CRON_MINUTES, smoke_status,
    )


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("ops sync scheduler stopped")
