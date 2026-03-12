"""Scheduler for daily skill registry scans.

Uses APScheduler's BackgroundScheduler to run the skill scanner once daily.
Integrates with the FastAPI app lifespan.

Usage:
    from execution.skill_scanner_scheduler import start_scheduler, stop_scheduler

    # In FastAPI lifespan:
    start_scheduler()
    yield
    stop_scheduler()
"""

import asyncio
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _daily_scan_job() -> None:
    """Run the full skill scan in an async context."""
    from execution.skill_scanner import run_full_scan

    logger.info("Starting daily skill registry scan...")
    try:
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(run_full_scan())
        loop.close()
        logger.info(
            "Daily scan complete: %d sources scanned, %d total skills",
            result.get("sources_scanned", 0),
            result.get("total_skills", 0),
        )
    except Exception:
        logger.error("Daily skill scan failed", exc_info=True)


def start_scheduler() -> None:
    """Start the background scheduler with a daily scan job at 3 AM UTC."""
    global _scheduler
    if _scheduler is not None:
        logger.warning("Scheduler already running")
        return

    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        _daily_scan_job,
        trigger=CronTrigger(hour=3, minute=0),
        id="daily_skill_scan",
        name="Daily Skill Registry Scan",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("Skill scanner scheduler started (daily at 03:00 UTC)")


def stop_scheduler() -> None:
    """Gracefully shut down the scheduler."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Skill scanner scheduler stopped")
