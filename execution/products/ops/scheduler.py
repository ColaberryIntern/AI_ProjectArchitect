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
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

INTERVAL_MINUTES = int(os.environ.get("OPS_SYNC_INTERVAL_MINUTES", "5"))
MENTION_INTERVAL_MINUTES = int(os.environ.get("OPS_MENTION_INTERVAL_MINUTES", "10"))
AUTOPICKUP_INTERVAL_MINUTES = int(os.environ.get("OPS_AUTOPICKUP_INTERVAL_MINUTES", "15"))
JOB_ID = "ops_sync_all_users"
MENTION_JOB_ID = "ops_cb_mentions_all_users"
AUTOPICKUP_JOB_ID = "ops_autopickup_all_users"

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
    _scheduler.start()
    logger.info(
        "ops schedulers started: sync every %d min, mentions every %d min, "
        "autopickup every %d min",
        INTERVAL_MINUTES, MENTION_INTERVAL_MINUTES, AUTOPICKUP_INTERVAL_MINUTES,
    )


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("ops sync scheduler stopped")
